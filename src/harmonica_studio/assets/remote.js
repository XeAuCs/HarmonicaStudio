"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const dom = {
    connection: $("connection"), connectionLabel: $("connection-label"), notice: $("connection-notice"),
    previewMode: $("mode-preview"), gameMode: $("mode-game"), cover: $("score-cover"), scoreCanvas: $("score-canvas"),
    playerState: $("player-state"), title: $("song-title"), seek: $("seek"), seekLabel: $("seek-label"),
    gameProgress: $("game-progress"), gameFill: $("game-progress-fill"),
    position: $("position"), duration: $("duration"), play: $("play"), playLabel: $("play-label"),
    playIcon: $("play-icon"), stop: $("stop"), hint: $("mode-hint"), status: $("activity-status"),
    libraryCount: $("library-count"), refresh: $("refresh"), search: $("search"),
    clearSearch: $("clear-search"), empty: $("library-empty"), songs: $("song-list"), toast: $("toast"),
  };
  const STORAGE_KEY = "harmonica.remote.token";
  let token = null;
  try {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const scannedToken = fragment.get("token");
    if (scannedToken) {
      token = scannedToken;
      try { sessionStorage.setItem(STORAGE_KEY, token); } catch (_) { /* Private browsing can reject storage. */ }
    } else {
      try { token = sessionStorage.getItem(STORAGE_KEY); } catch (_) { /* The current scan still works without storage. */ }
    }
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  } catch (_) { token = null; }

  let state = null;
  let connected = false;
  let mode = "preview";
  let pollTimer = null;
  let pollController = null;
  let polling = false;
  let pollAgain = false;
  let failureCount = 0;
  let frame = null;
  let toastTimer = null;
  let dragging = false;
  let dragPosition = 0;
  let librarySignature = "";
  let library = [];
  let rows = [];
  let sample = { position: 0, duration: 0, running: false, correction: 0, at: performance.now() };
  let paintedSecond = -1;
  let paintedDuration = -1;
  let scoreId = null;
  let score = null;
  let scoreController = null;
  let scoreRetryTimer = null;
  let scoreRetryDelay = 0;
  let scoreGeneration = 0;
  let scoreNeeded = false;
  let scorePaintKey = "";
  let scoreSize = { width: 0, height: 0, ratio: 1 };
  const scoreContext = dom.scoreCanvas.getContext("2d");
  const scorePalette = { line: "#CFC8BB", accent: "#9F4937", muted: "#6E695F" };
  const pending = new Set();
  const setText = (element, value) => { if (element.textContent !== value) element.textContent = value; };
  const finite = (value) => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  const clamp = (value, maximum) => Math.min(Math.max(0, value), maximum);
  const safeText = (value) => {
    const text = typeof value === "string" ? value : "";
    return token ? text.split(token).join("[连接凭据]") : text;
  };
  const formatTime = (value) => {
    const seconds = Math.floor(finite(value));
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
  };
  const currentTransport = () => mode === "game" ? (state?.game || {}) : (state || {});
  const transportState = () => mode === "game" ? state?.game?.state : state?.transport;
  const transportRunning = () => transportState() === "playing";

  function showToast(message) {
    clearTimeout(toastTimer);
    dom.toast.textContent = safeText(message) || "操作未完成，请稍后重试。";
    dom.toast.hidden = false;
    toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 4200);
  }

  function setConnection(value, message = "") {
    const frozenPosition = sampledPosition();
    connected = value === "connected";
    dom.connection.dataset.state = value;
    setText(dom.connectionLabel, { connected: "已连接", connecting: "连接中", offline: "连接中断", expired: "需重新扫码" }[value] || "连接中断");
    setText(dom.notice, message);
    dom.notice.hidden = !message;
    if (!connected) {
      cancelScoreRequest();
      sample.position = frozenPosition;
      sample.running = false;
      sample.correction = 0;
      sample.at = performance.now();
      paintProgress();
    }
    updateControls();
  }

  function forgetToken() {
    token = null;
    try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) { /* Storage is optional. */ }
    clearTimeout(pollTimer);
    setConnection("expired", "连接已过期，请扫描电脑上最新的二维码。");
  }

  async function request(path, options = {}) {
    const controller = options.controller || new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(path, {
        method: options.body ? "POST" : "GET",
        headers: { "Authorization": `Bearer ${token}`, ...(options.body ? { "Content-Type": "application/json" } : {}) },
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
        cache: "no-store",
        credentials: "omit",
        referrerPolicy: "no-referrer",
      });
      if (response.status === 401 || response.status === 403) {
        forgetToken();
        throw new Error("连接已过期，请重新扫码。");
      }
      let body;
      try { body = await response.json(); } catch (_) { throw new Error("电脑返回了无法读取的信息，请稍后重试。"); }
      if (!response.ok || body?.ok === false) throw new Error(safeText(body?.message || body?.error) || "操作未完成，请稍后重试。");
      return body;
    } finally { clearTimeout(timeout); }
  }

  function schedulePoll(delay = 500) {
    clearTimeout(pollTimer);
    if (token && !document.hidden) pollTimer = setTimeout(pollState, delay);
  }

  async function pollState() {
    if (!token || document.hidden) return;
    if (polling) { pollAgain = true; return; }
    polling = true;
    pollAgain = false;
    const controller = new AbortController();
    pollController = controller;
    try {
      const next = await request("/api/state", { controller });
      if (!next || typeof next !== "object" || !Array.isArray(next.library)) throw new Error("状态暂时无法读取。");
      failureCount = 0;
      setConnection("connected");
      acceptState(next);
    } catch (_) {
      if (token && !document.hidden) {
        failureCount += 1;
        setConnection("offline", "与电脑的连接中断，正在重连。请保持同一 Wi-Fi，并在电脑上开启手机遥控。");
      }
    } finally {
      if (pollController === controller) pollController = null;
      polling = false;
      const delay = pollAgain ? 0 : failureCount ? Math.min(8000, 500 * (2 ** Math.min(failureCount, 4))) : 500;
      schedulePoll(delay);
    }
  }

  function sampledPosition(now = performance.now()) {
    const sinceSample = Math.max(0, now - sample.at);
    const elapsed = sample.running && connected ? Math.min(sinceSample, 1500) / 1000 : 0;
    const correction = sample.correction * Math.max(0, 1 - sinceSample / 180);
    return clamp(sample.position + elapsed + correction, sample.duration);
  }

  function resample(smooth = false) {
    const previous = sampledPosition();
    const transport = currentTransport();
    const position = finite(transport.position);
    const correction = smooth && Math.abs(previous - position) < 0.18 ? previous - position : 0;
    const duration = finite(transport.duration) || (mode === "game" ? finite(state?.duration) : 0);
    sample = { position, duration, running: transportRunning(), correction, at: performance.now() };
    sample.position = clamp(sample.position, sample.duration);
    paintedSecond = -1;
    paintedDuration = -1;
    paintProgress();
    if (sample.running && connected && !document.hidden && frame === null) frame = requestAnimationFrame(animate);
  }

  function applyPalette(palette) {
    if (!palette || typeof palette !== "object") return;
    for (const name of ["bg", "surface", "ink", "muted", "line", "accent"]) {
      if (typeof palette[name] === "string" && /^#[0-9a-f]{6}$/i.test(palette[name])) {
        document.documentElement.style.setProperty(`--${name}`, palette[name]);
        if (Object.hasOwn(scorePalette, name) && scorePalette[name] !== palette[name]) {
          scorePalette[name] = palette[name];
          scorePaintKey = "";
        }
      }
    }
    if (typeof palette.bg === "string" && /^#[0-9a-f]{6}$/i.test(palette.bg)) document.querySelector('meta[name="theme-color"]').content = palette.bg;
  }

  function acceptState(next) {
    const sameSong = next.song_id === state?.song_id;
    const wasRunning = transportRunning();
    state = next;
    syncScore(next.score_id);
    applyPalette(next.palette);
    setText(dom.title, safeText(next.title) || "选一首，慢慢听");
    const available = next.library.filter((song) => song && typeof song.id === "string" && typeof song.title === "string");
    const signature = JSON.stringify(available.map((song) => [song.id, song.title, song.duration_seconds]));
    if (signature !== librarySignature) {
      library = available;
      librarySignature = signature;
      renderLibrary();
    }
    resample(sameSong && wasRunning && transportRunning());
    updateControls();
  }

  function updateControls() {
    const game = mode === "game";
    const gameState = state?.game?.state;
    const playing = transportRunning();
    const countdown = game && gameState === "countdown";
    const actionBusy = [...pending].some((action) => action !== "stop" && action !== "game_stop");
    const busy = Boolean(state?.busy) || actionBusy;
    const canPlay = game ? Boolean(state?.game?.available && state?.can_play) : Boolean(state?.can_play);
    dom.previewMode.classList.toggle("active", !game);
    dom.gameMode.classList.toggle("active", game);
    dom.previewMode.setAttribute("aria-pressed", String(!game));
    dom.gameMode.setAttribute("aria-pressed", String(game));
    dom.seek.hidden = game;
    dom.seekLabel.hidden = game;
    dom.gameProgress.hidden = !game;
    dom.seek.disabled = !connected || busy || !state?.can_play || sample.duration <= 0;
    dom.play.disabled = !connected || busy || !canPlay || (game && (playing || countdown));
    dom.stop.disabled = !connected || pending.has(game ? "game_stop" : "stop");
    dom.refresh.disabled = !connected || busy;
    dom.refresh.classList.toggle("is-refreshing", pending.has("refresh"));
    setText(dom.playLabel, game ? (countdown ? "准备演奏…" : playing ? "演奏中" : "开始演奏") : playing ? "暂停" : transportState() === "paused" ? "继续播放" : "播放");
    dom.playIcon.setAttribute("d", !game && playing ? "M8 5v14M16 5v14" : "m9 5 10 7-10 7V5Z");
    setText(dom.hint, game ? "电脑先进入游戏口琴界面。开始后倒计时 3 秒，切出游戏会停止。" : "声音从电脑播放，手机只负责控制。");
    const labels = game ? { idle: "等待演奏", ready: "准备就绪", countdown: "即将开始", playing: "正在演奏", ended: "演奏结束" } : { ready: "准备就绪", playing: "正在试听", paused: "已暂停", ended: "试听结束" };
    setText(dom.playerState, !connected ? "等待连接" : busy ? "正在准备曲谱" : labels[transportState()] || "选择曲目");
    const status = game ? safeText(state?.game?.message) || (state && !state?.game?.available ? "请先在电脑上开启游戏演奏。" : "") : safeText(state?.status);
    setText(dom.status, pending.has("select") ? "正在选择曲目…" : status);
    for (const row of rows) {
      row.button.disabled = !connected || busy;
      row.button.setAttribute("aria-current", String(row.id === state?.song_id));
    }
    if (!library.length && connected) {
      dom.empty.textContent = "曲库还是空的。在电脑曲库文件夹放入 MIDI，再点刷新。";
      dom.empty.hidden = false;
    }
  }

  function renderLibrary() {
    const query = dom.search.value.trim().toLocaleLowerCase();
    const visible = library.map((song, index) => ({ ...song, index })).filter((song) => song.title.toLocaleLowerCase().includes(query));
    const fragment = document.createDocumentFragment();
    rows = [];
    for (const song of visible) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "song-button";
      button.setAttribute("aria-label", `选择 ${safeText(song.title)}`);
      const number = document.createElement("span");
      number.className = "song-number";
      number.textContent = String(song.index + 1).padStart(2, "0");
      number.setAttribute("aria-hidden", "true");
      const title = document.createElement("span");
      title.className = "song-name";
      title.textContent = safeText(song.title);
      const duration = document.createElement("span");
      duration.className = "song-duration";
      duration.textContent = song.duration_seconds > 0 ? formatTime(song.duration_seconds) : "";
      const indicator = document.createElement("span");
      indicator.className = "song-indicator";
      indicator.setAttribute("aria-hidden", "true");
      button.append(number, title, duration, indicator);
      button.addEventListener("click", () => command("select", { song_id: song.id, autoplay: false }));
      item.append(button);
      fragment.append(item);
      rows.push({ id: song.id, button });
    }
    dom.songs.replaceChildren(fragment);
    dom.libraryCount.textContent = query ? `${visible.length} / ${library.length} 首` : `${library.length} 首`;
    dom.clearSearch.hidden = !dom.search.value;
    dom.empty.hidden = visible.length > 0;
    dom.empty.textContent = query ? "没有找到这首曲子，换个关键词试试。" : connected ? "曲库还是空的。在电脑曲库文件夹放入 MIDI，再点刷新。" : "连接后显示电脑曲库";
    updateControls();
  }

  function paintProgress(now = performance.now()) {
    const position = dragging && mode === "preview" ? dragPosition : sampledPosition(now);
    const fraction = sample.duration > 0 ? clamp(position / sample.duration, 1) : 0;
    if (!dragging) dom.seek.value = String(Math.round(fraction * 1000));
    dom.seek.style.setProperty("--progress", `${fraction * 100}%`);
    dom.gameFill.style.transform = `scaleX(${fraction})`;
    const second = Math.floor(position);
    if (second !== paintedSecond) {
      dom.position.textContent = formatTime(position);
      dom.seek.setAttribute("aria-valuetext", `${Math.floor(second / 60)} 分 ${second % 60} 秒`);
      dom.gameProgress.setAttribute("aria-valuenow", String(Math.round(fraction * 100)));
      paintedSecond = second;
    }
    if (sample.duration !== paintedDuration) {
      dom.duration.textContent = formatTime(sample.duration);
      paintedDuration = sample.duration;
    }
    paintScore(position);
  }

  function cancelScoreRequest() {
    scoreGeneration += 1;
    if (scoreRetryTimer !== null) {
      clearTimeout(scoreRetryTimer);
      scoreRetryTimer = null;
      scoreNeeded = Boolean(scoreId && !score);
    }
    if (scoreController) {
      scoreController.abort();
      scoreController = null;
      // A reconnect may finish the interrupted request for the same score.
      scoreNeeded = Boolean(scoreId && !score);
    }
  }

  function syncScore(id) {
    const nextId = typeof id === "string" && id ? id : null;
    if (nextId !== scoreId) {
      cancelScoreRequest();
      scoreId = nextId;
      score = null;
      scoreNeeded = Boolean(nextId);
      scoreRetryDelay = 0;
      scorePaintKey = "";
      dom.scoreCanvas.dataset.scoreId = "";
      dom.scoreCanvas.dataset.noteCount = "0";
      paintScore(sampledPosition());
    }
    if (scoreNeeded && !scoreController && connected && !document.hidden) loadScore();
  }

  async function loadScore() {
    const wantedId = scoreId;
    const generation = scoreGeneration;
    const controller = new AbortController();
    scoreController = controller;
    scoreNeeded = false;
    try {
      const data = await request("/api/score", { controller });
      if (generation !== scoreGeneration || !connected || document.hidden || wantedId !== scoreId) return;
      if (data?.id !== wantedId || !Array.isArray(data.notes)) throw new Error("曲谱正在更新。");
      const notes = data.notes.filter((note) => Array.isArray(note) && note.length >= 3 &&
        Number.isFinite(note[0]) && Number.isFinite(note[1]) && note[0] >= 0 && note[1] > note[0] &&
        Number.isInteger(note[2]) && note[2] >= 0 && note[2] <= 127).map((note) => note.slice(0, 3));
      notes.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
      let low = Number.isInteger(data.low) && data.low >= 0 && data.low <= 127 ? data.low : 60;
      let high = Number.isInteger(data.high) && data.high >= low && data.high <= 127 ? data.high : low + 12;
      if (notes.length) { low = notes[0][2]; high = low; }
      let end = 0;
      const ends = notes.map((note) => {
        low = Math.min(low, note[2]); high = Math.max(high, note[2]);
        end = Math.max(end, note[1]);
        return end;
      });
      const middle = (low + high) / 2;
      const range = Math.max(12, high - low + 4);
      score = { notes, ends, low: middle - range / 2, high: middle + range / 2, duration: finite(data.duration) };
      scoreRetryDelay = 0;
      dom.scoreCanvas.dataset.scoreId = wantedId;
      dom.scoreCanvas.dataset.noteCount = String(notes.length);
      scorePaintKey = "";
      paintProgress();
    } catch (_) {
      // Keep an empty grid when this score is unavailable; never reuse another song.
      if (generation === scoreGeneration && wantedId === scoreId && connected && !document.hidden) {
        scoreRetryDelay = scoreRetryDelay ? Math.min(8000, scoreRetryDelay * 2) : 2000;
        scoreRetryTimer = setTimeout(() => {
          scoreRetryTimer = null;
          if (generation === scoreGeneration && wantedId === scoreId && connected && !document.hidden) {
            scoreNeeded = true;
            syncScore(scoreId);
          }
        }, scoreRetryDelay);
      }
    } finally {
      if (scoreController === controller) scoreController = null;
    }
  }

  function resizeScore() {
    const bounds = dom.scoreCanvas.getBoundingClientRect();
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    const width = bounds.width;
    const height = bounds.height;
    if (width <= 0 || height <= 0) return;
    if (width !== scoreSize.width || height !== scoreSize.height || ratio !== scoreSize.ratio) {
      scoreSize = { width, height, ratio };
      dom.scoreCanvas.width = Math.max(1, Math.round(width * ratio));
      dom.scoreCanvas.height = Math.max(1, Math.round(height * ratio));
      scorePaintKey = "";
    }
    paintProgress();
  }

  function paintScore(position) {
    if (!scoreContext || scoreSize.width <= 0) return;
    const key = `${position}:${scoreGeneration}:${Boolean(score)}`;
    if (key === scorePaintKey) return;
    scorePaintKey = key;
    const ctx = scoreContext;
    const { width, height } = scoreSize;
    const top = 8;
    const bottom = height - 21;
    const center = width / 2;
    const pixelsPerSecond = width / 8;
    const windowStart = position - 4;
    const windowEnd = position + 4;
    const low = score?.low ?? 58;
    const high = score?.high ?? 74;
    const pitchY = (pitch) => top + (high - pitch) / (high - low) * (bottom - top);
    ctx.setTransform(dom.scoreCanvas.width / width, 0, 0, dom.scoreCanvas.height / height, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.lineWidth = 1;
    ctx.strokeStyle = scorePalette.line;
    ctx.globalAlpha = 0.55;
    for (let pitch = Math.ceil(low / 3) * 3; pitch <= high; pitch += 3) {
      const y = pitchY(pitch);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
    }
    ctx.fillStyle = scorePalette.muted;
    ctx.font = '9px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    for (let second = Math.max(0, Math.ceil(windowStart)); second <= windowEnd; second += 1) {
      const x = center + (second - position) * pixelsPerSecond;
      ctx.globalAlpha = second % 2 ? 0.22 : 0.42;
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
      if (second % 2 === 0 && x > 12 && x < width - 12) {
        ctx.globalAlpha = 0.7;
        ctx.fillText(formatTime(second), x, height - 1);
      }
    }
    if (score) {
      // Prefix maximum ends retain long notes that began before the visible window.
      let first = 0;
      let last = score.ends.length;
      while (first < last) {
        const middle = (first + last) >>> 1;
        if (score.ends[middle] <= windowStart) first = middle + 1;
        else last = middle;
      }
      const noteHeight = Math.max(3, Math.min(5, (bottom - top) / (high - low) * 0.85));
      ctx.save(); ctx.beginPath(); ctx.rect(0, top, width, bottom - top); ctx.clip();
      ctx.fillStyle = scorePalette.accent;
      ctx.strokeStyle = scorePalette.accent;
      for (let index = first; index < score.notes.length; index += 1) {
        const [start, end, pitch] = score.notes[index];
        if (start >= windowEnd) break;
        if (end <= windowStart) continue;
        const x = center + (start - position) * pixelsPerSecond;
        const noteWidth = Math.max(2, (end - start) * pixelsPerSecond);
        const y = pitchY(pitch) - noteHeight / 2;
        const active = start <= position && position < end;
        ctx.globalAlpha = active ? 1 : end <= position ? 0.35 : 0.62;
        ctx.fillRect(x, y, noteWidth, noteHeight);
        if (active) { ctx.lineWidth = 1; ctx.strokeRect(x, y - 1, noteWidth, noteHeight + 2); }
      }
      ctx.restore();
    }
    ctx.globalAlpha = 0.85;
    ctx.strokeStyle = scorePalette.accent;
    ctx.fillStyle = scorePalette.accent;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(center, 5); ctx.lineTo(center, bottom + 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(center - 3.5, 0); ctx.lineTo(center + 3.5, 0); ctx.lineTo(center, 5); ctx.closePath(); ctx.fill();
    ctx.globalAlpha = 1;
  }

  function animate(now) {
    frame = null;
    if (document.hidden) return;
    paintProgress(now);
    if (sample.running && connected) frame = requestAnimationFrame(animate);
  }

  async function command(action, parameters = {}) {
    if (!connected || !token || pending.has(action)) return;
    pending.add(action);
    updateControls();
    try {
      const result = await request("/api/command", { body: { action, ...parameters } });
      if (result?.message) showToast(result.message);
      if (action === "seek") {
        sample.position = clamp(finite(parameters.position), sample.duration);
        sample.correction = 0;
        sample.at = performance.now();
        paintedSecond = -1;
        paintProgress();
      }
      schedulePoll(0);
    } catch (error) {
      showToast(error.name === "AbortError" || error instanceof TypeError ? "操作未确认，请检查电脑连接后重试。" : error.message);
      schedulePoll(0);
    } finally {
      pending.delete(action);
      updateControls();
    }
  }

  function setMode(value) {
    mode = value;
    dragging = false;
    resample();
    updateControls();
  }
  dom.previewMode.addEventListener("click", () => setMode("preview"));
  dom.gameMode.addEventListener("click", () => setMode("game"));
  dom.play.addEventListener("click", () => command(mode === "game" ? "game_play" : transportRunning() ? "pause" : "play"));
  dom.stop.addEventListener("click", () => command(mode === "game" ? "game_stop" : "stop"));
  dom.refresh.addEventListener("click", () => command("refresh"));
  dom.search.addEventListener("input", renderLibrary);
  dom.search.addEventListener("keydown", (event) => { if (event.key === "Enter") dom.search.blur(); });
  dom.clearSearch.addEventListener("click", () => { dom.search.value = ""; renderLibrary(); dom.search.focus(); });
  dom.seek.addEventListener("pointerdown", () => { dragging = true; dragPosition = sampledPosition(); });
  dom.seek.addEventListener("input", () => { dragging = true; dragPosition = Number(dom.seek.value) / 1000 * sample.duration; paintProgress(); });
  dom.seek.addEventListener("change", () => {
    const position = Number(dom.seek.value) / 1000 * sample.duration;
    dragging = false;
    command("seek", { position });
  });
  dom.seek.addEventListener("pointercancel", () => { dragging = false; });
  dom.seek.addEventListener("blur", () => { dragging = false; });
  window.addEventListener("pointerup", () => {
    // A press without movement does not always emit a range change event.
    setTimeout(() => { if (dragging) { dragging = false; paintProgress(); } }, 0);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(pollTimer);
      pollController?.abort();
      cancelScoreRequest();
      cancelAnimationFrame(frame);
      frame = null;
    } else {
      failureCount = 0;
      if (token) setConnection("connecting");
      schedulePoll(0);
      if (frame === null) frame = requestAnimationFrame(animate);
    }
  });
  window.addEventListener("online", () => { failureCount = 0; schedulePoll(0); });
  window.addEventListener("offline", () => setConnection("offline", "手机网络已断开。连接到电脑所在的 Wi-Fi 后会自动重连。"));
  window.addEventListener("pagehide", () => { clearTimeout(pollTimer); pollController?.abort(); cancelScoreRequest(); cancelAnimationFrame(frame); frame = null; });
  window.addEventListener("pageshow", (event) => { if (event.persisted) { schedulePoll(0); if (frame === null) frame = requestAnimationFrame(animate); } });

  window.addEventListener("resize", resizeScore);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(resizeScore).observe(dom.cover);
  resizeScore();

  if (token) {
    setConnection("connecting");
    schedulePoll(0);
  } else {
    setConnection("expired", "请扫描电脑「手机遥控」窗口中的二维码，连接后即可选曲和播放。");
  }
  frame = requestAnimationFrame(animate);
})();
