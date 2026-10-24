"""End-to-end tests for thumbs up/down and no-repeat, against a REAL Postgres test database.

They are skipped unless you point them at an empty throwaway database:

    set TEST_DATABASE_URL=postgresql://user:password@localhost:5432/moodlist_test      (Windows)
    export TEST_DATABASE_URL=postgresql://user:password@localhost:5432/moodlist_test   (Mac/Linux)
    python -m unittest tests.test_feedback_api -v

Safety: the database name must contain "test", the file must be the only one that imported
main in this run, and the song catalog is a temporary file. Never use your real database URL.
Run this file on its own so nothing else connects first."""
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
SAFE = bool(TEST_URL) and "test" in TEST_URL.rsplit("/", 1)[-1].split("?")[0].lower()

if SAFE and "main" not in sys.modules:
    os.environ["DATABASE_URL"] = TEST_URL
    os.environ["SECRET_KEY"] = "t" * 48
    os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
    os.environ["RATE_LIMIT_PER_MIN"] = "1000"
    os.environ["MOODLIST_DB"] = str(Path(tempfile.mkdtemp()) / "api_test.db")
    os.environ.pop("YOUTUBE_API_KEY", None)


@unittest.skipUnless(SAFE, "set TEST_DATABASE_URL to a throwaway database whose name contains 'test'")
class FeedbackApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import auth
        import catalog
        import database
        import feedback
        import main
        import models

        wanted = TEST_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
        if main.engine.url.render_as_string(hide_password=False) != wanted:
            raise unittest.SkipTest("main was imported with a different database; run this file alone")
        if not str(catalog.DB_PATH).startswith(tempfile.gettempdir()):
            raise unittest.SkipTest("the song catalog is not a temporary file")

        cls.catalog, cls.main, cls.models, cls.auth = catalog, main, models, auth
        cls.database, cls.feedback_mod = database, feedback
        cls.client_cm = TestClient(main.app)
        cls.client = cls.client_cm.__enter__()  # runs startup: creates tables, loads the seed

        # Pretend every song has a matched video so playlists behave as in production.
        for n, song in enumerate(catalog.pending()):
            catalog.mark_resolved(song["id"], f"tst{n:08d}", 5_000_000)

        cls.user_ids = []
        cls.alice = cls._make_user("alice")
        cls.bob = cls._make_user("bob")

    @classmethod
    def _make_user(cls, name):
        with cls.database.SessionLocal() as db:
            user = cls.models.User(
                email=f"{name}-{uuid.uuid4().hex[:8]}@example.test",
                username=f"{name}-{uuid.uuid4().hex[:8]}",
                hashed_password=None,
                is_verified=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            cls.user_ids.append(user.id)
            token = cls.auth.create_access_token({"sub": str(user.id)})
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    def tearDownClass(cls):
        with cls.database.SessionLocal() as db:
            for uid in cls.user_ids:
                user = db.get(cls.models.User, uid)
                if user:
                    db.delete(user)  # history and feedback go with it
            db.commit()
        cls.client_cm.__exit__(None, None, None)

    # ---------------------------------------------------------------- helpers
    def playlist(self, headers, mood="happy"):
        res = self.client.get("/api/playlist", params={"mood": mood, "size": 10}, headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["tracks"]

    def feedback(self, headers, video_id, value):
        return self.client.post("/api/feedback", json={"video_id": video_id, "value": value}, headers=headers)

    # ------------------------------------------------------------------ tests
    def test_requires_sign_in(self):
        self.assertEqual(self.client.get("/api/playlist", params={"mood": "happy"}).status_code, 401)
        self.assertEqual(self.client.post("/api/feedback", json={"video_id": "tst00000001", "value": 1}).status_code, 401)

    def test_invalid_input_is_rejected(self):
        self.assertEqual(self.feedback(self.alice, "tst00000001", 5).status_code, 422)
        self.assertEqual(self.feedback(self.alice, "!!", 1).status_code, 422)
        self.assertEqual(self.feedback(self.alice, "zzzzzzzzzzz", 1).status_code, 404)
        self.assertEqual(self.client.delete("/api/feedback", params={"value": 7}, headers=self.alice).status_code, 422)

    def test_playlists_do_not_repeat_for_one_person(self):
        first = self.playlist(self.bob)
        self.assertTrue(all(t["video_id"] and t["feedback"] == 0 for t in first))
        second = self.playlist(self.bob)
        self.assertFalse({t["video_id"] for t in first} & {t["video_id"] for t in second})

    def test_dislike_removes_a_song_for_good_and_can_be_cleared(self):
        tracks = self.playlist(self.alice)
        victim = tracks[0]["video_id"]
        self.assertEqual(self.feedback(self.alice, victim, -1).json(), {"video_id": victim, "value": -1})
        self.assertEqual(self.feedback(self.alice, victim, -1).status_code, 200)  # tapping twice is harmless
        for _ in range(25):
            self.assertNotIn(victim, {t["video_id"] for t in self.playlist(self.alice)})

        # someone else is not affected: they can still be given it
        seen = set()
        for _ in range(60):
            seen |= {t["video_id"] for t in self.playlist(self.bob)}
        self.assertIn(victim, seen)

        self.assertEqual(self.client.delete("/api/feedback", params={"value": -1}, headers=self.alice).json(), {"removed": 1})
        with self.database.SessionLocal() as db:
            left = (
                db.query(self.feedback_mod.SongFeedback)
                .filter(self.feedback_mod.SongFeedback.user_id == self.user_ids[0])
                .count()
            )
            self.assertEqual(left, 0)

    def test_like_is_saved_taken_back_and_shown(self):
        tracks = self.playlist(self.alice, mood="calm")
        song = tracks[0]["video_id"]
        self.assertEqual(self.feedback(self.alice, song, 1).status_code, 200)
        with self.database.SessionLocal() as db:
            user = db.query(self.models.User).filter(self.models.User.id == self.user_ids[0]).one()
            liked, blocked = self.feedback_mod.feedback_sets(db, user)
            self.assertEqual((len(liked), len(blocked)), (1, 0))
        self.assertEqual(self.feedback(self.alice, song, 0).status_code, 200)
        with self.database.SessionLocal() as db:
            user = db.query(self.models.User).filter(self.models.User.id == self.user_ids[0]).one()
            self.assertEqual(self.feedback_mod.feedback_sets(db, user), (set(), set()))

    def test_history_is_recorded(self):
        self.playlist(self.bob, mood="energetic")
        res = self.client.get("/users/me/history", headers=self.bob)
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(len(res.json()), 1)


if __name__ == "__main__":
    unittest.main()
