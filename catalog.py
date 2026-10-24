"""Song catalog: seed CSV -> SQLite, plus the playlist picker.

The catalog is the heart of the app. Every song has tags (genre, moods, times of
day, weather). Picking a playlist is just filtering + weighted random choice, so
it costs no API calls and works for any number of users.
YouTube video ids are looked up once (see youtube.py) and then stored here.
"""
import os
import random
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import options

BASE = Path(__file__).resolve().parent
SEED_PATH = Path(os.environ.get("MOODLIST_SEED", BASE / "data" / "seed_songs.csv"))
DB_PATH = Path(os.environ.get("MOODLIST_DB", BASE / "data" / "moodlist.db"))

ANY = "*"
MAX_PER_ARTIST = 2
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
  id INTEGER PRIMARY KEY,
  artist TEXT NOT NULL,
  title TEXT NOT NULL,
  genre TEXT NOT NULL,
  moods TEXT NOT NULL,
  times TEXT NOT NULL,
  weather TEXT NOT NULL,
  video_id TEXT,
  views INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | failed
  bad_ids TEXT NOT NULL DEFAULT '',         -- video ids that turned out unplayable
  reports INTEGER NOT NULL DEFAULT 0,       -- how many visitors reported the current video
  UNIQUE (artist, title)
);
CREATE TABLE IF NOT EXISTS usage (
  day TEXT PRIMARY KEY,
  units INTEGER NOT NULL DEFAULT 0
);
"""


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------- seed

def _tags(value, allowed, where, errors):
    if value == ANY:
        return ANY
    parts = [p.strip() for p in value.split(",") if p.strip()]
    bad = [p for p in parts if p not in allowed]
    if bad or not parts:
        errors.append(f"{where}: unknown or empty tag(s) {bad or value!r}")
    return ",".join(parts)


def parse_seed(path=SEED_PATH):
    """Read the seed CSV (pipe separated). Returns (rows, errors)."""
    moods = set(options.ids(options.MOODS))
    times = set(options.ids(options.TIMES))
    weathers = set(options.ids(options.WEATHERS))
    genres = set(options.ids(options.GENRES)) - {"any"}
    rows, errors = [], []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) != 6:
                errors.append(f"line {n}: expected 6 fields, got {len(parts)}")
                continue
            artist, title, genre, m, t, w = parts
            where = f"line {n} ({artist} - {title})"
            if genre not in genres:
                errors.append(f"{where}: unknown genre {genre!r}")
            if m == ANY:
                errors.append(f"{where}: moods cannot be '*'")
            rows.append(
                (
                    artist,
                    title,
                    genre,
                    _tags(m, moods, where, errors),
                    _tags(t, times, where, errors),
                    _tags(w, weathers, where, errors),
                )
            )
    return rows, errors


def sync_seed():
    """Create tables and make the songs table match the seed file.
    Video ids already found are kept."""
    rows, errors = parse_seed()
    if errors:
        raise ValueError("Problems in seed_songs.csv:\n" + "\n".join(errors))
    with _write_lock, db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        # Upgrade databases created by older versions (keeps the videos already found).
        have = {r["name"] for r in con.execute("PRAGMA table_info(songs)")}
        if "bad_ids" not in have:
            con.execute("ALTER TABLE songs ADD COLUMN bad_ids TEXT NOT NULL DEFAULT ''")
        if "reports" not in have:
            con.execute("ALTER TABLE songs ADD COLUMN reports INTEGER NOT NULL DEFAULT 0")
        keep = set()
        for artist, title, genre, m, t, w in rows:
            con.execute(
                """INSERT INTO songs (artist, title, genre, moods, times, weather)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (artist, title) DO UPDATE SET
                     genre = excluded.genre, moods = excluded.moods,
                     times = excluded.times, weather = excluded.weather""",
                (artist, title, genre, m, t, w),
            )
            keep.add((artist, title))
        for r in con.execute("SELECT id, artist, title FROM songs").fetchall():
            if (r["artist"], r["title"]) not in keep:
                con.execute("DELETE FROM songs WHERE id = ?", (r["id"],))
    return len(keep)


# ------------------------------------------------------------------------- picking

def _has(tags, value):
    return tags == ANY or value in tags.split(",")


def _weight(song, time, weather):
    w = 1.0
    for chosen, tags in ((time, song["times"]), (weather, song["weather"])):
        if not chosen:
            continue
        if tags == ANY:
            w += 1.0
        elif chosen in tags.split(","):
            w += 2.0
    return w


def _first_artist(artist):
    return artist.split(" ft. ")[0].strip().lower()


def song_key(artist, title):
    """A stable identity for a song that does not depend on which YouTube video is attached
    to it, so feedback and "already played" survive a video being replaced."""
    return f"{artist.strip().lower()}|{title.strip().lower()}"


# A song the person liked is this many times likelier to come up (it still has to fit the mood).
LIKED_BOOST = 2.5


def pick_songs(mood, time=None, weather=None, genre="any", size=12, rng=None, only_matched=False,
               avoid=None, liked=None, blocked=None):
    """Return (songs, note). Weighted random, max 2 songs per artist.
    With only_matched=True only songs that already have a YouTube video are used, so every
    song returned can go into the YouTube playlist link (the list may then be shorter).

    Optional personalisation, all sets of song_key(...) values:
      blocked  songs the person disliked. Never returned.
      avoid    songs they were given recently. Used only when there are not enough other songs.
      liked    songs they liked. Likelier to be picked (LIKED_BOOST)."""
    rng = rng or random
    avoid, liked, blocked = avoid or set(), liked or set(), blocked or set()
    where = "status = 'ok'" if only_matched else "status != 'failed'"
    with db() as con:
        rows = [dict(r) for r in con.execute(f"SELECT * FROM songs WHERE {where}")]

    for r in rows:
        r["_key"] = song_key(r["artist"], r["title"])

    pool, other_genres = [], []
    for r in rows:
        if r["_key"] in blocked:
            continue
        if mood not in r["moods"].split(","):
            continue
        if genre == "any" or r["genre"] == genre:
            pool.append(r)
        else:
            other_genres.append(r)

    def ranked(songs, extra):
        # Weighted random order (Efraimidis-Spirakis): higher weight -> likelier to come first.
        def weight(r):
            w = _weight(r, time, weather)
            return w * LIKED_BOOST if r["_key"] in liked else w

        keyed = ((rng.random() ** (1.0 / weight(r)), r, extra) for r in songs)
        return sorted(keyed, key=lambda x: x[0], reverse=True)

    def tiered(songs, extra):
        # Order of preference, each group shuffled by weight:
        #   1. songs with a matched YouTube video that were not played recently
        #   2. matched songs that were played recently (only when 1 runs out)
        #   3. songs without a video, not recent
        #   4. songs without a video, recent
        # Matched songs come first so as many songs as possible end up in the
        # "Open as YouTube playlist" link.
        groups = []
        for matched in (True, False):
            part = [s for s in songs if (s["status"] == "ok") == matched]
            groups.append([s for s in part if s["_key"] not in avoid])
            groups.append([s for s in part if s["_key"] in avoid])
        out = []
        for g in groups:
            out += ranked(g, extra)
        return out

    # Songs of the chosen genre come first; other genres are only used if the list is still short.
    keyed = tiered(pool, False) + tiered(other_genres, True)

    picked, per_artist, used_extra = [], {}, False
    for _, r, extra in keyed:
        a = _first_artist(r["artist"])
        if per_artist.get(a, 0) >= MAX_PER_ARTIST:
            continue
        per_artist[a] = per_artist.get(a, 0) + 1
        picked.append(r)
        used_extra = used_extra or extra
        if len(picked) >= size:
            break

    for r in picked:
        r.pop("_key", None)

    note = None
    if used_extra:
        note = (
            f"There aren't many {options.label(options.GENRES, genre)} songs for this mood yet, "
            "so a few from other genres were added."
        )
    return picked, note


# ------------------------------------------------------------- video ids and quota

def lookup_videos(video_ids):
    """Map YouTube video ids to {"artist", "title"}. Also knows ids that were later replaced
    (bad_ids), so older saved playlists keep their song names."""
    wanted = {v for v in video_ids if v}
    found = {}
    if not wanted:
        return found
    with db() as con:
        rows = con.execute(
            "SELECT artist, title, video_id, bad_ids FROM songs WHERE video_id IS NOT NULL OR bad_ids != ''"
        ).fetchall()
    for r in rows:
        for vid in [r["video_id"], *r["bad_ids"].split(",")]:
            if vid in wanted and vid not in found:
                found[vid] = {"artist": r["artist"], "title": r["title"]}
    return found

def find_song_by_video(video_id):
    """Return {"artist", "title"} for a video id (current or replaced), or None."""
    if not video_id:
        return None
    with db() as con:
        rows = con.execute(
            "SELECT artist, title, video_id, bad_ids FROM songs "
            "WHERE video_id = ? OR (',' || bad_ids || ',') LIKE ?",
            (video_id, f"%,{video_id},%"),
        ).fetchall()
    for r in rows:  # LIKE treats "_" as a wildcard, so confirm the exact id
        if r["video_id"] == video_id or video_id in r["bad_ids"].split(","):
            return {"artist": r["artist"], "title": r["title"]}
    return None


def pending(limit=None, include_failed=False):
    statuses = ("pending", "failed") if include_failed else ("pending",)
    q = f"SELECT * FROM songs WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id"
    with db() as con:
        rows = [dict(r) for r in con.execute(q, statuses)]
    # Take one song from each genre in turn, so every run (the free quota only covers about
    # 90 songs a day) fills all genres evenly instead of finishing pop first and rock last.
    by_genre = {}
    for r in rows:
        by_genre.setdefault(r["genre"], []).append(r)
    ordered = []
    while by_genre:
        for genre in list(by_genre):
            ordered.append(by_genre[genre].pop(0))
            if not by_genre[genre]:
                del by_genre[genre]
    return ordered[: int(limit)] if limit else ordered


def mark_resolved(song_id, video_id, views):
    with _write_lock, db() as con:
        con.execute(
            "UPDATE songs SET video_id = ?, views = ?, status = 'ok', reports = 0 WHERE id = ?",
            (video_id, views, song_id),
        )


def ok_songs():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM songs WHERE status = 'ok' ORDER BY id")]


def _mark_bad(con, song_id):
    """Forget a song's video (remembering it as bad) so it gets looked up again."""
    row = con.execute("SELECT video_id, bad_ids FROM songs WHERE id = ?", (song_id,)).fetchone()
    if not row:
        return
    bad = [b for b in row["bad_ids"].split(",") if b]
    if row["video_id"] and row["video_id"] not in bad:
        bad.append(row["video_id"])
    con.execute(
        "UPDATE songs SET status = 'pending', video_id = NULL, views = NULL, reports = 0, bad_ids = ? WHERE id = ?",
        (",".join(bad), song_id),
    )


def mark_bad(song_id):
    with _write_lock, db() as con:
        _mark_bad(con, song_id)


def ok_count():
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM songs WHERE status = 'ok'").fetchone()[0]


def mark_failed(song_id):
    with _write_lock, db() as con:
        con.execute("UPDATE songs SET status = 'failed' WHERE id = ?", (song_id,))


def _today():
    # YouTube quota resets at midnight Pacific time; a fixed UTC-8 is close enough.
    return datetime.now(timezone(timedelta(hours=-8))).strftime("%Y-%m-%d")


def usage_today():
    with db() as con:
        row = con.execute("SELECT units FROM usage WHERE day = ?", (_today(),)).fetchone()
    return row["units"] if row else 0


def add_usage(units):
    with _write_lock, db() as con:
        con.execute(
            """INSERT INTO usage (day, units) VALUES (?, ?)
               ON CONFLICT (day) DO UPDATE SET units = units + excluded.units""",
            (_today(), units),
        )


def set_usage_at_least(units):
    with _write_lock, db() as con:
        con.execute(
            """INSERT INTO usage (day, units) VALUES (?, ?)
               ON CONFLICT (day) DO UPDATE SET units = MAX(units, excluded.units)""",
            (_today(), units),
        )


def stats():
    with db() as con:
        by_status = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM songs GROUP BY status")}
        rows = [dict(r) for r in con.execute("SELECT genre, moods FROM songs WHERE status != 'failed'")]
    grid = {}
    for r in rows:
        for m in r["moods"].split(","):
            grid[(m, r["genre"])] = grid.get((m, r["genre"]), 0) + 1
    return by_status, grid
