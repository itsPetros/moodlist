"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { mood: null, time: null, weather: null, genre: "any", tracks: [] };

/* ---------------------------------------------------------------- helpers */

function timeOfDay(hour) {
  if (hour < 5) return "night";
  if (hour < 12) return "morning";
  if (hour < 17) return "afternoon";
  if (hour < 22) return "evening";
  return "night";
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 1800);
}

function showError(message) {
  const el = $("#error");
  el.textContent = message || "";
  el.hidden = !message;
}

/* ---------------------------------------------------------------- choices */

function buildChips(container, items, key, optional) {
  container.textContent = "";
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.dataset.id = item.id;
    button.setAttribute("aria-pressed", String(state[key] === item.id));

    const emoji = document.createElement("span");
    emoji.textContent = item.emoji;
    emoji.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = item.label;
    button.append(emoji, label);

    button.addEventListener("click", () => {
      state[key] = optional && state[key] === item.id ? null : item.id;
      for (const chip of container.children) {
        chip.setAttribute("aria-pressed", String(chip.dataset.id === state[key]));
      }
      updateCta();
    });
    container.append(button);
  }
}

function updateCta() {
  const ready = Boolean(state.mood);
  $("#go").disabled = !ready;
  $("#hint").textContent = ready ? "" : "Pick a mood to start";
}

async function loadOptions() {
  try {
    const res = await fetch("/api/options");
    const options = await res.json();
    state.time = timeOfDay(new Date().getHours()); // pre-select the current time of day
    buildChips($("#g-mood"), options.moods, "mood", false);
    buildChips($("#g-time"), options.times, "time", true);
    buildChips($("#g-weather"), options.weathers, "weather", true);
    buildChips($("#g-genre"), options.genres, "genre", false);
    updateCta();
  } catch {
    showError("Couldn't load the options. Check your connection and reload.");
  }
}

/* --------------------------------------------------------------- playlist */

async function generate() {
  if (!state.mood) return;
  const button = $("#go");
  button.disabled = true;
  button.textContent = "Creating…";
  showError("");

  const params = new URLSearchParams({ mood: state.mood, genre: state.genre });
  if (state.time) params.set("time", state.time);
  if (state.weather) params.set("weather", state.weather);

  try {
    const res = await fetch("/api/playlist?" + params);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.");
    }
    renderResult(data);
  } catch (err) {
    showError(err instanceof TypeError ? "Network problem. Check your connection and try again." : err.message);
  } finally {
    button.disabled = !state.mood;
    button.textContent = "Create playlist";
  }
}

function renderResult(data) {
  state.tracks = data.tracks;

  $("#empty").hidden = true;
  $("#result").hidden = false;
  $("#r-title").textContent = data.title;
  const note = $("#r-note");
  note.textContent = data.note || "";
  note.hidden = !data.note;

  // The main action: every song in the list is in this one YouTube playlist.
  const link = $("#yt-link");
  link.hidden = !data.youtube_playlist_url;
  if (data.youtube_playlist_url) {
    link.href = data.youtube_playlist_url;
    link.textContent =
      data.playlist_count < data.total
        ? `Open ${data.playlist_count} of ${data.total} songs as a YouTube playlist ↗`
        : `Open all ${data.total} songs as a YouTube playlist ↗`;
  }

  const list = $("#tracks");
  list.textContent = "";
  state.tracks.forEach((track) => list.append(buildRow(track)));

  if (window.matchMedia("(max-width: 899px)").matches) {
    $("#right").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// Each song is a plain link: to its YouTube video, or to a YouTube search if no video is matched.
function buildRow(track) {
  const li = document.createElement("li");
  li.className = "row";
  const row = document.createElement("a");
  row.className = "track is-link";
  row.href = track.video_id
    ? "https://www.youtube.com/watch?v=" + encodeURIComponent(track.video_id)
    : track.search_url;
  row.target = "_blank";
  row.rel = "noopener noreferrer";

  const text = document.createElement("span");
  text.className = "t-text";
  const title = document.createElement("span");
  title.className = "t-title";
  title.textContent = track.title;
  const artist = document.createElement("span");
  artist.className = "t-artist";
  artist.textContent = track.artist;
  text.append(title, artist);

  const tag = document.createElement("span");
  tag.className = "t-tag";
  tag.textContent = track.video_id ? "Watch ↗" : "Search ↗";

  row.append(text, tag);
  li.append(row);
  return li;
}

/* ---------------------------------------------------------------- actions */

async function copyList() {
  const text = state.tracks.map((t) => `${t.artist} - ${t.title}`).join("\n");
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("List copied");
}

/* ------------------------------------------------------------------- init */

$("#go").addEventListener("click", generate);
$("#again").addEventListener("click", generate);
$("#copy").addEventListener("click", copyList);

const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
if (isIOS && !window.navigator.standalone) $("#ios-hint").hidden = false;

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

loadOptions();
