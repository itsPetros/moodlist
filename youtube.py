"""Finds the right YouTube video for a catalog song.

Cost (YouTube Data API quota units): search = 100, videos lookup = 1.
A free key has 10,000 units per day, i.e. about 90 songs per day. Each song is
looked up once and then stored in the database, so this is a one-time cost.

A video is accepted only if:
  * its title contains the song title and the artist appears in the title/channel,
  * it has at least MIN_VIEWS views (default 1,000,000),
  * it is public, embeddable, not age-restricted and not blocked in the countries
    listed in YOUTUBE_REGION (a video can be "embeddable" and still be blocked in
    your country, which shows "Video unavailable" in the player),
  * it is not a cover, karaoke, remix, live version, etc. (unless the song itself is).
Among the accepted videos the one with the most views wins.
"""
import os
import re
import unicodedata

import httpx

import catalog

API = "https://www.googleapis.com/youtube/v3"
SEARCH_COST = 100
VIDEOS_COST = 1
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}

BAD_WORDS = (
    "cover", "karaoke", "reaction", "tutorial", "lesson", "nightcore", "slowed",
    "sped up", "8d audio", "remix", "instrumental", "live", "mashup", "parody",
    "piano version", "acoustic version", "1 hour", "10 hours", "lofi", "lo fi",
)


class YouTubeError(Exception):
    def __init__(self, message, reason=""):
        super().__init__(message)
        self.reason = reason


def api_key():
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def min_views():
    return int(os.environ.get("MIN_VIEWS", "1000000"))


def daily_budget():
    return int(os.environ.get("DAILY_UNIT_BUDGET", "9000"))


def regions():
    """Countries where a video must be playable to be accepted (YOUTUBE_REGION=AM,US)."""
    raw = os.environ.get("YOUTUBE_REGION", "US")
    return [r.strip().upper() for r in raw.split(",") if r.strip()] or ["US"]


DETAIL_PARTS = "snippet,statistics,status,contentDetails"


def api_get(path, params):
    r = httpx.get(f"{API}/{path}", params={**params, "key": api_key()}, timeout=15)
    if r.status_code != 200:
        message, reason = r.text[:200], ""
        try:
            err = r.json()["error"]
            message = err.get("message", message)
            reason = (err.get("errors") or [{}])[0].get("reason", "")
        except Exception:
            pass
        raise YouTubeError(message, reason)
    return r.json()


# ------------------------------------------------------------------- matching

def norm(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("'", "").replace("’", "").replace("&", " and ")
    return re.sub(r"[\W_]+", " ", text).strip()


def squash(text):
    return norm(text).replace(" ", "")


def first_artist(artist):
    return re.split(r"\s+(?:ft\.?|feat\.?|featuring)\s+", artist, maxsplit=1)[0].strip()


def _has_word(haystack, word):
    return re.search(rf"\b{re.escape(word)}\b", haystack) is not None


def looks_right(song, video):
    """True if this video plausibly is the official upload of the song."""
    vtitle = norm(video["snippet"]["title"])
    channel = norm(video["snippet"].get("channelTitle", ""))
    song_title = norm(song["title"])
    if not song_title or squash(song["title"]) not in squash(video["snippet"]["title"]):
        return False

    artist = first_artist(song["artist"])
    blob = squash(video["snippet"]["title"] + " " + video["snippet"].get("channelTitle", ""))
    tokens = [t for t in norm(artist).split() if len(t) >= 4]
    artist_ok = squash(artist) in blob or any(t in blob for t in tokens)
    if not artist_ok:
        return False

    for word in BAD_WORDS:
        if _has_word(vtitle, word) and not _has_word(song_title, word):
            return False
    return True


def playable_issue(video):
    """Why this video can't be played inside the page, or None if it looks playable.
    'embeddable' alone is not enough: a video can still be blocked in some countries
    or age-restricted, and then the player shows 'Video unavailable'."""
    status = video.get("status", {})
    if status.get("privacyStatus") != "public":
        return "not public"
    if not status.get("embeddable"):
        return "embedding disabled"
    if status.get("uploadStatus") not in (None, "processed"):
        return "still processing"
    details = video.get("contentDetails", {})
    if details.get("contentRating", {}).get("ytRating") == "ytAgeRestricted":
        return "age restricted"
    restriction = details.get("regionRestriction") or {}
    blocked = {c.upper() for c in restriction.get("blocked", [])}
    allowed = restriction.get("allowed")
    allowed = None if allowed is None else {c.upper() for c in allowed}
    for region in regions():
        if region in blocked:
            return f"blocked in {region}"
        if allowed is not None and region not in allowed:
            return f"not available in {region}"
    return None


def choose_best(song, videos, exclude=()):
    best = None
    for v in videos:
        if v["id"] in exclude or playable_issue(v):
            continue
        views = int(v.get("statistics", {}).get("viewCount", 0))
        if views < min_views() or not looks_right(song, v):
            continue
        if best is None or views > best["views"]:
            best = {"video_id": v["id"], "views": views, "title": v["snippet"]["title"]}
    return best


# ------------------------------------------------------------------- resolving

def resolve_song(song):
    """Look up one song. Returns (status, info) where status is
    'ok' | 'failed' | 'skipped' | 'error'."""
    if not api_key():
        return "skipped", "no YOUTUBE_API_KEY set"
    if catalog.usage_today() + SEARCH_COST + VIDEOS_COST > daily_budget():
        return "skipped", "daily quota budget used up"
    try:
        found = api_get(
            "search",
            {
                "part": "snippet",
                "type": "video",
                "videoEmbeddable": "true",
                "videoSyndicated": "true",  # only videos that can play outside youtube.com
                "regionCode": regions()[0],
                "maxResults": 8,
                "q": f"{first_artist(song['artist'])} {song['title']} official",
            },
        )
        catalog.add_usage(SEARCH_COST)
        ids = [i["id"]["videoId"] for i in found.get("items", []) if i.get("id", {}).get("videoId")]
        if not ids:
            catalog.mark_failed(song["id"])
            return "failed", "no search results"
        details = api_get("videos", {"part": DETAIL_PARTS, "id": ",".join(ids)})
        catalog.add_usage(VIDEOS_COST)
        # Videos that were reported as unplayable before are never picked again.
        exclude = {b for b in (song.get("bad_ids") or "").split(",") if b}
        best = choose_best(song, details.get("items", []), exclude)
        if not best:
            catalog.mark_failed(song["id"])
            return "failed", "no popular, embeddable match"
        catalog.mark_resolved(song["id"], best["video_id"], best["views"])
        return "ok", best
    except YouTubeError as e:
        if e.reason in QUOTA_REASONS:
            catalog.set_usage_at_least(daily_budget())
            return "skipped", "YouTube quota exceeded for today"
        return "error", f"{e} ({e.reason})" if e.reason else str(e)
    except httpx.HTTPError as e:
        return "error", f"network problem: {e}"


# ------------------------------------------------------------------- re-checking

def recheck_videos(songs):
    """Re-validate videos that were saved earlier, against the current rules.
    Costs 1 quota unit per 50 videos. Returns (still_ok, broken) where
    still_ok is a list of (song, views) and broken is a list of (song, reason)."""
    still_ok, broken = [], []
    for i in range(0, len(songs), 50):
        chunk = songs[i : i + 50]
        found = api_get("videos", {"part": DETAIL_PARTS, "id": ",".join(s["video_id"] for s in chunk)})
        catalog.add_usage(VIDEOS_COST)
        by_id = {v["id"]: v for v in found.get("items", [])}
        for song in chunk:
            video = by_id.get(song["video_id"])
            if video is None:
                broken.append((song, "removed or private"))
                continue
            issue = playable_issue(video)
            if issue:
                broken.append((song, issue))
            else:
                still_ok.append((song, int(video.get("statistics", {}).get("viewCount", 0))))
    return still_ok, broken
