// Local Storage Token Key
const TOKEN_KEY = "moodlist_jwt_token";
let currentPlaylistSongIds = []; // Populate when playlist is generated

// --- INITIALIZATION ---
document.addEventListener("DOMContentLoaded", () => {
  checkAuthStatus();
});

function getAuthHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { "Authorization": `Bearer ${token}` } : {};
}

// --- AUTH MODAL CONTROLS ---
function openAuthModal(mode = 'login') {
  document.getElementById("auth-modal").classList.remove("hidden");
  switchTab(mode);
}

function closeAuthModal() {
  document.getElementById("auth-modal").classList.add("hidden");
}

function switchTab(mode) {
  const forms = ["signup-form", "verify-form", "login-form", "forgot-form", "reset-form"];
  forms.forEach(id => document.getElementById(id).classList.add("hidden"));
  
  if (mode === 'signup') document.getElementById("signup-form").classList.remove("hidden");
  if (mode === 'login') document.getElementById("login-form").classList.remove("hidden");
  if (mode === 'forgot') document.getElementById("forgot-form").classList.remove("hidden");
}

// --- SIGN UP & VERIFICATION ---
let pendingEmail = "";

async function handleSignup(e) {
  e.preventDefault();
  const username = document.getElementById("signup-username").value;
  const email = document.getElementById("signup-email").value;

  const res = await fetch("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email })
  });

  const data = await res.json();
  if (res.ok) {
    pendingEmail = email;
    alert("Verification code sent! (Check console if testing locally)");
    if (data.verification_code) console.log("Demo Code:", data.verification_code);
    
    document.getElementById("signup-form").classList.add("hidden");
    document.getElementById("verify-form").classList.remove("hidden");
  } else {
    alert(data.detail || "Registration failed");
  }
}

async function handleVerifyAndSetPassword(e) {
  e.preventDefault();
  const code = document.getElementById("verify-code").value;
  const password = document.getElementById("signup-password").value;

  const res = await fetch("/auth/set-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: pendingEmail, code, password })
  });

  if (res.ok) {
    alert("Account verified successfully! Please log in.");
    switchTab("login");
  } else {
    const data = await res.json();
    alert(data.detail || "Verification failed");
  }
}

// --- LOGIN ---
async function handleLogin(e) {
  e.preventDefault();
  const formData = new FormData();
  formData.append("username", document.getElementById("login-username").value);
  formData.append("password", document.getElementById("login-password").value);

  const res = await fetch("/login", {
    method: "POST",
    body: formData
  });

  const data = await res.json();
  if (res.ok) {
    localStorage.setItem(TOKEN_KEY, data.access_token);
    closeAuthModal();
    checkAuthStatus();
  } else {
    alert(data.detail || "Login failed");
  }
}

function handleLogout() {
  localStorage.removeItem(TOKEN_KEY);
  checkAuthStatus();
}

// --- CHECK LOGGED IN USER & UPDATE HEADER ---
async function checkAuthStatus() {
  const token = localStorage.getItem(TOKEN_KEY);
  const nav = document.getElementById("user-nav");

  if (!token) {
    nav.innerHTML = `<button onclick="openAuthModal('login')">Log In / Sign Up</button>`;
    document.getElementById("save-playlist-container")?.classList.add("hidden");
    return;
  }

  const res = await fetch("/users/me", { headers: getAuthHeaders() });
  if (res.ok) {
    const user = await res.json();
    const avatar = user.profile_picture_url || '/static/default-avatar.png';
    
    nav.innerHTML = `
      <div class="user-profile-badge">
        <img src="${avatar}" alt="Avatar" class="nav-avatar" onclick="triggerAvatarUpload()" />
        <span>${user.username}</span>
        <button onclick="handleLogout()">Logout</button>
      </div>
      <input type="file" id="avatar-file-input" style="display:none" onchange="uploadAvatar(this)" />
    `;
    document.getElementById("save-playlist-container")?.classList.remove("hidden");
  } else {
    handleLogout();
  }
}

// --- AVATAR UPLOAD ---
function triggerAvatarUpload() {
  document.getElementById("avatar-file-input").click();
}

async function uploadAvatar(input) {
  if (!input.files[0]) return;
  const formData = new FormData();
  formData.append("file", input.files[0]);

  const res = await fetch("/users/me/avatar", {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData
  });

  if (res.ok) {
    checkAuthStatus();
  } else {
    alert("Avatar upload failed");
  }
}

// --- SAVE PLAYLIST ---
async function saveCurrentPlaylist() {
  const title = document.getElementById("playlist-title-input").value || "My Mood Playlist";
  const is_public = document.getElementById("playlist-public-checkbox").checked;
  
  // Example selections from page filters
  const mood = document.querySelector('.mood-btn.active')?.dataset.value || "chill";
  const genre = document.querySelector('.genre-btn.active')?.dataset.value || "pop";

  const payload = {
    title,
    mood,
    genre,
    song_ids: currentPlaylistSongIds,
    is_public
  };

  const res = await fetch("/playlists", {
    method: "POST",
    headers: {
      ...getAuthHeaders(),
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (res.ok) {
    alert("Playlist saved successfully!");
  } else {
    alert("Failed to save playlist.");
  }
}