"use strict";

const app = document.querySelector("#app");
const summary = document.querySelector("#summary");
const disk = document.querySelector("#disk");
const banner = document.querySelector("#banner");

let status = null;
let generation = 0;
let peers = [];
let pollTimer = 0;
let timelineTimer = 0;
let timelineState = null;
let liveWanted = true;
let sessionState = null;

document.addEventListener("DOMContentLoaded", boot);
window.addEventListener("hashchange", render);

async function boot() {
  document.querySelector("#settings-button").addEventListener("click", () => {
    location.hash = "#/settings";
  });
  document.querySelector("#logout-button").addEventListener("click", signOut);
  try {
    sessionState = await getJSON("/api/session");
  } catch (error) {
    summary.textContent = error.message;
    return;
  }
  if (sessionState.needsSetup) {
    renderSetup();
    return;
  }
  if (!sessionState.authenticated) {
    renderLogin();
    return;
  }
  showAccountChrome();
  await refreshStatus();
  render();
  pollTimer = window.setInterval(refreshStatus, 5000);
  document.addEventListener("keydown", onKey);
}

function showAccountChrome() {
  document.querySelector("#settings-button").hidden = !sessionState.canSettings;
  document.querySelector("#logout-button").hidden = false;
  summary.textContent = sessionState.username ? `Signed in as ${sessionState.username}` : "Connecting…";
}

async function refreshStatus() {
  try {
    const next = await getJSON("/api/status");
    status = next;
    paintChrome();
    paintTileStates();
  } catch (error) {
    summary.textContent = error.message;
  }
}

function paintChrome() {
  if (!status) return;
  const cameras = status.cameras || [];
  const online = cameras.filter((camera) => camera.online).length;
  const recording = cameras.filter((camera) => camera.recording).length;
  summary.textContent = `${recording} recording · ${online} live`;
  disk.textContent = `${status.diskFreeGb} GB free · keeping ${status.retainDays} days`;
  if (status.message) {
    banner.hidden = false;
    banner.textContent = status.message;
  } else {
    banner.hidden = true;
  }
}

function selectedId() {
  const hash = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  if (!status || !hash) return null;
  return status.cameras.some((camera) => camera.id === hash) ? hash : null;
}

function render() {
  if (!sessionState || !sessionState.authenticated) return;
  if (!status) return;
  liveWanted = true;
  stopLive();
  window.clearInterval(timelineTimer);
  timelineState = null;
  if (location.hash === "#/settings") {
    renderSettings();
    return;
  }
  const id = selectedId();
  if (!id) renderGrid();
  else renderCamera(id);
}

function renderGrid() {
  app.innerHTML = "";
  if (!status.cameras.length) {
    app.innerHTML = `<section class="empty"><h2>No cameras yet</h2><p>Add one in Settings. The address starts with rtsp://</p></section>`;
    return;
  }
  const grid = document.createElement("div");
  grid.className = "grid";
  const token = ++generation;
  for (const camera of status.cameras) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "tile";
    tile.dataset.id = camera.id;
    tile.innerHTML = `
      <div class="frame">
        <video autoplay playsinline muted></video>
        <p class="overlay">Connecting…</p>
      </div>
      <div class="meta"><h2></h2><span class="dot"></span></div>`;
    tile.querySelector("h2").textContent = camera.name;
    tile.addEventListener("click", () => {
      location.hash = "#/" + encodeURIComponent(camera.id);
    });
    grid.appendChild(tile);
    const video = tile.querySelector("video");
    const overlay = tile.querySelector(".overlay");
    keepLive(video, camera.id, token, overlay);
  }
  app.appendChild(grid);
  paintTileStates();
}

function paintTileStates() {
  if (!status) return;
  for (const camera of status.cameras) {
    const tile = app.querySelector(`.tile[data-id="${CSS.escape(camera.id)}"]`);
    if (!tile) continue;
    const dot = tile.querySelector(".dot");
    dot.className = "dot";
    if (camera.error) dot.classList.add("bad");
    else if (camera.recording) dot.classList.add("rec");
    else if (camera.online) dot.classList.add("on");
    const overlay = tile.querySelector(".overlay");
    if (camera.asleep) overlay.textContent = "Outside recording hours";
    else if (camera.error && !tile.querySelector("video").srcObject) overlay.textContent = camera.error;
  }
}

function renderCamera(id) {
  const camera = status.cameras.find((item) => item.id === id);
  app.innerHTML = "";
  const switcher = document.createElement("div");
  switcher.className = "switcher";
  const all = document.createElement("button");
  all.type = "button";
  all.textContent = "All cameras";
  all.addEventListener("click", () => {
    location.hash = "";
  });
  switcher.appendChild(all);
  for (const item of status.cameras) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item.name;
    button.setAttribute("aria-pressed", item.id === id ? "true" : "false");
    button.addEventListener("click", () => {
      location.hash = "#/" + encodeURIComponent(item.id);
    });
    switcher.appendChild(button);
  }

  const stage = document.createElement("div");
  stage.className = "stage";
  stage.innerHTML = `
    <video id="live-video" autoplay playsinline muted></video>
    <video id="play-video" playsinline></video>
    <p class="overlay" id="stage-note"></p>`;

  const transport = document.createElement("div");
  transport.className = "transport";
  transport.innerHTML = `
    <button type="button" id="live-button" aria-pressed="true">Live</button>
    <button type="button" id="play-button" hidden>Pause</button>
    <label>Day <input id="day" type="date"></label>
    <button type="button" id="hour-button">This hour</button>
    <button type="button" id="day-button">Full day</button>
    <div class="readout" id="readout" aria-live="polite"><strong>—</strong><span>Move along the timeline</span></div>`;

  const wrap = document.createElement("div");
  wrap.className = "timeline-wrap";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "Recording timeline. Move the pointer to read motion. Click to play that moment.");
  canvas.tabIndex = 0;
  wrap.appendChild(canvas);
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "Bright marks are movement. Scroll to zoom, drag to move, click to play.";

  app.append(switcher, stage, transport, wrap, hint);

  const dayInput = transport.querySelector("#day");
  const today = calendarToday(status.timezone);
  dayInput.value = today;
  const token = ++generation;
  timelineState = {
    id,
    token,
    timezone: status.timezone,
    day: today,
    dayStart: 0,
    dayEnd: 0,
    viewStart: 0,
    viewEnd: 0,
    segments: [],
    motion: new Map(),
    mode: "live",
    playStart: 0,
    hover: null,
    canvas,
    dragging: false,
    moved: false,
    anchorX: 0,
    anchorStart: 0,
  };
  setDay(today);
  const liveVideo = stage.querySelector("#live-video");
  const playVideo = stage.querySelector("#play-video");
  playVideo.hidden = true;
  keepLive(liveVideo, id, token, stage.querySelector("#stage-note"));

  transport.querySelector("#live-button").addEventListener("click", () => goLive());
  transport.querySelector("#play-button").addEventListener("click", togglePause);
  transport.querySelector("#day-button").addEventListener("click", () => {
    timelineState.viewStart = timelineState.dayStart;
    timelineState.viewEnd = timelineState.dayEnd;
    drawTimeline();
  });
  transport.querySelector("#hour-button").addEventListener("click", zoomHour);
  dayInput.addEventListener("change", () => setDay(dayInput.value));

  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointerleave", () => {
    if (!timelineState) return;
    timelineState.hover = null;
    drawTimeline();
    showReadout(playbackMs() || Date.now());
  });
  canvas.addEventListener("wheel", onWheel, {passive: false});
  canvas.addEventListener("dblclick", () => {
    timelineState.viewStart = timelineState.dayStart;
    timelineState.viewEnd = timelineState.dayEnd;
    drawTimeline();
  });
  playVideo.addEventListener("timeupdate", () => {
    if (!timelineState || timelineState.mode !== "play" || timelineState.hover != null) return;
    drawTimeline();
    showReadout(playbackMs());
  });
  playVideo.addEventListener("ended", playNext);
  timelineTimer = window.setInterval(() => loadTimeline(false), 4000);
  loadTimeline(true);
}

function setDay(day) {
  const start = unixAtZone(day, "00:00:00", status.timezone);
  const end = unixAtZone(addDays(day, 1), "00:00:00", status.timezone);
  timelineState.day = day;
  timelineState.dayStart = start;
  timelineState.dayEnd = end;
  timelineState.viewStart = start;
  timelineState.viewEnd = end;
  loadTimeline(true);
}

async function loadTimeline(redrawNow) {
  if (!timelineState) return;
  const state = timelineState;
  try {
    const data = await getJSON(
      `/api/cameras/${encodeURIComponent(state.id)}/timeline?start=${state.dayStart}&end=${state.dayEnd}`
    );
    if (!timelineState || timelineState.token !== state.token) return;
    state.segments = data.segments || [];
    state.motion = new Map((data.motion || []).map((point) => [point.t, point.level]));
    if (redrawNow || state.hover == null) drawTimeline();
  } catch (error) {
    const note = document.querySelector("#stage-note");
    if (note) note.textContent = error.message;
  }
}

function goLive() {
  if (!timelineState) return;
  timelineState.mode = "live";
  liveWanted = true;
  const playVideo = document.querySelector("#play-video");
  const liveVideo = document.querySelector("#live-video");
  const note = document.querySelector("#stage-note");
  if (playVideo) {
    playVideo.pause();
    playVideo.removeAttribute("src");
    playVideo.load();
    playVideo.hidden = true;
  }
  if (liveVideo) liveVideo.hidden = false;
  const liveButton = document.querySelector("#live-button");
  const playButton = document.querySelector("#play-button");
  if (liveButton) liveButton.setAttribute("aria-pressed", "true");
  if (playButton) playButton.hidden = true;
  if (note) note.textContent = "";
  drawTimeline();
}

function playAt(ms) {
  const note = document.querySelector("#stage-note");
  if (sessionState && sessionState.canPlayback === false) {
    if (note) note.textContent = "Playback is off for this account.";
    return;
  }
  const segment = segmentAt(timelineState.segments, ms, Date.now());
  if (!segment) {
    if (note) note.textContent = "Nothing was recorded at that moment.";
    return;
  }
  if (!segment.ready) {
    if (note) note.textContent = "That part is still being saved. Playback starts a few seconds after it is recorded.";
    return;
  }
  timelineState.mode = "play";
  timelineState.playStart = segment.startMs;
  liveWanted = false;
  closePeers();
  const liveVideo = document.querySelector("#live-video");
  const playVideo = document.querySelector("#play-video");
  if (liveVideo) liveVideo.hidden = true;
  playVideo.hidden = false;
  let started = false;
  const start = () => {
    if (started) return;
    started = true;
    const offset = Math.max(0, (ms - segment.startMs) / 1000);
    const max = Math.max(0, (playVideo.duration || offset) - 0.05);
    playVideo.currentTime = Math.min(offset, max);
    const attempt = playVideo.play();
    if (attempt) {
      attempt.catch(() => {
        playVideo.muted = true;
        playVideo.play();
      });
    }
  };
  playVideo.onerror = () => {
    if (note) note.textContent = "This recording could not be played.";
  };
  playVideo.onloadedmetadata = start;
  if (playVideo.getAttribute("src") === segment.url && playVideo.readyState >= 1) start();
  else playVideo.src = segment.url;
  document.querySelector("#live-button").setAttribute("aria-pressed", "false");
  const playButton = document.querySelector("#play-button");
  playButton.hidden = false;
  playButton.textContent = "Pause";
  if (note) note.textContent = "";
  drawTimeline();
}

function togglePause() {
  const video = document.querySelector("#play-video");
  const button = document.querySelector("#play-button");
  if (!video || video.hidden) return;
  if (video.paused) {
    video.play();
    button.textContent = "Pause";
  } else {
    video.pause();
    button.textContent = "Play";
  }
}

function playNext() {
  if (!timelineState || timelineState.mode !== "play") return;
  const video = document.querySelector("#play-video");
  const currentEnd = timelineState.playStart + Math.round((video.duration || 0) * 1000);
  const next = nextReady(timelineState.segments, currentEnd);
  if (!next) {
    const note = document.querySelector("#stage-note");
    if (note) note.textContent = "Gap in the recording.";
    return;
  }
  playAt(next.startMs);
}

function playbackMs() {
  if (!timelineState || timelineState.mode !== "play") return null;
  const video = document.querySelector("#play-video");
  if (!video || !video.duration && video.readyState < 1) return timelineState.playStart;
  return timelineState.playStart + Math.round((video.currentTime || 0) * 1000);
}

function zoomHour() {
  const center = playbackMs() || Math.min(Date.now(), timelineState.dayEnd - 1);
  const span = 60 * 60 * 1000;
  timelineState.viewStart = Math.max(timelineState.dayStart, center - span / 2);
  timelineState.viewEnd = Math.min(timelineState.dayEnd, timelineState.viewStart + span);
  if (timelineState.viewEnd - timelineState.viewStart < span) {
    timelineState.viewStart = Math.max(timelineState.dayStart, timelineState.viewEnd - span);
  }
  drawTimeline();
}

function drawTimeline() {
  const state = timelineState;
  if (!state) return;
  const canvas = state.canvas;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 10) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.round(112 * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = rect.width;
  const height = 112;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#1c1916";
  ctx.fillRect(0, 0, width, height);

  const span = state.viewEnd - state.viewStart;
  const now = Date.now();
  const xAt = (ms) => ((ms - state.viewStart) / span) * width;
  const barTop = 28;
  const barBottom = 96;

  ctx.fillStyle = "rgba(226, 177, 90, 0.08)";
  for (const segment of state.segments) {
    const end = segment.endMs == null ? now : segment.endMs;
    const x0 = clamp(xAt(segment.startMs), 0, width);
    const x1 = clamp(xAt(end), 0, width);
    if (x1 > x0) ctx.fillRect(x0, barTop, x1 - x0, barBottom - barTop);
  }

  const pixels = Math.max(1, Math.floor(width));
  for (let x = 0; x < pixels; x++) {
    const a = state.viewStart + (x / pixels) * span;
    const b = state.viewStart + ((x + 1) / pixels) * span;
    let max = 0;
    let covered = false;
    const first = Math.floor(a / 1000);
    const last = Math.floor((b - 1) / 1000);
    for (let sec = first; sec <= last; sec++) {
      const mid = sec * 1000 + 500;
      if (segmentAt(state.segments, mid, now)) covered = true;
      max = Math.max(max, state.motion.get(sec) || 0);
    }
    if (!covered) continue;
    const barHeight = Math.max(2, (max / 100) * (barBottom - barTop));
    ctx.fillStyle = max > 0 ? levelColor(max) : "#4a433b";
    ctx.fillRect(x, barBottom - barHeight, 1, barHeight);
  }

  ctx.strokeStyle = "#6d645b";
  ctx.fillStyle = "#a3988c";
  ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
  ctx.lineWidth = 1;
  const step = tickStep(span / 1000) * 1000;
  for (let t = state.dayStart; t <= state.dayEnd; t += step) {
    if (t < state.viewStart || t > state.viewEnd) continue;
    const x = xAt(t);
    ctx.beginPath();
    ctx.moveTo(x, 22);
    ctx.lineTo(x, barBottom);
    ctx.stroke();
    ctx.fillText(formatTick(t, state.timezone, step / 1000), x + 4, 16);
  }

  if (now >= state.viewStart && now <= state.viewEnd && calendarContains(state.day, now, state.timezone)) {
    ctx.strokeStyle = "rgba(243, 239, 230, 0.85)";
    ctx.beginPath();
    ctx.moveTo(xAt(now), barTop);
    ctx.lineTo(xAt(now), barBottom);
    ctx.stroke();
  }

  const playhead = playbackMs();
  if (playhead != null && playhead >= state.viewStart && playhead <= state.viewEnd) {
    ctx.strokeStyle = "#e2b15a";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(xAt(playhead), barTop - 4);
    ctx.lineTo(xAt(playhead), barBottom);
    ctx.stroke();
  }

  if (state.hover != null) {
    ctx.strokeStyle = "#f3efe6";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(xAt(state.hover), barTop);
    ctx.lineTo(xAt(state.hover), barBottom);
    ctx.stroke();
  }
  const readoutAt = state.hover != null ? state.hover : (playbackMs() || Math.min(Date.now(), state.dayEnd - 1));
  showReadout(readoutAt);
}

function showReadout(ms) {
  const node = document.querySelector("#readout");
  if (!node || !timelineState || ms == null) return;
  const kind = describe(ms, timelineState.segments, timelineState.motion, Date.now());
  node.querySelector("strong").textContent = formatClock(ms, timelineState.timezone);
  const detail = node.querySelector("span");
  if (kind.kind === "gap") detail.textContent = "No recording";
  else if (kind.kind === "saving") detail.textContent = kind.level ? `Saving · motion ${kind.level}%` : "Saving this segment";
  else if (kind.kind === "still") detail.textContent = "Still";
  else detail.textContent = `Motion ${kind.level}%`;
}

function onPointerDown(event) {
  timelineState.dragging = true;
  timelineState.moved = false;
  timelineState.anchorX = event.clientX;
  timelineState.anchorStart = timelineState.viewStart;
  event.currentTarget.setPointerCapture(event.pointerId);
}

function onPointerMove(event) {
  if (!timelineState) return;
  const ms = msAt(event);
  timelineState.hover = ms;
  if (timelineState.dragging) {
    const dx = event.clientX - timelineState.anchorX;
    if (Math.abs(dx) > 4) timelineState.moved = true;
    if (timelineState.moved) {
      const width = timelineState.canvas.getBoundingClientRect().width;
      const span = timelineState.viewEnd - timelineState.viewStart;
      const shift = (-dx / width) * span;
      let start = timelineState.anchorStart + shift;
      let end = start + span;
      if (start < timelineState.dayStart) {
        end += timelineState.dayStart - start;
        start = timelineState.dayStart;
      }
      if (end > timelineState.dayEnd) {
        start -= end - timelineState.dayEnd;
        end = timelineState.dayEnd;
      }
      timelineState.viewStart = Math.max(timelineState.dayStart, start);
      timelineState.viewEnd = Math.min(timelineState.dayEnd, end);
    }
  }
  drawTimeline();
  showReadout(ms);
}

function onPointerUp(event) {
  if (!timelineState) return;
  const wasDrag = timelineState.moved;
  timelineState.dragging = false;
  if (!wasDrag) playAt(msAt(event));
}

function onWheel(event) {
  event.preventDefault();
  const state = timelineState;
  const ms = msAt(event);
  const factor = event.deltaY > 0 ? 1.25 : 0.8;
  let span = (state.viewEnd - state.viewStart) * factor;
  const minSpan = 10 * 60 * 1000;
  const maxSpan = state.dayEnd - state.dayStart;
  span = clamp(span, minSpan, maxSpan);
  const width = state.canvas.getBoundingClientRect().width;
  const rel = clamp((event.clientX - state.canvas.getBoundingClientRect().left) / width, 0, 1);
  let start = ms - rel * span;
  let end = start + span;
  if (start < state.dayStart) {
    end += state.dayStart - start;
    start = state.dayStart;
  }
  if (end > state.dayEnd) {
    start -= end - state.dayEnd;
    end = state.dayEnd;
  }
  state.viewStart = start;
  state.viewEnd = end;
  state.hover = ms;
  drawTimeline();
  showReadout(ms);
}

function onKey(event) {
  if (event.key === "Escape" && location.hash && !["INPUT", "TEXTAREA"].includes(event.target.tagName)) {
    location.hash = "";
    return;
  }
  if (!timelineState || timelineState.mode !== "play") return;
  if (event.target && ["INPUT", "BUTTON"].includes(event.target.tagName)) return;
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const delta = (event.key === "ArrowLeft" ? -5 : 5) * 1000;
    playAt((playbackMs() || timelineState.viewStart) + delta);
  } else if (event.key === " ") {
    event.preventDefault();
    togglePause();
  }
}

function msAt(event) {
  const rect = timelineState.canvas.getBoundingClientRect();
  const rel = clamp((event.clientX - rect.left) / rect.width, 0, 1);
  return Math.round(timelineState.viewStart + rel * (timelineState.viewEnd - timelineState.viewStart));
}

function describe(ms, segments, motion, now) {
  const segment = segmentAt(segments, ms, now);
  const level = motion.get(Math.floor(ms / 1000)) || 0;
  if (!segment) return {kind: "gap", level: 0};
  if (!segment.ready) return {kind: "saving", level};
  if (!level) return {kind: "still", level: 0};
  return {kind: "motion", level};
}

function segmentAt(segments, ms, now) {
  let found = null;
  for (const segment of segments) {
    const end = segment.endMs == null ? now : segment.endMs;
    if (ms >= segment.startMs && ms < end) {
      if (!found || segment.ready) found = segment;
    }
  }
  return found;
}

function nextReady(segments, afterMs) {
  let found = null;
  for (const segment of segments) {
    if (!segment.ready || segment.endMs == null) continue;
    if (segment.startMs < afterMs - 200) continue;
    if (!found || segment.startMs < found.startMs) found = segment;
  }
  if (!found || found.startMs - afterMs > 3000) return null;
  return found;
}

function keepLive(video, cameraId, token, overlay) {
  if (sessionState && sessionState.canLive === false) {
    if (overlay) overlay.textContent = "Live view is off for this account.";
    return;
  }
  let delay = 800;
  const run = async () => {
    while (token === generation) {
      if (!liveWanted) {
        await sleep(250);
        continue;
      }
      try {
        if (overlay && token === generation) overlay.textContent = "";
        const pc = await connectLive(video, cameraId, token);
        await ended(pc, token);
        try { pc.close(); } catch (_error) { /* already closed */ }
        delay = 800;
      } catch (error) {
        if (token !== generation) return;
        if (error.message === "cancelled") return;
        if (overlay) overlay.textContent = error.message || "No signal";
        await sleep(delay);
        delay = Math.min(8000, delay * 2);
      }
    }
  };
  run();
}

async function connectLive(video, cameraId, token) {
  const pc = new RTCPeerConnection({iceServers: []});
  peers.push(pc);
  pc.addTransceiver("video", {direction: "recvonly"});
  pc.addTransceiver("audio", {direction: "recvonly"});
  const tighten = () => {
    for (const receiver of pc.getReceivers()) {
      if ("playoutDelayHint" in receiver) receiver.playoutDelayHint = 0;
    }
  };
  pc.ontrack = (event) => {
    if (token !== generation) return;
    video.srcObject = event.streams[0];
    tighten();
  };
  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitIce(pc);
    if (token !== generation) {
      pc.close();
      throw new Error("cancelled");
    }
    const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/webrtc`, {
      method: "POST",
      headers: {"Content-Type": "application/sdp"},
      body: pc.localDescription.sdp,
    });
    if (!response.ok) {
      let detail = "This camera's live view is not ready yet.";
      try {
        const body = await response.json();
        if (body.detail) detail = body.detail;
      } catch (_error) {
        /* the status line is enough */
      }
      throw new Error(detail);
    }
    await pc.setRemoteDescription({type: "answer", sdp: await response.text()});
    tighten();
    return pc;
  } catch (error) {
    pc.close();
    throw error;
  }
}

function waitIce(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, 1200);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(timer);
        resolve();
      }
    });
  });
}

function ended(pc, token) {
  return new Promise((resolve) => {
    const timer = window.setInterval(() => {
      if (token !== generation || ["failed", "closed", "disconnected"].includes(pc.connectionState)) {
        window.clearInterval(timer);
        resolve();
      }
    }, 500);
  });
}

function closePeers() {
  for (const pc of peers) {
    try { pc.close(); } catch (_error) { /* already closed */ }
  }
  peers = [];
  for (const video of document.querySelectorAll("video")) {
    if (video.srcObject) video.srcObject = null;
  }
}

function stopLive() {
  generation += 1;
  closePeers();
}

async function getJSON(url) {
  const response = await fetch(url);
  if (response.status === 401 && url !== "/api/session") {
    sessionState = {authenticated: false, needsSetup: false};
    renderLogin();
    throw new Error("Sign in required.");
  }
  if (!response.ok) {
    let detail = `The recorder returned ${response.status}.`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch (_error) {
      /* keep the status */
    }
    throw new Error(detail);
  }
  return response.json();
}

async function sendJSON(url, method, body) {
  const response = await fetch(url, {
    method,
    headers: {"Content-Type": "application/json"},
    body: body == null ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) {
    sessionState = {authenticated: false, needsSetup: false};
    renderLogin();
    throw new Error("Sign in required.");
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (!response.ok) {
    throw new Error((payload && payload.detail) || `The recorder returned ${response.status}.`);
  }
  return payload;
}

function unixAtZone(date, time, timeZone) {
  let guess = Date.parse(`${date}T${time}Z`);
  const format = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const want = Date.parse(`${date}T${time}Z`);
  for (let i = 0; i < 3; i++) {
    const parts = Object.fromEntries(format.formatToParts(new Date(guess)).map((part) => [part.type, part.value]));
    const shown = Date.parse(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}Z`);
    guess += want - shown;
  }
  return guess;
}

function calendarToday(timeZone) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function calendarContains(day, ms, timeZone) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(ms)) === day;
}

function addDays(date, days) {
  const [year, month, day] = date.split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day));
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function formatClock(ms, timeZone) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone, hour: "numeric", minute: "2-digit", second: "2-digit",
  }).format(new Date(ms));
}

function formatTick(ms, timeZone, stepSeconds) {
  const options = {timeZone, hour: "numeric"};
  if (stepSeconds < 3600) options.minute = "2-digit";
  return new Intl.DateTimeFormat("en-US", options).format(new Date(ms));
}

function tickStep(spanSeconds) {
  if (spanSeconds <= 20 * 60) return 60;
  if (spanSeconds <= 2 * 3600) return 5 * 60;
  if (spanSeconds <= 6 * 3600) return 15 * 60;
  if (spanSeconds <= 12 * 3600) return 3600;
  return 2 * 3600;
}

function levelColor(level) {
  const t = clamp(level / 100, 0, 1);
  const red = Math.round(90 + t * 136);
  const green = Math.round(70 + t * 107);
  const blue = Math.round(50 + t * 40);
  return `rgb(${red}, ${green}, ${blue})`;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
