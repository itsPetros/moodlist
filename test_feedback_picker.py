"""Tests for the "don't repeat / dislike / like" picking. They need no Postgres.
Run with:  python -m unittest tests.test_feedback_picker -v   (from the project folder)"""
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MOODLIST_DB", str(Path(tempfile.mkdtemp()) / "feedback_picker.db"))

import catalog  # noqa: E402
import options  # noqa: E402


def keys(songs):
    return {catalog.song_key(s["artist"], s["title"]) for s in songs}


class PickerFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog.sync_seed()

    def test_song_key_ignores_case_and_spaces(self):
        self.assertEqual(catalog.song_key(" Adele ", "Hello"), catalog.song_key("adele", " hello"))

    def test_blocked_songs_are_never_returned(self):
        rng = random.Random(1)
        first, _ = catalog.pick_songs("happy", size=12, rng=rng)
        blocked = keys(first)
        for _ in range(40):
            songs, _ = catalog.pick_songs("happy", size=12, rng=rng, blocked=blocked)
            self.assertFalse(keys(songs) & blocked)

    def test_recent_songs_are_skipped_when_there_are_enough_others(self):
        rng = random.Random(2)
        for mood in options.ids(options.MOODS):
            first, _ = catalog.pick_songs(mood, size=6, rng=rng)
            for _ in range(15):
                songs, _ = catalog.pick_songs(mood, size=12, rng=rng, avoid=keys(first))
                self.assertEqual(len(songs), 12, mood)
                self.assertFalse(keys(songs) & keys(first), mood)

    def test_recent_songs_are_used_as_a_fallback_not_a_dead_end(self):
        rng = random.Random(3)
        everything = {
            catalog.song_key(r["artist"], r["title"])
            for r in catalog.pick_songs("sad", size=500, rng=rng)[0]
        }
        songs, _ = catalog.pick_songs("sad", size=12, rng=rng, avoid=everything)
        self.assertEqual(len(songs), 12)

    def test_liked_songs_come_up_more_often(self):
        rng = random.Random(4)
        base, _ = catalog.pick_songs("happy", size=12, rng=rng)
        favourite = {catalog.song_key(base[0]["artist"], base[0]["title"])}
        runs = 400
        plain = sum(bool(keys(catalog.pick_songs("happy", size=12, rng=rng)[0]) & favourite) for _ in range(runs))
        boosted = sum(
            bool(keys(catalog.pick_songs("happy", size=12, rng=rng, liked=favourite)[0]) & favourite)
            for _ in range(runs)
        )
        self.assertGreater(boosted, plain + runs * 0.05)

    def test_results_carry_no_internal_fields(self):
        songs, _ = catalog.pick_songs("happy", size=5)
        self.assertTrue(all("_key" not in s for s in songs))


class FindSongTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog.sync_seed()

    def test_finds_current_and_replaced_videos(self):
        song = catalog.pending(limit=1)[0]
        catalog.mark_resolved(song["id"], "abcDEF_1234", 5_000_000)
        found = catalog.find_song_by_video("abcDEF_1234")
        self.assertEqual(found, {"artist": song["artist"], "title": song["title"]})
        catalog.mark_bad(song["id"])  # the video is now remembered as a bad one
        self.assertEqual(catalog.find_song_by_video("abcDEF_1234"), found)

    def test_unknown_or_empty_ids(self):
        self.assertIsNone(catalog.find_song_by_video("zzzzzzzzzzz"))
        self.assertIsNone(catalog.find_song_by_video(""))
        self.assertIsNone(catalog.find_song_by_video("abcDEF_12__"))  # "_" must not act as a wildcard


if __name__ == "__main__":
    unittest.main()
