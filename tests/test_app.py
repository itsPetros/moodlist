"""Run with:  python -m unittest discover tests -v
Uses a temporary database and a fake YouTube API, so no key or internet is needed."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MOODLIST_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["RATE_LIMIT_PER_MIN"] = "1000"
os.environ.pop("YOUTUBE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

import catalog  # noqa: E402
import main  # noqa: E402
import options  # noqa: E402
import youtube  # noqa: E402


class SeedTests(unittest.TestCase):
    def test_seed_is_valid(self):
        rows, errors = catalog.parse_seed()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(rows), 200)
        keys = [(r[0], r[1]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)), "duplicate artist/title in seed")

    def test_every_mood_has_enough_songs(self):
        rows, _ = catalog.parse_seed()
        for mood in options.ids(options.MOODS):
            n = sum(1 for r in rows if mood in r[3].split(","))
            self.assertGreaterEqual(n, 25, f"mood {mood} has only {n} songs")

    def test_every_genre_has_songs_for_every_mood_or_gets_padded(self):
        # Thin genre/mood combinations are allowed (the picker pads them), but not empty genres.
        rows, _ = catalog.parse_seed()
        for genre in options.ids(options.GENRES):
            if genre == "any":
                continue
            self.assertGreaterEqual(sum(1 for r in rows if r[2] == genre), 10, genre)


class PickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog.sync_seed()

    def test_returns_requested_size_without_duplicates_or_artist_spam(self):
        for mood in options.ids(options.MOODS):
            for genre in ("any", "rock", "jazz"):
                songs, _ = catalog.pick_songs(mood, "night", "rainy", genre, 12)
                self.assertEqual(len(songs), 12, (mood, genre))
                self.assertEqual(len({s["id"] for s in songs}), 12)
                artists = [catalog._first_artist(s["artist"]) for s in songs]
                self.assertLessEqual(max(artists.count(a) for a in set(artists)), catalog.MAX_PER_ARTIST)
                self.assertTrue(all(mood in s["moods"].split(",") for s in songs))

    def test_genre_is_respected_when_enough_songs_exist(self):
        songs, note = catalog.pick_songs("energetic", None, None, "rock", 12)
        self.assertTrue(all(s["genre"] == "rock" for s in songs))
        self.assertIsNone(note)

    def test_thin_genre_is_padded_and_explained(self):
        songs, note = catalog.pick_songs("sad", None, None, "latin", 12)
        self.assertEqual(len(songs), 12)
        self.assertIn("latin", [s["genre"] for s in songs])
        self.assertIsNotNone(note)

    def test_time_and_weather_shift_the_results(self):
        def rainy_share(weather):
            hits = total = 0
            for _ in range(60):
                for s in catalog.pick_songs("sad", "night", weather, "any", 12)[0]:
                    total += 1
                    hits += "rainy" in s["weather"].split(",")
            return hits / total

        self.assertGreater(rainy_share("rainy"), rainy_share("sunny"))

    def test_playlists_are_random(self):
        a = [s["id"] for s in catalog.pick_songs("happy", "morning", "sunny", "any", 12)[0]]
        b = [s["id"] for s in catalog.pick_songs("happy", "morning", "sunny", "any", 12)[0]]
        c = [s["id"] for s in catalog.pick_songs("happy", "morning", "sunny", "any", 12)[0]]
        self.assertFalse(a == b == c)


def video(vid, title, channel, views, embeddable=True, privacy="public", blocked=None, allowed=None, age=False):
    details = {}
    if blocked is not None:
        details["regionRestriction"] = {"blocked": blocked}
    if allowed is not None:
        details["regionRestriction"] = {"allowed": allowed}
    if age:
        details["contentRating"] = {"ytRating": "ytAgeRestricted"}
    return {
        "id": vid,
        "snippet": {"title": title, "channelTitle": channel},
        "statistics": {"viewCount": str(views)},
        "status": {"embeddable": embeddable, "privacyStatus": privacy, "uploadStatus": "processed"},
        "contentDetails": details,
    }


class MatchingTests(unittest.TestCase):
    song = {"id": 1, "artist": "Pharrell Williams", "title": "Happy"}

    def test_accepts_official_video(self):
        v = video("a", "Pharrell Williams - Happy (Official Music Video)", "PharrellWilliamsVEVO", 1_500_000_000)
        self.assertTrue(youtube.looks_right(self.song, v))

    def test_rejects_wrong_artist_and_bad_versions(self):
        self.assertFalse(youtube.looks_right(self.song, video("b", "Happy - Kids Choir", "SchoolTV", 5_000_000)))
        self.assertFalse(youtube.looks_right(self.song, video("c", "Pharrell Williams - Happy (Karaoke)", "KaraokeCo", 5_000_000)))
        self.assertFalse(youtube.looks_right(self.song, video("d", "Pharrell Williams - Happy (Live)", "Pharrell", 5_000_000)))

    def test_accents_apostrophes_and_features(self):
        song = {"artist": "Beyonce", "title": "Halo"}
        self.assertTrue(youtube.looks_right(song, video("e", "Beyoncé - Halo", "Beyoncé", 2_000_000)))
        song = {"artist": "Guns N' Roses", "title": "Sweet Child O' Mine"}
        self.assertTrue(youtube.looks_right(song, video("f", "Guns N' Roses - Sweet Child O' Mine", "Guns N' Roses", 2_000_000)))
        song = {"artist": "Claude Debussy", "title": "Clair de Lune"}
        self.assertTrue(youtube.looks_right(song, video("g", "Debussy - Clair de Lune", "Classical", 2_000_000)))

    def test_word_boundaries_do_not_trigger_bad_words(self):
        song = {"artist": "Bon Jovi", "title": "Livin' on a Prayer"}
        self.assertTrue(youtube.looks_right(song, video("h", "Bon Jovi - Livin' On A Prayer (Official Video)", "Bon Jovi", 9_000_000)))

    def test_choose_best_applies_view_embed_and_privacy_rules(self):
        vids = [
            video("low", "Pharrell Williams - Happy", "Pharrell", 500_000),
            video("blocked", "Pharrell Williams - Happy", "Pharrell", 900_000_000, embeddable=False),
            video("private", "Pharrell Williams - Happy", "Pharrell", 900_000_000, privacy="unlisted"),
            video("small", "Pharrell Williams - Happy (Official Audio)", "Pharrell", 2_000_000),
            video("best", "Pharrell Williams - Happy (Official Music Video)", "Pharrell", 1_500_000_000),
        ]
        self.assertEqual(youtube.choose_best(self.song, vids)["video_id"], "best")
        self.assertIsNone(youtube.choose_best(self.song, vids[:3]))


class PlayabilityTests(unittest.TestCase):
    def setUp(self):
        os.environ["YOUTUBE_REGION"] = "AM,US"

    def tearDown(self):
        os.environ.pop("YOUTUBE_REGION", None)

    def issue(self, **kw):
        return youtube.playable_issue(video("x", "A - B", "C", 5_000_000, **kw))

    def test_plain_video_is_fine(self):
        self.assertIsNone(self.issue())

    def test_blocked_in_one_of_our_regions(self):
        self.assertEqual(self.issue(blocked=["AM"]), "blocked in AM")
        self.assertEqual(self.issue(blocked=["DE", "US"]), "blocked in US")
        self.assertIsNone(self.issue(blocked=["DE", "RU"]))

    def test_allowed_list_must_include_all_our_regions(self):
        self.assertEqual(self.issue(allowed=["US", "GB"]), "not available in AM")
        self.assertIsNone(self.issue(allowed=["US", "AM", "GB"]))
        self.assertIsNotNone(self.issue(allowed=[]))  # empty allow-list = blocked everywhere

    def test_age_restricted_private_and_non_embeddable(self):
        self.assertEqual(self.issue(age=True), "age restricted")
        self.assertEqual(self.issue(privacy="unlisted"), "not public")
        self.assertEqual(self.issue(embeddable=False), "embedding disabled")

    def test_region_default_is_us(self):
        os.environ.pop("YOUTUBE_REGION", None)
        self.assertEqual(youtube.regions(), ["US"])

    def test_choose_best_skips_blocked_and_excluded_videos(self):
        song = {"artist": "Pharrell Williams", "title": "Happy"}
        vids = [
            video("blocked", "Pharrell Williams - Happy (Official Music Video)", "Pharrell", 900_000_000, blocked=["AM"]),
            video("bad", "Pharrell Williams - Happy (Official Audio)", "Pharrell", 800_000_000),
            video("good", "Pharrell Williams - Happy (Lyric Video)", "Pharrell", 5_000_000),
        ]
        self.assertEqual(youtube.choose_best(song, vids, exclude={"bad"})["video_id"], "good")

    def test_recheck_finds_removed_blocked_and_fine_videos(self):
        os.environ["YOUTUBE_API_KEY"] = "fake"
        orig = youtube.api_get
        songs = [
            {"id": 1, "artist": "A", "title": "One", "video_id": "fine0000001"},
            {"id": 2, "artist": "A", "title": "Two", "video_id": "gone0000002"},
            {"id": 3, "artist": "A", "title": "Three", "video_id": "blkd0000003"},
        ]

        def fake(path, params):
            self.assertEqual(path, "videos")
            self.assertIn("contentDetails", params["part"])
            return {"items": [
                video("fine0000001", "A - One", "A", 7_000_000),
                video("blkd0000003", "A - Three", "A", 7_000_000, blocked=["AM"]),
            ]}

        youtube.api_get = fake
        try:
            ok, broken = youtube.recheck_videos(songs)
        finally:
            youtube.api_get = orig
            os.environ.pop("YOUTUBE_API_KEY", None)
        self.assertEqual([(s["id"], v) for s, v in ok], [(1, 7_000_000)])
        self.assertEqual([(s["id"], r) for s, r in broken], [(2, "removed or private"), (3, "blocked in AM")])


class ResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog.sync_seed()

    def setUp(self):
        self._orig = youtube.api_get
        os.environ["YOUTUBE_API_KEY"] = "fake"
        os.environ["DAILY_UNIT_BUDGET"] = "999999"

    def tearDown(self):
        youtube.api_get = self._orig
        os.environ.pop("YOUTUBE_API_KEY", None)

    def test_resolve_stores_best_video_and_counts_quota(self):
        song = catalog.pending(1)[0]
        before = catalog.usage_today()

        def fake(path, params):
            if path == "search":
                return {"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}]}
            title = f"{youtube.first_artist(song['artist'])} - {song['title']} (Official Video)"
            return {"items": [video("v1", title, "Chan", 3_000_000), video("v2", title, "Chan", 9_000_000)]}

        youtube.api_get = fake
        status, info = youtube.resolve_song(song)
        self.assertEqual((status, info["video_id"]), ("ok", "v2"))
        self.assertEqual(catalog.usage_today() - before, 101)
        with catalog.db() as con:
            row = con.execute("SELECT * FROM songs WHERE id = ?", (song["id"],)).fetchone()
        self.assertEqual((row["status"], row["video_id"], row["views"]), ("ok", "v2", 9_000_000))

    def test_resolver_uses_syndicated_filter_and_skips_known_bad_videos(self):
        song = dict(catalog.pending(1)[0])
        song["bad_ids"] = "v2"  # v2 was reported as unplayable before
        seen = {}

        def fake(path, params):
            if path == "search":
                seen.update(params)
                return {"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}]}
            title = f"{youtube.first_artist(song['artist'])} - {song['title']} (Official Video)"
            return {"items": [video("v1", title, "Chan", 3_000_000), video("v2", title, "Chan", 9_000_000)]}

        youtube.api_get = fake
        status, info = youtube.resolve_song(song)
        self.assertEqual((status, info["video_id"]), ("ok", "v1"))
        self.assertEqual(seen["videoSyndicated"], "true")
        self.assertEqual(seen["videoEmbeddable"], "true")
        self.assertIn("regionCode", seen)

    def test_low_view_results_mark_song_failed_and_exclude_it(self):
        song = catalog.pending(1)[0]

        def fake(path, params):
            if path == "search":
                return {"items": [{"id": {"videoId": "x"}}]}
            title = f"{youtube.first_artist(song['artist'])} - {song['title']}"
            return {"items": [video("x", title, "Chan", 1_000)]}

        youtube.api_get = fake
        self.assertEqual(youtube.resolve_song(song)[0], "failed")
        picked = [s["id"] for m in options.ids(options.MOODS) for s in catalog.pick_songs(m, size=15)[0]]
        self.assertNotIn(song["id"], picked)

    def test_quota_error_stops_resolving_for_the_day(self):
        def fake(path, params):
            raise youtube.YouTubeError("quota", "quotaExceeded")

        youtube.api_get = fake
        os.environ["DAILY_UNIT_BUDGET"] = "5000"
        song = catalog.pending(1)[0]
        self.assertEqual(youtube.resolve_song(song)[0], "skipped")
        youtube.api_get = self._orig
        self.assertEqual(youtube.resolve_song(song)[0], "skipped")  # budget now exhausted, no HTTP call
        self.assertGreaterEqual(catalog.usage_today(), 5000)

    def test_no_key_means_skipped(self):
        os.environ.pop("YOUTUBE_API_KEY", None)
        self.assertEqual(youtube.resolve_song(catalog.pending(1)[0])[0], "skipped")


class SavedVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog.sync_seed()

    def make_ok(self, offset, video_id):
        """Give the song at this position a clean row with a saved video.
        Each test uses its own position, so the tests don't depend on running in a given order."""
        with catalog.db() as con:
            sid = con.execute("SELECT id FROM songs ORDER BY id LIMIT 1 OFFSET ?", (offset,)).fetchone()["id"]
            con.execute(
                "UPDATE songs SET status='pending', video_id=NULL, views=NULL, bad_ids='', reports=0 WHERE id = ?",
                (sid,),
            )
        catalog.mark_resolved(sid, video_id, 5_000_000)
        return sid

    def row(self, song_id):
        with catalog.db() as con:
            return dict(con.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone())

    def test_mark_bad_forgets_the_video_and_remembers_it_as_bad(self):
        sid = self.make_ok(150, "rep00000001")
        catalog.mark_bad(sid)
        r = self.row(sid)
        self.assertEqual((r["status"], r["video_id"], r["bad_ids"]), ("pending", None, "rep00000001"))

    def test_mark_bad_accumulates_bad_ids(self):
        sid = self.make_ok(151, "bad00000001")
        catalog.mark_bad(sid)
        catalog.mark_resolved(sid, "bad00000002", 1_000_000)
        catalog.mark_bad(sid)
        self.assertEqual(self.row(sid)["bad_ids"], "bad00000001,bad00000002")

    def test_old_database_is_upgraded_without_losing_videos(self):
        import sqlite3
        old = Path(tempfile.mkdtemp()) / "old.db"
        con = sqlite3.connect(old)
        con.executescript("""
            CREATE TABLE songs (id INTEGER PRIMARY KEY, artist TEXT NOT NULL, title TEXT NOT NULL,
              genre TEXT NOT NULL, moods TEXT NOT NULL, times TEXT NOT NULL, weather TEXT NOT NULL,
              video_id TEXT, views INTEGER, status TEXT NOT NULL DEFAULT 'pending', UNIQUE (artist, title));
            INSERT INTO songs (artist, title, genre, moods, times, weather, video_id, views, status)
              VALUES ('Pharrell Williams', 'Happy', 'pop', 'happy', '*', '*', 'keepme00001', 123456789, 'ok');
        """)
        con.commit()
        con.close()
        saved = catalog.DB_PATH
        catalog.DB_PATH = old
        try:
            catalog.sync_seed()
            with catalog.db() as c:
                row = dict(c.execute("SELECT * FROM songs WHERE title = 'Happy'").fetchone())
        finally:
            catalog.DB_PATH = saved
        self.assertEqual((row["video_id"], row["status"], row["bad_ids"], row["reports"]), ("keepme00001", "ok", "", 0))


class VideoFirstTests(unittest.TestCase):
    """Songs with a matched video are chosen before songs without one, so they all fit
    into the 'Open as YouTube playlist' link. Uses its own throwaway database."""

    def setUp(self):
        self.saved = catalog.DB_PATH
        catalog.DB_PATH = Path(tempfile.mkdtemp()) / "tier.db"
        catalog.sync_seed()

    def tearDown(self):
        catalog.DB_PATH = self.saved

    def happy_ids(self):
        with catalog.db() as con:
            return [r["id"] for r in con.execute("SELECT id, moods FROM songs ORDER BY id")
                    if "happy" in r["moods"].split(",")]

    def set_ok(self, ids):
        for sid in ids:
            catalog.mark_resolved(sid, f"vid{sid:08d}", 5_000_000)

    def test_only_matched_songs_are_used_when_there_are_enough(self):
        self.set_ok(self.happy_ids()[:40])
        for _ in range(30):
            songs, _ = catalog.pick_songs("happy", "morning", "sunny", "any", 12)
            self.assertEqual(len(songs), 12)
            self.assertTrue(all(s["status"] == "ok" for s in songs))

    def test_all_matched_songs_are_included_when_there_are_few(self):
        ok = self.happy_ids()[:5]
        self.set_ok(ok)
        for _ in range(30):
            songs, _ = catalog.pick_songs("happy", "morning", "sunny", "any", 12)
            self.assertEqual(len(songs), 12)
            self.assertTrue(set(ok) <= {s["id"] for s in songs})

    def test_only_matched_mode_never_returns_unmatched_songs(self):
        ok = self.happy_ids()[:5]
        self.set_ok(ok)
        songs, _ = catalog.pick_songs("happy", None, None, "any", 12, only_matched=True)
        self.assertEqual({s["id"] for s in songs}, set(ok))  # shorter list, but every song has a video

    def test_lookup_order_spreads_across_genres(self):
        first = catalog.pending(18)
        self.assertEqual(len(first), 18)
        self.assertGreaterEqual(len({s["genre"] for s in first}), 9)  # one of every genre, twice
        self.assertEqual(len(catalog.pending()), len({s["id"] for s in catalog.pending()}))

    def test_chosen_genre_still_beats_a_matched_song_of_another_genre(self):
        pop_happy = [s for s in catalog.pick_songs("happy", None, None, "pop", 15)[0]]
        self.set_ok([pop_happy[0]["id"]])
        songs, note = catalog.pick_songs("happy", None, None, "jazz", 10)
        jazz_count = sum(1 for s in songs if s["genre"] == "jazz")
        self.assertEqual([s["genre"] for s in songs[:jazz_count]], ["jazz"] * jazz_count)
        self.assertGreaterEqual(jazz_count, 3)
        self.assertIsNotNone(note)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(main.app)
        cls.client = cls.ctx.__enter__()  # runs startup (seed sync)
        # make a few songs playable so the playlist link appears
        with catalog.db() as con:
            con.execute("UPDATE songs SET status='ok', video_id='abcdefghijk', views=5000000 WHERE id % 3 = 0")

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_pages_and_assets(self):
        for path in ("/", "/manifest.webmanifest", "/sw.js", "/static/app.js", "/static/style.css",
                     "/static/icons/icon-192.png", "/static/icons/icon-512.png",
                     "/static/icons/apple-touch-icon.png", "/healthz"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        html = self.client.get("/").text
        self.assertIn("viewport-fit=cover", html)
        self.assertEqual(self.client.get("/sw.js").headers["cache-control"], "no-cache")

    def test_options(self):
        data = self.client.get("/api/options").json()
        self.assertEqual({"moods", "times", "weathers", "genres"}, set(data))

    def test_playlist(self):
        r = self.client.get("/api/playlist", params={"mood": "sad", "time": "night", "weather": "rainy", "genre": "any"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(10 <= len(body["tracks"]) <= 15)
        self.assertEqual(body["title"], "Sad · Night · Rainy")
        for t in body["tracks"]:
            self.assertTrue(t["search_url"].startswith("https://www.youtube.com/results?search_query="))
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertTrue(body["youtube_playlist_url"].startswith("https://www.youtube.com/watch_videos?video_ids="))

    def test_size_is_clamped_and_optional_fields_work(self):
        self.assertEqual(len(self.client.get("/api/playlist", params={"mood": "happy", "size": 500}).json()["tracks"]), 15)
        self.assertEqual(len(self.client.get("/api/playlist", params={"mood": "happy", "size": 1}).json()["tracks"]), 10)

    def test_referrer_policy_header(self):
        for path in ("/", "/api/options", "/static/app.js"):
            self.assertEqual(self.client.get(path).headers["referrer-policy"], "strict-origin-when-cross-origin", path)
        self.assertIn('name="referrer"', self.client.get("/").text)

    def test_removed_features_are_gone(self):
        self.assertEqual(self.client.post("/api/report", params={"video_id": "abcdefghijk"}).status_code, 404)
        html = self.client.get("/").text
        self.assertNotIn("player", html.lower().replace("playlist", ""))
        self.assertNotIn("youtube.com/iframe_api", self.client.get("/static/app.js").text)

    def test_validation(self):
        self.assertEqual(self.client.get("/api/playlist", params={"mood": "grumpy"}).status_code, 400)
        self.assertEqual(self.client.get("/api/playlist", params={"mood": "sad", "genre": "polka"}).status_code, 400)
        self.assertEqual(self.client.get("/api/playlist", params={"mood": "sad", "time": "noon"}).status_code, 400)
        self.assertEqual(self.client.get("/api/playlist").status_code, 422)

    def test_every_song_in_a_playlist_is_in_the_youtube_link(self):
        for mood in options.ids(options.MOODS):
            body = self.client.get("/api/playlist", params={"mood": mood}).json()
            ids = [t["video_id"] for t in body["tracks"]]
            self.assertTrue(ids and all(ids), mood)  # every song has a matched video
            self.assertEqual(body["total"], len(ids))
            self.assertEqual(body["playlist_count"], len(ids))
            self.assertEqual(body["youtube_playlist_url"].split("video_ids=")[1].split(","), ids)

    def test_few_matched_songs_give_a_shorter_playlist_with_a_note(self):
        saved = catalog.DB_PATH
        catalog.DB_PATH = Path(tempfile.mkdtemp()) / "few.db"
        catalog.sync_seed()
        try:
            chosen, artists = [], set()
            with catalog.db() as con:
                for r in con.execute("SELECT id, artist, moods FROM songs ORDER BY id"):
                    if "sad" in r["moods"].split(",") and r["artist"] not in artists and len(chosen) < 6:
                        artists.add(r["artist"])
                        chosen.append(r["id"])
            for sid in chosen:
                catalog.mark_resolved(sid, f"few{sid:08d}", 5_000_000)
            body = self.client.get("/api/playlist", params={"mood": "sad"}).json()
        finally:
            catalog.DB_PATH = saved
        self.assertEqual((len(body["tracks"]), body["playlist_count"]), (6, 6))
        self.assertTrue(all(t["video_id"] for t in body["tracks"]))
        self.assertIn("Only 6 songs", body["note"])

    def test_without_any_matched_song_it_falls_back_to_search_links(self):
        saved = catalog.DB_PATH
        catalog.DB_PATH = Path(tempfile.mkdtemp()) / "none.db"
        catalog.sync_seed()
        try:
            body = self.client.get("/api/playlist", params={"mood": "sad"}).json()
        finally:
            catalog.DB_PATH = saved
        self.assertEqual(body["playlist_count"], 0)
        self.assertIsNone(body["youtube_playlist_url"])
        self.assertIn("build_catalog.py", body["note"])
        self.assertTrue(all(t["video_id"] is None and t["search_url"] for t in body["tracks"]))

    def test_rate_limit(self):
        main._hits.clear()
        os.environ["RATE_LIMIT_PER_MIN"] = "3"
        try:
            codes = [self.client.get("/api/playlist", params={"mood": "calm"}).status_code for _ in range(5)]
        finally:
            os.environ["RATE_LIMIT_PER_MIN"] = "1000"
            main._hits.clear()
        self.assertEqual(codes, [200, 200, 200, 429, 429])


if __name__ == "__main__":
    unittest.main()
