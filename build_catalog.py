"""Fill the catalog with YouTube video ids (run once, then again on later days).

    python build_catalog.py              # look up as many songs as today's quota allows
    python build_catalog.py --limit 20   # only look up 20 songs
    python build_catalog.py --retry      # also retry songs that failed before
    python build_catalog.py --recheck    # re-test saved videos, replace the ones that can't play
    python build_catalog.py --stats      # show how many songs exist per mood and genre

Needs YOUTUBE_API_KEY in your .env file.
"""
import argparse
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import catalog
import options
import youtube


def show_stats():
    by_status, grid = catalog.stats()
    print("Songs by status:", by_status or "none")
    genres = [g["id"] for g in options.GENRES if g["id"] != "any"]
    print("\nSongs per mood x genre (thin spots are where genre picks get padded):")
    print(f"{'':11}" + "".join(f"{g[:7]:>8}" for g in genres) + f"{'ALL':>8}")
    for m in options.MOODS:
        counts = [grid.get((m["id"], g), 0) for g in genres]
        print(f"{m['id']:11}" + "".join(f"{c:>8}" for c in counts) + f"{sum(counts):>8}")


def recheck():
    """Re-test every saved video (1 quota unit per 50 videos) and queue broken ones for replacement."""
    songs = catalog.ok_songs()
    if not songs:
        print("No saved videos to re-check yet.")
        return
    print(f"Re-checking {len(songs)} saved video(s) for playback in: {', '.join(youtube.regions())}")
    try:
        still_ok, broken = youtube.recheck_videos(songs)
    except youtube.YouTubeError as e:
        sys.exit(f"Re-check failed: {e}")
    for song, views in still_ok:
        catalog.mark_resolved(song["id"], song["video_id"], views)
    for song, reason in broken:
        catalog.mark_bad(song["id"])
        print(f"  replace  {song['artist']} - {song['title']}  ({reason})")
    print(f"{len(still_ok)} fine, {len(broken)} will be replaced with a better video.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--retry", action="store_true")
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    n = catalog.sync_seed()
    print(f"Catalog has {n} songs.")
    if args.stats:
        show_stats()
        return
    if not youtube.api_key():
        sys.exit("Set YOUTUBE_API_KEY in your .env file first (see README).")
    print(f"Videos must be playable in: {', '.join(youtube.regions())}  (change with YOUTUBE_REGION in .env)")
    if args.recheck:
        recheck()

    todo = catalog.pending(args.limit, include_failed=args.retry)
    if not todo:
        print("Nothing to do: every song has been looked up.")
        show_stats()
        return
    print(f"{len(todo)} song(s) to look up. Quota used today: {catalog.usage_today()} / {youtube.daily_budget()}\n")

    ok = failed = 0
    for song in todo:
        status, info = youtube.resolve_song(song)
        name = f"{song['artist']} - {song['title']}"
        if status == "ok":
            ok += 1
            print(f"  ok      {name}  ({info['views']:,} views)")
        elif status == "failed":
            failed += 1
            print(f"  no match {name}  ({info})")
        elif status == "skipped":
            print(f"\nStopping: {info}. Run this again tomorrow to continue.")
            break
        else:
            print(f"  error   {name}: {info}")
            if "keyNotValid" in info or "accessNotConfigured" in info or "API key" in info:
                sys.exit("\nThe API key was rejected. Check YOUTUBE_API_KEY and that YouTube Data API v3 is enabled.")
    left = len(catalog.pending())
    print(f"\nDone: {ok} found, {failed} without a good match, {left} still waiting.")


if __name__ == "__main__":
    main()
