# MoodList

Pick a mood, time of day, weather and genre. Get a playlist of 10-15 popular songs that plays right in the page.
No accounts, no login, nothing stored about users. Works on phones, tablets and laptops, and can be installed like an app.

## How it works

1. `data/seed_songs.csv` is the song catalog: about 300 well-known hits, each tagged with genre, moods, times of day and weather.
2. When someone makes a playlist, the server filters the catalog by their choices and picks songs at random, favouring the ones that fit their time of day and weather. No outside calls, so it is instant and free, however many people use it.
3. Each song needs a YouTube video id to play in the page. `build_catalog.py` looks them up once through the YouTube Data API and saves them in a small database (`data/moodlist.db`). A video is accepted only if it has **1M+ views**, is public, embeddable, not age-restricted and not blocked in your `YOUTUBE_REGION` countries, and is not a cover, karaoke, remix or live version.
4. Songs that already have a video are always picked first, so they all go into the **Open all N songs as a YouTube playlist** button at the top of the result. Songs without a video yet only fill the gaps; the page says how many are missing ("7 of 12 songs") and gives those songs a Search link.
5. The embedded player is off by default (set `SHOW_PLAYER=1` in `.env` to bring it back). Each song is a link that opens it on YouTube.

The free YouTube quota covers about 90 songs a day, so a fresh catalog needs a few days of `python build_catalog.py` runs before every playlist is complete. Each run covers all genres evenly, not one genre at a time.

## Setup

### 1. Install

You need Python 3.10 or newer.

```bash
cd moodlist
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac / Linux
pip install -r requirements.txt
```

### 2. Get a free YouTube API key

1. Go to https://console.cloud.google.com and create a project (any name).
2. Open **APIs & Services > Library**, search for **YouTube Data API v3** and click **Enable**.
3. Open **APIs & Services > Credentials > Create credentials > API key**.
4. Click the key, under **API restrictions** choose **YouTube Data API v3**, and save.
5. Make a **copy** of `.env.example` and name the copy `.env` (the app only reads `.env`; `.env.example` is just a template, so keep real keys out of it). Paste the key after `YOUTUBE_API_KEY=`.
6. In the same `.env`, set `YOUTUBE_REGION` to the countries where people will use the app, for example `YOUTUBE_REGION=AM,US`. Videos blocked in any of these countries are skipped (see "Video unavailable" below).

### 3. Look up the videos

```bash
python build_catalog.py
```

A free key has 10,000 quota units per day. Looking up one song costs about 101 units, so one run covers around 90 songs and then stops by itself. With about 300 songs, run it again on the next 3 days (the quota resets daily at midnight Pacific time). Songs already found are kept.

You can skip this step. The running app also looks up a few missing songs in the background each time someone makes a playlist, so the catalog fills itself in, just more slowly.

Handy options: `python build_catalog.py --stats` shows how many songs exist per mood and genre, `--limit 20` looks up only 20 songs, `--retry` retries songs that found no match, and `--recheck` re-tests every saved video against the current rules and replaces the ones that can't play (it costs 1 quota unit per 50 videos, plus the normal cost for each replacement search).

### 4. Run it

```bash
uvicorn main:app --reload
```

Open http://127.0.0.1:8000.

To try it on your phone, run `uvicorn main:app --host 0.0.0.0`, make sure the phone is on the same Wi-Fi, and open `http://<your-computer-ip>:8000`. Installing it as an app needs HTTPS, which you get once it is hosted online (localhost on the computer itself also works).

## Changing things

| I want to... | Edit |
| --- | --- |
| Add or remove songs, change moods/times/weather of a song | `data/seed_songs.csv` (restart the app or run `build_catalog.py`) |
| Add a mood, time, weather or genre button | `options.py`, then tag songs with it in the CSV |
| Change colors, spacing, layout | `static/style.css` (colors are at the top) |
| Change behavior of the page | `static/app.js` |
| Change how songs are picked | `pick_songs` in `catalog.py` |
| Change the 1M-view rule | `MIN_VIEWS` in `.env` |
| Change playlist length | `size` limits in `main.py` (currently 10-15) |

Only add songs that are really popular. The 1M-view check rejects obscure matches, but the better the catalog, the better the playlists.

## If a video says "Video unavailable"

(This only applies if you turned the embedded player on with `SHOW_PLAYER=1`.) The player shows this when YouTube refuses to play a video inside another site's page. The usual reasons are: the owner (often a record label) doesn't allow embedding, the video is age-restricted, or it is blocked in the viewer's country. The YouTube API's "embeddable" flag doesn't catch all of these.

What the app does about it:
- When it saves a video it checks age restriction and country blocks for the countries in `YOUTUBE_REGION`.
- If a video still can't play, the page skips it, says so, and every song has a **↗** link that opens the video on YouTube itself, which usually works even when the embedded player doesn't.
- The page tells the server (video id only, nothing about the visitor) when a video can't play. After 2 reports from different visitors the video is dropped and the song is looked up again next time, never choosing that video again.

What to do on your side:
1. Set `YOUTUBE_REGION` in `.env` to include your own country (for example `AM,US`).
2. Run `python build_catalog.py --recheck` once. It tests all saved videos again and replaces the broken ones.
3. Restart the app and hard refresh the page (Ctrl+Shift+R).

If the player shows "error 153" or every video fails the same way, the page is probably not sending a Referer to YouTube. Open the app through `http://127.0.0.1:8000` (not as a saved .html file) and don't add a `Referrer-Policy: no-referrer` header if you host it.

## Good to know

- **Thin spots:** some mood and genre combinations have few songs (for example Latin + sad). Then the playlist is padded with songs from other genres and the page says so. Add songs to the CSV to fill the gaps; `--stats` shows where they are.
- **"Open as YouTube playlist"** uses a YouTube link format that is not officially documented, so it may not work for every list (or at all, if YouTube changes it). The **↗** link next to each song is the reliable way to open a video on YouTube.
- **Autoplay:** iPhones and many browsers block sound until you tap play, so the first song is loaded and waits for a tap. After that, songs continue automatically.
- **Ads:** people without YouTube Premium will hear YouTube ads in the player.
- **Privacy:** the server stores no user data. Rate limiting is per IP address, in memory only.

## Putting it online

Any host that runs Python works (Render, Railway, Fly.io and similar). Set the variables from `.env` on the host, start it with `uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers`, and keep `data/` on a persistent disk so the video ids survive restarts (or run `build_catalog.py` on the host). Your YouTube key stays on the server, never in the page.

## Tests

```bash
python -m unittest discover tests -v
```

Tests use a temporary database and a fake YouTube API, so they need no key or internet.
