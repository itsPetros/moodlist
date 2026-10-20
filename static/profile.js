"use strict";

const TOKEN_KEY = "moodlist_jwt_token";
const $ = (sel) => document.querySelector(sel);

const state = {
  user: null,
  saved: [],
  history: [],
  options: { moods: [], times: [], weathers: [], genres: [] },
  tab: "saved",
};

/* ------------------------------------------------------------ helpers */

// Tiny DOM builder: everything goes in as text, never as HTML.
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs || {})) {
    if (val == null || val === false) continue;
    if (key === "class") el.className = val;
    else if (key === "text") el.textContent = val;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), val);
    else el.setAttribute(key, val === true ? "" : val);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid);
  }
  return el;
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2000);
}

function showError(message) {
  const el = $("#error");
  el.textContent = message || "";
  el.hidden = !message;
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  location.replace("/");
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { ...(options.headers || {}), Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY)}` },
  });
  if (res.status === 401) {
    logout();
    throw new Error("Signed out");
  }
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail; } catch { /* no body */ }
    throw new Error(typeof detail === "string" && detail ? detail : "Something went wrong. Try again.");
  }
  return res.status === 204 ? null : res.json();
}

// The server sends UTC times without a "Z"; add it so the browser converts to local time.
function parseDate(iso) {
  return new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(iso) ? iso : iso + "Z");
}

function when(iso) {
  const d = parseDate(iso);
  const now = new Date();
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return "Today, " + d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (sameDay(d, yesterday)) return "Yesterday";
  const opts = d.getFullYear() === now.getFullYear()
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" };
  return d.toLocaleDateString([], opts);
}

function option(list, id) {
  return state.options[list].find((o) => o.id === id);
}

function tagText(list, id) {
  const o = option(list, id);
  return o ? `${o.emoji} ${o.label}` : id;
}

const chevron = () => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.4");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M6 9l6 6 6-6");
  svg.append(path);
  return svg;
};

// A button that needs a second tap within 3 seconds to confirm a destructive action.
function confirmButton(label, armedLabel, onConfirm) {
  const btn = h("button", { type: "button", class: "pf-btn", text: label });
  let timer = null;
  btn.addEventListener("click", async () => {
    if (!timer) {
      btn.textContent = armedLabel;
      btn.classList.add("pf-danger-armed");
      timer = setTimeout(() => {
        timer = null;
        btn.textContent = label;
        btn.classList.remove("pf-danger-armed");
      }, 3000);
      return;
    }
    clearTimeout(timer);
    timer = null;
    btn.disabled = true;
    try {
      await onConfirm();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = label;
      btn.classList.remove("pf-danger-armed");
      toast(err.message);
    }
  });
  return btn;
}

/* ------------------------------------------------------------ rendering */

function renderHero() {
  const u = state.user;
  $("#name").textContent = u.username;
  $("#email").textContent = u.email;
  $("#avatar").src = u.profile_picture_url || "/static/icons/icon-192.png";
  $("#since").textContent = "Member since " +
    parseDate(u.created_at).toLocaleDateString([], { month: "long", year: "numeric" });
  document.title = `${u.username} · MoodList`;
}

function renderMoods() {
  // Prefer history (everything generated); fall back to saved playlists.
  const source = state.history.length ? state.history : state.saved;
  const counts = new Map();
  for (const p of source) counts.set(p.mood, (counts.get(p.mood) || 0) + 1);
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);

  $("#moods").hidden = top.length === 0;
  const list = $("#mood-list");
  list.textContent = "";
  list.classList.add("pf-top");
  for (const [mood, n] of top) {
    list.append(h("li", { class: "pf-chip" }, tagText("moods", mood), h("b", { text: String(n) })));
  }
}

function renderCounts() {
  $("#count-saved").textContent = state.saved.length;
  $("#count-history").textContent = state.history.length;
}

function tagChips(p) {
  const chips = [h("li", { class: "pf-chip", text: tagText("moods", p.mood) })];
  if (p.genre && p.genre !== "any") chips.push(h("li", { class: "pf-chip", text: tagText("genres", p.genre) }));
  if (p.weather) chips.push(h("li", { class: "pf-chip", text: tagText("weathers", p.weather) }));
  if (p.time_of_day) chips.push(h("li", { class: "pf-chip", text: tagText("times", p.time_of_day) }));
  return h("ul", { class: "pf-chips pf-pl-tags" }, chips);
}

function trackList(tracks) {
  return h("ol", { class: "pf-trk" }, tracks.map((t) =>
    h("li", null,
      h("a", { href: t.url, target: "_blank", rel: "noopener noreferrer" },
        h("div", { class: "t-text" },
          h("span", { class: "t-title", text: t.title }),
          t.artist ? h("span", { class: "t-artist", text: t.artist }) : null)))));
}

function youtubeLink(tracks) {
  const ids = tracks.map((t) => t.video_id).filter(Boolean).slice(0, 50);
  if (ids.length < 2) return null;
  return h("a", {
    class: "pf-btn pf-primary",
    href: "https://www.youtube.com/watch_videos?video_ids=" + ids.join(","),
    target: "_blank",
    rel: "noopener noreferrer",
    text: "Open on YouTube ↗",
  });
}

function playlistCard(p, kind) {
  const count = p.tracks.length;
  const meta = [`${count} ${count === 1 ? "song" : "songs"}`, when(p.created_at)];
  if (kind === "saved") meta.push(p.is_public ? "Public" : "Private");

  const actions = h("div", { class: "pf-actions" }, youtubeLink(p.tracks));

  if (kind === "saved") {
    actions.append(confirmButton("Delete", "Tap again to delete", async () => {
      await api(`/users/me/playlists/${p.id}`, { method: "DELETE" });
      state.saved = state.saved.filter((x) => x.id !== p.id);
      refresh();
      toast("Playlist deleted");
    }));
  } else {
    const save = h("button", { type: "button", class: "pf-btn", text: "Save to library" });
    save.addEventListener("click", () => saveFromHistory(p, save));
    actions.append(save, confirmButton("Remove", "Tap again to remove", async () => {
      await api(`/users/me/history/${p.id}`, { method: "DELETE" });
      state.history = state.history.filter((x) => x.id !== p.id);
      refresh();
      toast("Removed from history");
    }));
  }

  return h("details", { class: "pf-pl" },
    h("summary", null,
      h("h3", { class: "pf-pl-title", text: p.title }),
      h("span", { class: "pf-pl-side" }, meta.map((m) => h("span", { text: m })), chevron()),
      tagChips(p)),
    h("div", { class: "pf-pl-body" }, trackList(p.tracks), actions));
}

function emptyState(emoji, headline, body) {
  return h("div", { class: "pf-empty" },
    h("p", { class: "pf-empty-emoji", "aria-hidden": "true", text: emoji }),
    h("p", null, h("strong", { text: headline })),
    h("p", { text: body }),
    h("a", { class: "pf-btn pf-primary", href: "/", text: "Create a playlist" }));
}

function renderPanels() {
  const saved = $("#panel-saved");
  saved.textContent = "";
  if (!state.saved.length) {
    saved.append(emptyState("💜", "No saved playlists yet.",
      "Create a playlist, name it and tap Save Playlist. It will show up here."));
  } else {
    state.saved.forEach((p) => saved.append(playlistCard(p, "saved")));
  }

  const hist = $("#panel-history");
  hist.textContent = "";
  if (!state.history.length) {
    hist.append(emptyState("🎧", "Nothing here yet.",
      "Every playlist you create while signed in is listed here, newest first."));
  } else {
    hist.append(h("div", { class: "pf-toolbar" },
      confirmButton("Clear history", "Tap again to clear", async () => {
        await api("/users/me/history", { method: "DELETE" });
        state.history = [];
        refresh();
        toast("History cleared");
      })));
    state.history.forEach((p) => hist.append(playlistCard(p, "history")));
  }
}

function refresh() {
  renderCounts();
  renderMoods();
  renderPanels();
}

/* ------------------------------------------------------------ actions */

async function saveFromHistory(p, btn) {
  const songIds = p.tracks.map((t) => t.video_id).filter(Boolean);
  if (!songIds.length) {
    toast("None of these songs have a YouTube match yet, so it can't be saved.");
    return;
  }
  btn.disabled = true;
  try {
    await api("/playlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: p.title,
        mood: p.mood,
        genre: p.genre,
        weather: p.weather,
        time_of_day: p.time_of_day,
        song_ids: songIds,
        is_public: true,
      }),
    });
    state.saved = await api("/users/me/playlists");
    renderCounts();
    renderPanels();
    toast("Saved to your library");
  } catch (err) {
    btn.disabled = false;
    toast(err.message);
  }
}

async function uploadAvatar(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    toast("Pick a picture under 2 MB.");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  try {
    const data = await api("/users/me/avatar", { method: "POST", body: form });
    state.user.profile_picture_url = data.profile_picture_url;
    $("#avatar").src = data.profile_picture_url;
    toast("Profile picture updated");
  } catch (err) {
    toast(err.message);
  }
}

function selectTab(name, focus) {
  state.tab = name;
  for (const t of ["saved", "history"]) {
    const on = t === name;
    const tab = $(`#tab-${t}`);
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`#panel-${t}`).hidden = !on;
    if (on && focus) tab.focus();
  }
}

function wireTabs() {
  const names = ["saved", "history"];
  for (const name of names) {
    const tab = $(`#tab-${name}`);
    tab.addEventListener("click", () => selectTab(name));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      selectTab(names[(names.indexOf(name) + 1) % names.length], true);
    });
  }
}

/* ------------------------------------------------------------ boot */

async function init() {
  if (!localStorage.getItem(TOKEN_KEY)) {
    location.replace("/");
    return;
  }

  $("#logout").addEventListener("click", logout);
  $("#avatar-btn").addEventListener("click", () => $("#avatar-input").click());
  $("#avatar-input").addEventListener("change", (e) => {
    uploadAvatar(e.target.files[0]);
    e.target.value = "";
  });
  wireTabs();

  for (const id of ["saved", "history"]) $(`#panel-${id}`).append(h("p", { class: "pf-loading", text: "Loading…" }));

  try {
    const [user, saved, history, options] = await Promise.all([
      api("/users/me"),
      api("/users/me/playlists"),
      api("/users/me/history"),
      fetch("/api/options").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    state.user = user;
    state.saved = saved;
    state.history = history;
    if (options) state.options = options;
    renderHero();
    refresh();
  } catch (err) {
    if (err.message !== "Signed out") {
      showError("Couldn't load your profile. Check your connection and reload.");
      for (const id of ["saved", "history"]) $(`#panel-${id}`).textContent = "";
    }
  }
}

document.addEventListener("DOMContentLoaded", init);