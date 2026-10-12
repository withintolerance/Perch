"use strict";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function renderLogin() {
  hideAccountChrome();
  summary.textContent = "Sign in";
  disk.textContent = "";
  app.innerHTML = "";
  const form = document.createElement("form");
  form.className = "gate card";
  form.innerHTML = `
    <h2>Sign in</h2>
    <p>This recorder is private. Wi-Fi access is not enough.</p>
    <label class="field"><span>Username</span><input name="username" autocomplete="username" required></label>
    <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>
    <p class="error-text" data-error></p>
    <button type="submit">Sign in</button>`;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await sendJSON("/api/login", "POST", {username: data.get("username"), password: data.get("password")});
      sessionState = await getJSON("/api/session");
      showAccountChrome();
      await refreshStatus();
      if (!pollTimer) pollTimer = window.setInterval(refreshStatus, 5000);
      render();
    } catch (error) {
      form.querySelector("[data-error]").textContent = error.message;
    }
  });
  app.appendChild(form);
}

function renderSetup() {
  hideAccountChrome();
  summary.textContent = "Create the owner account";
  disk.textContent = "";
  app.innerHTML = "";
  const form = document.createElement("form");
  form.className = "gate card";
  form.innerHTML = `
    <h2>Create the owner account</h2>
    <p>The first account is the only one. Do this before anyone else on the network opens the page.</p>
    <label class="field"><span>Username</span><input name="username" autocomplete="username" required></label>
    <label class="field"><span>Password</span><input name="password" type="password" autocomplete="new-password" minlength="10" required></label>
    <p class="error-text" data-error></p>
    <button type="submit">Create account</button>`;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await sendJSON("/api/setup", "POST", {username: data.get("username"), password: data.get("password")});
      sessionState = await getJSON("/api/session");
      showAccountChrome();
      await refreshStatus();
      if (!pollTimer) pollTimer = window.setInterval(refreshStatus, 5000);
      render();
    } catch (error) {
      form.querySelector("[data-error]").textContent = error.message;
    }
  });
  app.appendChild(form);
}

async function signOut() {
  await sendJSON("/api/logout", "POST");
  sessionState = {authenticated: false, needsSetup: false};
  status = null;
  window.clearInterval(pollTimer);
  pollTimer = 0;
  stopLive();
  renderLogin();
}

function hideAccountChrome() {
  document.querySelector("#settings-button").hidden = true;
  document.querySelector("#logout-button").hidden = true;
}

async function renderSettings() {
  app.innerHTML = `<section class="settings"><p>Loading settings…</p></section>`;
  let data;
  try {
    data = await getJSON("/api/settings");
  } catch (error) {
    app.innerHTML = `<p class="error-text"></p>`;
    app.querySelector("p").textContent = error.message;
    return;
  }
  const root = document.createElement("div");
  root.className = "settings";
  const cards = [recordingCard(data), mqttCard(data), accountCard(), camerasCard(data)];
  if (data.users) cards.splice(2, 0, usersCard(data));
  root.append(...cards);
  app.innerHTML = "";
  app.appendChild(root);
}

function recordingMode(policy) {
  if (policy.limitHours && policy.onlyMotion) return "hours_motion";
  if (policy.limitHours) return "hours";
  if (policy.onlyMotion) return "motion";
  return "always";
}

function recordingCard(data) {
  const policy = data.recording;
  const mode = recordingMode(policy);
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `
    <h2>Recording</h2>
    <label class="choice"><input type="radio" name="mode" value="always" ${mode === "always" ? "checked" : ""}> 24/7, keep everything</label>
    <label class="choice"><input type="radio" name="mode" value="hours" ${mode === "hours" ? "checked" : ""}> Keep everything, only during these hours</label>
    <label class="choice"><input type="radio" name="mode" value="motion" ${mode === "motion" ? "checked" : ""}> 24/7, keep only pieces with motion</label>
    <label class="choice"><input type="radio" name="mode" value="hours_motion" ${mode === "hours_motion" ? "checked" : ""}> Only during these hours, and only pieces with motion</label>
    <p class="help">Motion modes still record continuously, then delete a 15-second piece if nothing in it crosses the percent below. The start of a movement stays in that piece.</p>
    <label class="field"><span>Timezone</span><input name="timezone" type="text" value="${escapeAttr(data.timezone)}"></label>
    <label class="field"><span>Recording folder</span><input name="recordingsDir" type="text" value="${escapeAttr(data.recordingsDir)}" placeholder="/mnt/recordings"></label>
    <p class="help">New footage is written here. Clips already saved stay where they are and can still be played.</p>
    <div data-hours>
      <div class="row">
        <label class="field"><span>From</span><input name="start" type="time" value="${escapeAttr(policy.start)}"></label>
        <label class="field"><span>Until</span><input name="end" type="time" value="${escapeAttr(policy.end)}"></label>
      </div>
      <div class="days" data-days></div>
    </div>
    <label class="field"><span>Motion is motion at or above this percent</span><input name="motionPercent" type="number" min="1" max="100" value="${policy.motionPercent}"></label>
    <label class="field"><span>Keep recordings (days)</span><input name="retainDays" type="number" min="1" value="${policy.retainDays}"></label>
    <label class="field"><span>Always leave this much disk free (GB)</span><input name="minFreeGb" type="number" min="1" value="${policy.minFreeGb}"></label>
    <p class="error-text" data-error></p>
    <button type="button">Save recording</button>`;
  const days = section.querySelector("[data-days]");
  DAY_NAMES.forEach((label, index) => {
    const item = document.createElement("label");
    item.innerHTML = `<input type="checkbox" value="${index}" ${policy.days.includes(index) ? "checked" : ""}> ${label}`;
    days.appendChild(item);
  });
  const hours = section.querySelector("[data-hours]");
  const syncHours = () => {
    const selected = section.querySelector('[name="mode"]:checked').value;
    hours.hidden = selected !== "hours" && selected !== "hours_motion";
  };
  section.querySelectorAll('[name="mode"]').forEach((input) => input.addEventListener("change", syncHours));
  syncHours();
  section.querySelector("button").addEventListener("click", async () => {
    const error = section.querySelector("[data-error]");
    error.textContent = "";
    const selected = section.querySelector('[name="mode"]:checked').value;
    try {
      await sendJSON("/api/settings/timezone", "PUT", {timezone: section.querySelector('[name="timezone"]').value.trim()});
      await sendJSON("/api/settings/storage", "PUT", {recordingsDir: section.querySelector('[name="recordingsDir"]').value.trim()});
      await sendJSON("/api/settings/recording", "PUT", {
        limitHours: selected === "hours" || selected === "hours_motion",
        onlyMotion: selected === "motion" || selected === "hours_motion",
        start: section.querySelector('[name="start"]').value,
        end: section.querySelector('[name="end"]').value,
        motionPercent: Number(section.querySelector('[name="motionPercent"]').value),
        retainDays: Number(section.querySelector('[name="retainDays"]').value),
        minFreeGb: Number(section.querySelector('[name="minFreeGb"]').value),
        days: [...days.querySelectorAll("input:checked")].map((input) => Number(input.value)),
      });
      error.textContent = "Saved.";
      if (status) status.timezone = section.querySelector('[name="timezone"]').value.trim();
    } catch (err) {
      error.textContent = err.message;
    }
  });
  return section;
}

function mqttCard(data) {
  const mqtt = data.mqtt;
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `
    <h2>Home Assistant</h2>
    <p class="help">Each camera, and each zone, becomes a motion sensor. In Home Assistant, add the MQTT integration and point it at the same broker.</p>
    <label><input name="enabled" type="checkbox" ${mqtt.enabled ? "checked" : ""}> Publish motion</label>
    <label class="field"><span>Broker</span><input name="host" type="text" value="${escapeAttr(mqtt.host)}" placeholder="10.0.0.5"></label>
    <label class="field"><span>Port</span><input name="port" type="number" value="${mqtt.port}"></label>
    <label class="field"><span>Username</span><input name="username" type="text" value="${escapeAttr(mqtt.username)}"></label>
    <label class="field"><span>Password</span><input name="password" type="password" placeholder="${mqtt.passwordSet ? "Saved — leave blank to keep it" : ""}"></label>
    <label class="field"><span>Topic prefix</span><input name="prefix" type="text" value="${escapeAttr(mqtt.prefix)}"></label>
    <label class="field"><span>Stay on for this many seconds after motion stops</span><input name="holdSeconds" type="number" min="1" max="300" value="${mqtt.holdSeconds}"></label>
    <p class="error-text" data-error></p>
    <button type="button">Save Home Assistant</button>`;
  section.querySelector("button").addEventListener("click", async () => {
    const error = section.querySelector("[data-error]");
    try {
      await sendJSON("/api/settings/mqtt", "PUT", {
        enabled: section.querySelector('[name="enabled"]').checked,
        host: section.querySelector('[name="host"]').value.trim(),
        port: Number(section.querySelector('[name="port"]').value),
        username: section.querySelector('[name="username"]').value,
        password: section.querySelector('[name="password"]').value,
        prefix: section.querySelector('[name="prefix"]').value.trim(),
        holdSeconds: Number(section.querySelector('[name="holdSeconds"]').value),
      });
      error.textContent = "Saved.";
    } catch (err) {
      error.textContent = err.message;
    }
  });
  return section;
}

function usersCard(data) {
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `<h2>Users</h2><p class="help">Owners can change settings and see every camera. Other people only get the cameras and the live or playback access you tick.</p><div data-list></div>`;
  const list = section.querySelector("[data-list]");
  for (const user of data.users) list.appendChild(userEditor(user, data.cameras));
  const add = document.createElement("form");
  add.className = "camera-edit";
  add.append(userFields(null, data.cameras));
  const error = document.createElement("p");
  error.className = "error-text";
  const button = document.createElement("button");
  button.type = "submit";
  button.textContent = "Add user";
  add.append(error, button);
  add.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      await sendJSON("/api/settings/users", "POST", userPayload(add));
      renderSettings();
    } catch (err) {
      error.textContent = err.message;
    }
  });
  section.appendChild(add);
  return section;
}

function userEditor(user, cameras) {
  const block = document.createElement("form");
  block.className = "camera-edit";
  block.append(userFields(user, cameras));
  const error = document.createElement("p");
  error.className = "error-text";
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Save user";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Delete user";
  const row = document.createElement("div");
  row.className = "row";
  row.append(save, remove);
  block.append(error, row);
  block.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      await sendJSON(`/api/settings/users/${encodeURIComponent(user.username)}`, "PUT", userPayload(block, false));
      error.textContent = "Saved.";
    } catch (err) {
      error.textContent = err.message;
    }
  });
  remove.addEventListener("click", async () => {
    if (!confirm(`Delete ${user.username}?`)) return;
    try {
      await sendJSON(`/api/settings/users/${encodeURIComponent(user.username)}`, "DELETE");
      renderSettings();
    } catch (err) {
      error.textContent = err.message;
    }
  });
  return block;
}

function userFields(user, cameras) {
  const wrap = document.createElement("div");
  const owner = user && user.role === "owner";
  wrap.innerHTML = `
    <h3></h3>
    ${user ? "" : `<label class="field"><span>Username</span><input name="username" required></label>`}
    <label class="field"><span>${user ? "New password" : "Password"}</span><input name="password" type="password" autocomplete="new-password" ${user ? "" : "required"} placeholder="${user ? "Leave blank to keep the current password" : ""}"></label>
    <label class="choice"><input name="role" type="checkbox" ${owner ? "checked" : ""}> Owner</label>
    <label class="choice"><input name="canLive" type="checkbox" ${!user || user.canLive ? "checked" : ""}> Can watch live</label>
    <label class="choice"><input name="canPlayback" type="checkbox" ${!user || user.canPlayback ? "checked" : ""}> Can watch playback</label>
    <label class="choice"><input name="canSettings" type="checkbox" ${owner || (user && user.canSettings) ? "checked" : ""}> Can change settings</label>
    <div class="days" data-cameras></div>`;
  wrap.querySelector("h3").textContent = user ? user.username : "New user";
  const picks = wrap.querySelector("[data-cameras]");
  for (const camera of cameras) {
    const label = document.createElement("label");
    const checked = !user || owner || user.cameras.includes(camera.id);
    label.innerHTML = `<input type="checkbox" value="${escapeAttr(camera.id)}" ${checked ? "checked" : ""}> ${escapeAttr(camera.name)}`;
    picks.appendChild(label);
  }
  return wrap;
}

function userPayload(form, requirePassword = true) {
  const role = form.querySelector('[name="role"]').checked ? "owner" : "user";
  const payload = {
    role,
    canLive: form.querySelector('[name="canLive"]').checked,
    canPlayback: form.querySelector('[name="canPlayback"]').checked,
    canSettings: form.querySelector('[name="canSettings"]').checked,
    cameras: [...form.querySelectorAll("[data-cameras] input:checked")].map((input) => input.value),
    password: form.querySelector('[name="password"]').value,
  };
  const username = form.querySelector('[name="username"]');
  if (username) payload.username = username.value.trim();
  if (requirePassword && !payload.password) payload.password = "";
  return payload;
}

function accountCard() {
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `
    <h2>Account</h2>
    <label class="field"><span>Current password</span><input name="current" type="password" autocomplete="current-password"></label>
    <label class="field"><span>New password</span><input name="new" type="password" autocomplete="new-password" minlength="10"></label>
    <p class="error-text" data-error></p>
    <button type="button">Change password</button>`;
  section.querySelector("button").addEventListener("click", async () => {
    const error = section.querySelector("[data-error]");
    try {
      await sendJSON("/api/account/password", "POST", {
        current: section.querySelector('[name="current"]').value,
        new: section.querySelector('[name="new"]').value,
      });
      error.textContent = "Password changed.";
    } catch (err) {
      error.textContent = err.message;
    }
  });
  return section;
}

function camerasCard(data) {
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `<h2>Cameras</h2><div data-list></div>`;
  const list = section.querySelector("[data-list]");
  for (const camera of data.cameras) list.appendChild(cameraEditor(camera));
  const add = document.createElement("form");
  add.innerHTML = `
    <h3>Add a camera</h3>
    <label class="field"><span>Name</span><input name="name" required></label>
    <label class="field"><span>Id</span><input name="id" placeholder="living_room"></label>
    <label class="field"><span>RTSP address</span><input name="url" type="text" placeholder="rtsp://user:password@10.0.0.20:554/stream1" required></label>
    <p class="error-text" data-error></p>
    <button type="submit">Add camera</button>`;
  add.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(add);
    try {
      await sendJSON("/api/settings/cameras", "POST", {
        name: form.get("name"),
        id: form.get("id"),
        url: form.get("url"),
      });
      await refreshStatus();
      renderSettings();
    } catch (error) {
      add.querySelector("[data-error]").textContent = error.message;
    }
  });
  section.appendChild(add);
  return section;
}

function cameraEditor(camera) {
  const block = document.createElement("div");
  block.className = "camera-edit";
  block.innerHTML = `
    <h3></h3>
    <label class="field"><span>Name</span><input name="name" value="${escapeAttr(camera.name)}"></label>
    <label class="field"><span>RTSP address</span><input name="url" type="password" value="${escapeAttr(camera.url)}" autocomplete="off"></label>
    <p class="help">Zones limit motion, recording-on-motion, and Home Assistant to part of the picture. Leave none to use the whole frame.</p>
    <div class="snapshot-wrap"><img alt=""><canvas></canvas></div>
    <div class="zone-list" data-zones></div>
    <div class="row">
      <input name="zoneName" placeholder="Zone name">
      <button type="button" data-draw>Draw zone</button>
      <button type="button" data-save>Save camera</button>
      <button type="button" data-delete>Remove</button>
    </div>
    <p class="error-text" data-error></p>`;
  block.querySelector("h3").textContent = camera.name;
  const img = block.querySelector("img");
  const canvas = block.querySelector("canvas");
  img.src = `/api/cameras/${encodeURIComponent(camera.id)}/snapshot.jpg?t=${Date.now()}`;
  let zones = camera.zones.slice();
  let draft = null;
  let drawing = false;
  const paint = () => {
    const width = img.clientWidth;
    const height = img.clientHeight;
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = "#e2b15a";
    ctx.lineWidth = 2;
    for (const zone of zones.concat(draft ? [draft] : [])) {
      ctx.strokeRect(zone.x * width, zone.y * height, zone.w * width, zone.h * height);
    }
  };
  img.addEventListener("load", paint);
  const list = block.querySelector("[data-zones]");
  const drawList = () => {
    list.innerHTML = "";
    for (const zone of zones) {
      const row = document.createElement("div");
      row.className = "row";
      const label = document.createElement("span");
      label.textContent = zone.name;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Delete zone";
      button.addEventListener("click", () => {
        zones = zones.filter((item) => item.id !== zone.id);
        drawList();
        paint();
      });
      row.append(label, button);
      list.appendChild(row);
    }
  };
  drawList();
  canvas.addEventListener("pointerdown", (event) => {
    if (!drawing) return;
    const rect = canvas.getBoundingClientRect();
    draft = {x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height, w: 0, h: 0};
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!draft) return;
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    draft.w = x - draft.x;
    draft.h = y - draft.y;
    paint();
  });
  canvas.addEventListener("pointerup", () => {
    if (!draft) return;
    const name = block.querySelector('[name="zoneName"]').value.trim();
    const zone = normalizeZone(draft);
    draft = null;
    drawing = false;
    if (!name || zone.w < 0.02 || zone.h < 0.02) {
      paint();
      return;
    }
    zones.push({id: slug(name), name, ...zone});
    drawList();
    paint();
  });
  block.querySelector("[data-draw]").addEventListener("click", () => {
    drawing = true;
    block.querySelector("[data-error]").textContent = "Drag a rectangle on the picture.";
  });
  block.querySelector("[data-save]").addEventListener("click", async () => {
    const error = block.querySelector("[data-error]");
    try {
      await sendJSON(`/api/settings/cameras/${encodeURIComponent(camera.id)}`, "PUT", {
        name: block.querySelector('[name="name"]').value,
        url: block.querySelector('[name="url"]').value,
      });
      await sendJSON(`/api/settings/cameras/${encodeURIComponent(camera.id)}/zones`, "PUT", {zones});
      error.textContent = "Saved.";
      await refreshStatus();
    } catch (err) {
      error.textContent = err.message;
    }
  });
  block.querySelector("[data-delete]").addEventListener("click", async () => {
    if (!confirm(`Remove ${camera.name}? Recordings already on disk stay there.`)) return;
    try {
      await sendJSON(`/api/settings/cameras/${encodeURIComponent(camera.id)}`, "DELETE");
      await refreshStatus();
      renderSettings();
    } catch (err) {
      block.querySelector("[data-error]").textContent = err.message;
    }
  });
  return block;
}

function normalizeZone(zone) {
  const x = Math.min(zone.x, zone.x + zone.w);
  const y = Math.min(zone.y, zone.y + zone.h);
  return {
    x: clamp(x, 0, 1),
    y: clamp(y, 0, 1),
    w: clamp(Math.abs(zone.w), 0, 1 - x),
    h: clamp(Math.abs(zone.h), 0, 1 - y),
  };
}

function slug(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "zone";
}

function escapeAttr(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}
