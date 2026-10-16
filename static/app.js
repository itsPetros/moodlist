"use strict";

const $ = (sel) => document.querySelector(sel);
const TOKEN_KEY = "moodlist_jwt_token";

const state = { mood: null, time: null, weather: null, genre: "any", tracks: [] };

function getAuthHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { "Authorization": `Bearer ${token}` } : {};
}

function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 1800);
}

function showError(message) {
  const el = $("#error");
  if (el) {
    el.textContent = message || "";
    el.hidden = !message;
  }
}

/* ---------------------------------------------------- Flow & Navigation */

async function checkAuthFlow() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    showAuthScreen();
    return;
  }

  try {
    const res = await fetch("/users/me", { headers: getAuthHeaders() });
    if (res.ok) {
      const user = await res.json();
      renderAppHeader(user);
      showMainAppScreen();
    } else {
      handleLogout();
    }
  } catch {
    showAuthScreen();
  }
}

function showAuthScreen() {
  const authScreen = $("#auth-screen");
  const mainScreen = $("#main-app-screen");
  if (authScreen) authScreen.classList.remove("hidden");
  if (mainScreen) mainScreen.classList.add("hidden");
}

function showMainAppScreen() {
  const authScreen = $("#auth-screen");
  const mainScreen = $("#main-app-screen");
  if (authScreen) authScreen.classList.add("hidden");
  if (mainScreen) mainScreen.classList.remove("hidden");
  loadOptions();
}

function renderAppHeader(user) {
  const nav = $("#user-nav");
  if (!nav) return;
  const avatar = user.profile_picture_url || "/static/icons/icon-192.png";
  nav.innerHTML = `
    <img src="${avatar}" alt="Avatar" class="user-avatar" onclick="triggerAvatarUpload()" title="Click to change profile picture" />
    <span style="font-weight:500;">${user.username}</span>
    <button type="button" class="btn" onclick="handleLogout()">Logout</button>
    <input type="file" id="avatar-file-input" style="display:none" onchange="uploadAvatar(this)" />
  `;
}

/* ---------------------------------------------------- Auth Handlers */

window.handleGoogleSignIn = async function(response) {
  try {
    const res = await fetch('/auth/google', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential: response.credential })
    });

    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      alert("Backend Error: Check server terminal.");
      return;
    }

    if (res.ok && data && data.access_token) {
      localStorage.setItem(TOKEN_KEY, data.access_token);
      toast("Welcome to MoodList!");
      checkAuthFlow();
    } else {
      alert("Auth failed: " + (data?.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Could not connect to backend server.");
  }
};

window.handleLogout = function() {
  localStorage.removeItem(TOKEN_KEY);
  showAuthScreen();
};

window.triggerAvatarUpload = function() {
  const input = $("#avatar-file-input");
  if (input) input.click();
};

window.uploadAvatar = async function(input) {
  if (!input.files[0]) return;
  const formData = new FormData();
  formData.append("file", input.files[0]);

  const res = await fetch("/users/me/avatar", {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData
  });

  if (res.ok) {
    checkAuthFlow();
    toast("Avatar updated");
  } else {
    alert("Avatar upload failed");
  }
};

/* ------------------------------------------------ Main Playlist App Logic */

function timeOfDay(hour) {
  if (hour < 5) return "night";
  if (hour < 12) return "morning";
  if (hour < 17) return "afternoon";
  if (hour < 22) return "evening";
  return "night";
}

function buildChips(container, items, key, optional) {
  if (!container) return;
  container.textContent = "";
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.dataset.id = item.id;
    button.setAttribute("aria-pressed", String(state[key] === item.id));

    const emoji = document.createElement("span");
    emoji.textContent = item.emoji;
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
  const goBtn = $("#go");
  const hintEl = $("#hint");
  if (goBtn) goBtn.disabled = !ready;
  if (hintEl) hintEl.textContent = ready ? "" : "Pick a mood to start";
}

async function loadOptions() {
  try {
    const res = await fetch("/api/options");
    const options = await res.json();
    state.time = timeOfDay(new Date().getHours());
    buildChips($("#g-mood"), options.moods, "mood", false);
    buildChips($("#g-time"), options.times, "time", true);
    buildChips($("#g-weather"), options.weathers, "weather", true);
    buildChips($("#g-genre"), options.genres, "genre", false);
    updateCta();
  } catch {
    showError("Couldn't load options.");
  }
}

async function generate() {
  if (!state.mood) return;
  const button = $("#go");
  if (!button) return;
  button.disabled = true;
  button.textContent = "Creating…";
  showError("");

  const params = new URLSearchParams({ mood: state.mood, genre: state.genre });
  if (state.time) params.set("time", state.time);
  if (state.weather) params.set("weather", state.weather);

  try {
    const res = await fetch("/api/playlist?" + params);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Error generating playlist.");
    renderResult(data);
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = !state.mood;
    button.textContent = "Create playlist";
  }
}

function buildRow(track) {
  const li = document.createElement("li");
  li.className = "row";

  const a = document.createElement("a");
  a.className = "track is-link";
  a.href = track.search_url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";

  const textDiv = document.createElement("div");
  textDiv.className = "t-text";

  const titleSpan = document.createElement("span");
  titleSpan.className = "t-title";
  titleSpan.textContent = track.title;

  const artistSpan = document.createElement("span");
  artistSpan.className = "t-artist";
  artistSpan.textContent = track.artist;

  textDiv.append(titleSpan, artistSpan);
  a.append(textDiv);
  li.append(a);
  return li;
}

function renderResult(data) {
  state.tracks = data.tracks;

  $("#empty").hidden = true;
  $("#result").hidden = false;
  $("#r-title").textContent = data.title;

  const note = $("#r-note");
  note.textContent = data.note || "";
  note.hidden = !data.note;

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

window.saveCurrentPlaylist = async function() {
  const title = $("#playlist-title-input").value.trim() || "My MoodList Playlist";
  const isPublic = $("#playlist-public-checkbox").checked;
  const songIds = state.tracks.map(t => t.video_id).filter(Boolean);

  if (!songIds.length) {
    alert("No valid track IDs to save.");
    return;
  }

  const payload = {
    title,
    mood: state.mood,
    genre: state.genre,
    weather: state.weather,
    time_of_day: state.time,
    song_ids: songIds,
    is_public: isPublic
  };

  try {
    const res = await fetch("/playlists", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getAuthHeaders()
      },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      toast("Playlist saved successfully!");
      $("#playlist-title-input").value = "";
    } else {
      const data = await res.json();
      alert("Save failed: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Could not save playlist.");
  }
};

/* Initialize Application on Page Load */
document.addEventListener("DOMContentLoaded", () => {
  checkAuthFlow();

  const goBtn = $("#go");
  if (goBtn) {
    goBtn.addEventListener("click", generate);
  }
});