"use strict";
const $ = (id) => document.getElementById(id);
const messages = $("messages");
let cursor = 0, state = null, connected = false, sending = false, assistantBlock = null;
let confirmationId = null, dismissedWarning = false;
let taskId = null, lastTaskJSON = "";
let playbackExpiry = null;
let glowExpiry = null, glowSequence = -1;
let waveFrame = null, waveValues = Array(32).fill(0);
let lightPreview = false, previewExpiry = null, stateReceivedAt = 0;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
// Explicit app-only light preference; never changes the operating system setting.
for (const id of ["motion", "allow-pulse-motion"]) {
  try {
    const saved = localStorage.getItem("valera-light-" + id);
    if (saved !== null) $(id).checked = saved === "true";
  } catch { /* Preferences still work for this page when storage is unavailable. */ }
}
document.body.classList.toggle("motion-off", !$("motion").checked);
document.body.classList.toggle("allow-pulse-motion", $("allow-pulse-motion").checked);
document.body.classList.toggle("tab-hidden", document.hidden);
function pulseMotionBlocked() { return reducedMotion.matches && !$("allow-pulse-motion").checked; }
const fragment = new URLSearchParams(location.hash.slice(1));
let token = fragment.get("token") || "";
try {
  if (token) sessionStorage.setItem("valera-session", token);
  else token = sessionStorage.getItem("valera-session") || "";
} catch { /* A privacy setting may disable storage; the current tab still works. */ }
if (location.hash) history.replaceState(null, "", location.pathname);

const statusText = {
  offline: ["Немає з’єднання", "Відкрий посилання поточної сесії з термінала."],
  ready: ["Я поруч.", "Напиши повідомлення або увімкни мікрофон."],
  listening: ["Слухаю тебе.", "Говори природно. Ім’я для активації не потрібне."],
  processing_audio: ["Опрацьовую аудіо…", "Підготовка розпізнавання або обробка записаної репліки."],
  thinking: ["Опрацьовую запит…", "Відповідь з’являтиметься тут у міру готовності."],
  speaking: ["Відповідаю", "Можеш зупинити відповідь кнопкою нижче."],
  preparing_speech: ["Готую голос…", "Текст готовий, очікую на озвучення."],
  paused: ["Не поспішаємо.", "Розмова на паузі. Повернемося, коли будеш готовий."],
  confirmation: ["Потрібне твоє рішення.", "Перевір дію в панелі підтвердження під розмовою."],
  voice_error: ["Не чую мікрофона.", "Можна продовжити текстом. Подробиці — у журналі."],
};

function banner(text) { $("banner").textContent = text; $("banner").hidden = !text; }
function describeLight() {
  let detail;
  if (!$("motion").checked) detail = "Пульсацію вимкнено перемикачем вище.";
  else if (pulseMotionBlocked()) detail = "Система просить зменшити рух: пульсацію вимкнено. Окремий дозвіл вище вмикає її лише для ValleRa.";
  else if (document.hidden) detail = "Вкладку приховано — світлові ефекти призупинені.";
  else if (lightPreview) detail = "Зразок форми хвилі: демонстраційні дані, без голосу й API.";
  else if (!connected) detail = "Немає зв’язку з ValleRa. Перевірка світла працює окремо.";
  else if (typeof state?.tts_playback !== "boolean") detail = "Сервер не передає стан голосу. Перезапусти ValleRa й відкрий нове посилання сесії.";
  else if (document.body.dataset.playback !== "true") detail = "Очікування озвучення. У спокої обличчя статичне.";
  else if (document.body.dataset.glowMode !== "audio") detail = "Немає свіжих аудіоданих для хвилі. Рот залишається лінією; для цього виходу аналіз звуку може бути недоступний.";
  else detail = "Форма хвилі — з PCM голосу, яскравість — з рівня звуку. Отримано кадрів: " + (state?.tts_glow?.sequence ?? 0);
  if ($("motion").checked && reducedMotion.matches && !pulseMotionBlocked()) {
    detail += " Світлові ефекти дозволено для ValleRa; налаштування системи не змінені.";
  }
  $("light-status").textContent = detail;
  $("preview-light").disabled = !$("motion").checked || pulseMotionBlocked();
}
function drawWave(values) {
  // The 56-unit wave is only 1.4 times wider than the 40-unit resting mouth.
  const points = values.map((value, i) => [172 + 56 * i / 31,
    -value * .38 * Math.sin(Math.PI * i / 31)]);
  function pathFor(scale) {
    let path = "M172 0";
    for (let i = 1; i < points.length - 1; i++) {
      const [x, y] = points[i], [nx, ny] = points[i + 1];
      path += ` Q${x.toFixed(2)} ${(y * scale).toFixed(2)} ${((x + nx) / 2).toFixed(2)} ${((y + ny) * scale / 2).toFixed(2)}`;
    }
    return path + " L228 0";
  }
  $("mouth-wave").setAttribute("d", pathFor(1));
  $("mouth-echo").setAttribute("d", pathFor(.55));
}
function resetWave() {
  cancelAnimationFrame(waveFrame);
  waveFrame = null;
  waveValues = Array(32).fill(0);
  drawWave(waveValues);
}
function updateWave(samples) {
  cancelAnimationFrame(waveFrame);
  if (!$("motion").checked || pulseMotionBlocked() || document.hidden) { resetWave(); return; }
  const from = waveValues.slice(), started = performance.now();
  let lastPaint = -Infinity;
  function frame(now) {
    const progress = Math.min(1, (now - started) / 90);
    if (now - lastPaint >= 32 || progress === 1) {
      waveValues = samples.map((value, i) => from[i] + (value - from[i]) * progress);
      drawWave(waveValues);
      lastPaint = now;
    }
    waveFrame = progress < 1 ? requestAnimationFrame(frame) : null;
  }
  waveFrame = requestAnimationFrame(frame);
}
function renderGlow(playing, pulse) {
  if (lightPreview) return;
  if (!playing) {
    clearTimeout(glowExpiry);
    document.body.dataset.glow = "0";
    document.body.dataset.glowMode = "idle";
    resetWave();
    describeLight();
    return;
  }
  const fresh = pulse && Number.isInteger(pulse.sequence) && Number.isInteger(pulse.strength)
    && pulse.strength >= 0 && pulse.strength <= 5 && pulse.source === "pcm"
    && Array.isArray(pulse.samples) && pulse.samples.length === 32
    && pulse.samples.every(value => Number.isInteger(value) && Math.abs(value) <= 100)
    && Number.isFinite(pulse.age_ms) && pulse.age_ms >= 0 && pulse.age_ms < 300;
  document.body.dataset.glowMode = fresh ? "audio" : "unavailable";
  if (!fresh) { document.body.dataset.glow = "0"; resetWave(); }
  describeLight();
  if (!fresh || pulse.sequence === glowSequence) return;
  glowSequence = pulse.sequence;
  clearTimeout(glowExpiry);
  document.body.dataset.glow = String(pulse.strength);
  updateWave(pulse.samples);
  glowExpiry = setTimeout(() => {
    document.body.dataset.glow = "0";
    document.body.dataset.glowMode = "unavailable";
    resetWave();
    describeLight();
  }, Math.max(0, 300 - pulse.age_ms));
}
function controls() {
  for (const id of ["message", "stop", "new-chat"]) $(id).disabled = !connected;
  $("send").disabled = !connected || sending;
  $("microphone").disabled = !connected || !state?.microphone_available;
  $("pause").disabled = !connected || !!state?.confirmation || state?.mode !== "chat";
  $("cancel-task").disabled = !connected || !state?.task?.active || !!state?.task?.cancel_requested;
  for (const button of document.querySelectorAll("[data-prompt]")) button.disabled = !connected;
}
function renderState(next) {
  state = next;
  stateReceivedAt = performance.now();
  const phase = connected ? state.status : "offline";
  document.body.dataset.state = phase;
  clearTimeout(playbackExpiry);
  const playing = connected && !document.hidden && !!state.tts_playback;
  document.body.dataset.playback = String(playing);
  renderGlow(playing, state.tts_glow);
  // Clear stale illumination before a stalled network request times out.
  if (playing) playbackExpiry = setTimeout(() => {
    document.body.dataset.playback = "false";
    renderGlow(false);
  }, 3500);
  const [title, detail] = statusText[phase] || statusText.ready;
  $("status").textContent = title;
  $("status-detail").textContent = detail;
  $("provider").textContent = state.provider;
  $("connection").classList.toggle("connected", connected);
  $("connection").replaceChildren(Object.assign(document.createElement("i")), document.createTextNode(connected ? "З’єднано" : "Немає зв’язку"));
  $("microphone").setAttribute("aria-pressed", String(!!state.microphone));
  $("microphone").querySelector("span").textContent = state.microphone ? "Вимкнути мікрофон" : "Увімкнути мікрофон";
  $("microphone").title = $("microphone").querySelector("span").textContent;
  $("mic-detail").textContent = !connected ? "Стан мікрофона невідомий — перевір застосунок" :
    state.microphone_stopping ? "Завершую поточне захоплення…" :
    !state.microphone_available ? "Голосове введення недоступне в цій сесії" :
    state.microphone ? "Мікрофон дозволено · під час озвучення слухання призупинене" : "Мікрофон вимкнено · захоплення зупинене";
  $("pause").textContent = state.paused ? "▷ Повернутися до розмови" : "Ⅱ Пауза розмови";
  confirmationId = connected ? state.confirmation : null;
  $("confirmation").hidden = !confirmationId;
  $("confirmation-text").textContent = state.confirmation_prompt || "";
  renderTask(state.task);
  controls();
}
const taskStates = {planning:"Складаю план",awaiting_confirmation:"Очікує дозволу",running:"Виконую",completed:"Вікна програм знайдено",saved:"Профіль збережено",partial:"Частковий результат",failed:"Зупинено через помилку",cancelled:"Скасовано",interrupted:"Перервано"};
const stepStates = {pending:"Очікує",running:"Перевірка",verifying:"Запуск / перевірка вікна",verified:"Вікно знайдено",saved:"Збережено",unknown:"Не перевірено",skipped:"Пропущено"};
function renderTask(task) {
  $("agent-task").hidden = !task;
  if (!task) { taskId = null; lastTaskJSON = ""; return; }
  const serialized = JSON.stringify({connected, task});
  if (serialized === lastTaskJSON) return;
  if (taskId !== task.id) $("task-details").open = false;
  taskId = task.id; lastTaskJSON = serialized;
  $("task-title").textContent = task.title;
  const taskStatus = task.cancel_requested && task.active ? "Скасовую наступні кроки…" : (taskStates[task.status] || task.status);
  $("task-status").textContent = connected ? taskStatus : "Останній відомий стан: " + taskStatus;
  $("task-detail").textContent = task.detail;
  $("task-steps").replaceChildren();
  for (const step of task.steps) {
    const li = document.createElement("li");
    const name = document.createElement("strong"); name.textContent = step.name;
    const status = document.createElement("span"); status.textContent = " · " + (stepStates[step.status] || step.status);
    const detail = document.createElement("p"); detail.textContent = step.detail;
    li.append(name, status, detail); $("task-steps").append(li);
  }
}
function updateCompactConversation() {
  const blocks = Array.from(messages.children);
  // Keep the latest user turn and everything belonging to it, including sources.
  // No duplicate transcript: expanding history reveals the same DOM blocks.
  let start = blocks.findLastIndex((block) => block.classList.contains("user"));
  if (start < 0) start = Math.max(0, blocks.findLastIndex((block) => block.classList.contains("assistant")));
  blocks.forEach((block, index) => block.classList.toggle("current-turn", index >= start));
}
function appendEvent(event) {
  // Playback notifications wake the long poll; they are not chat messages.
  if (event.kind === "playback") return;
  if (event.kind === "response_end") { assistantBlock = null; return; }
  if (event.kind === "new_conversation") {
    messages.replaceChildren(); assistantBlock = null;
    appendEvent({kind: "notice", text: "Нову розмову розпочато. Попередня історія залишилася в локальному архіві."});
    return;
  }
  $("empty")?.remove();
  const nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 90;
  if (event.kind === "assistant" || event.kind === "user") {
    if (event.kind === "assistant" && assistantBlock?.isConnected) {
      assistantBlock.textContent += " " + event.text;
    } else {
      const block = document.createElement("article");
      block.className = "message " + event.kind;
      const label = document.createElement("div"); label.className = "label";
      label.textContent = event.kind === "user" ? "ТИ" : "ВАЛЕРА";
      if (event.source === "voice") { const source = document.createElement("small"); source.textContent = "розпізнано з голосу"; label.append(source); }
      const text = document.createElement("div"); text.className = "text"; text.textContent = event.text;
      block.append(label, text); messages.append(block);
      assistantBlock = event.kind === "assistant" ? text : null;
    }
  } else if (event.kind === "source") {
    try {
      const url = new URL(event.href);
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return;
      const link = document.createElement("a"); link.className = "source-link";
      link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = "↗ " + event.text;
      const host = document.createElement("small"); host.textContent = url.hostname; link.append(host); messages.append(link);
      if (!messages.classList.contains("expanded")) $("history-toggle").title = "Історія розмови та джерела відповіді";
    } catch { return; }
  } else if (event.kind === "notice") {
    assistantBlock = null;
    const notice = document.createElement("p"); notice.className = "notice"; notice.textContent = event.text; messages.append(notice);
  }
  while (messages.children.length > 200) messages.firstElementChild.remove();
  updateCompactConversation();
  if (nearBottom || !messages.classList.contains("expanded") || event.kind === "user") messages.scrollTop = messages.scrollHeight;
}
async function request(path, options = {}) {
  const response = await fetch(path, {...options, cache: "no-store", credentials: "omit",
    headers: {"Authorization": "Bearer " + token, ...(options.body ? {"Content-Type": "application/json"} : {})},
    signal: AbortSignal.timeout(12000)});
  if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("Сервер UI недоступний.");
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не вдалося виконати запит.");
  return data;
}
async function action(name, fields = {}) {
  try {
    await request("/api/action", {method: "POST", body: JSON.stringify({action: name, ...fields})});
    banner(""); return true;
  } catch (error) {
    banner(error.name === "TimeoutError" ? "Немає підтвердження доставки. Перевір розмову перед повтором команди." : error.message);
    return false;
  }
}
async function send(text) {
  if (!connected || sending || !text.trim()) return;
  sending = true; controls();
  if (await action("message", {text: text.trim()})) $("message").value = "";
  sending = false; controls(); $("message").focus();
}
$("composer").addEventListener("submit", (event) => { event.preventDefault(); send($("message").value); });
$("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send($("message").value); }
});
$("microphone").addEventListener("click", () => action("microphone", {enabled: !state.microphone}));
$("stop").addEventListener("click", () => action("stop"));
$("pause").addEventListener("click", () => action(state.paused ? "resume" : "pause"));
$("new-chat").addEventListener("click", () => send("Команда: нова розмова"));
$("cancel-task").addEventListener("click", () => action("cancel_task", {task_id:taskId}));
$("history-toggle").addEventListener("click", () => {
  const expanded = messages.classList.toggle("expanded");
  document.body.classList.toggle("history-open", expanded);
  $("history-toggle").setAttribute("aria-expanded", String(expanded));
  $("history-toggle").querySelector("span").textContent = expanded ? "Згорнути історію" : "Розгорнути історію";
  $("history-toggle").title = expanded ? "Згорнути історію" : "Історія розмови та джерела";
  messages.scrollTop = messages.scrollHeight;
});
function endLightPreview() {
  clearTimeout(previewExpiry);
  lightPreview = false;
  document.body.classList.remove("light-preview");
  $("light-preview-note").hidden = true;
  const recent = connected && !document.hidden && performance.now() - stateReceivedAt < 3500;
  renderGlow(recent && !!state?.tts_playback);
}
for (const id of ["motion", "allow-pulse-motion"]) {
  $(id).addEventListener("change", () => {
    document.body.classList.toggle("motion-off", !$("motion").checked);
    document.body.classList.toggle("allow-pulse-motion", $("allow-pulse-motion").checked);
    try { localStorage.setItem("valera-light-" + id, String($(id).checked)); } catch { /* Page-local fallback. */ }
    if ((!$("motion").checked || pulseMotionBlocked()) && lightPreview) endLightPreview();
    if (!$("motion").checked || pulseMotionBlocked()) resetWave();
    describeLight();
  });
}
reducedMotion.addEventListener("change", () => {
  if (pulseMotionBlocked() && lightPreview) endLightPreview();
  if (pulseMotionBlocked()) resetWave();
  describeLight();
});
$("preview-light").addEventListener("click", () => {
  if (!$("motion").checked || pulseMotionBlocked()) return;
  clearTimeout(previewExpiry);
  clearTimeout(glowExpiry);
  lightPreview = true;
  document.body.classList.add("light-preview");
  document.body.dataset.glowMode = "preview";
  document.body.dataset.glow = "5";
  resetWave();
  drawWave([0, 2, -4, 7, 14, -11, -20, 6, 24, 48, -12, -55, -31, 18, 65, 90,
    20, -46, -72, -22, 32, 54, 17, -35, -22, 8, 18, 9, -7, -3, 2, 0]);
  $("light-preview-note").hidden = false;
  $("about-dialog").close();
  describeLight();
  previewExpiry = setTimeout(endLightPreview, 3000);
});
// Do not retain speech pulses in a background tab.
document.addEventListener("visibilitychange", () => {
  document.body.classList.toggle("tab-hidden", document.hidden);
  if (document.hidden) {
    if (lightPreview) endLightPreview();
    document.body.dataset.playback = "false";
    renderGlow(false);
  }
  describeLight();
});
for (const [id, accept] of [["accept", true], ["reject", false]]) {
  $(id).addEventListener("click", async () => {
    $("accept").disabled = $("reject").disabled = true;
    await action("confirm", {request_id: confirmationId, accept});
    $("accept").disabled = $("reject").disabled = false;
  });
}
for (const button of document.querySelectorAll("[data-prompt]")) {
  button.addEventListener("click", () => { $("message").value = button.dataset.prompt; $("message").focus(); });
}
$("about").addEventListener("click", () => { describeLight(); $("about-dialog").showModal(); });
$("close-about").addEventListener("click", () => $("about-dialog").close());
async function poll() {
  if (!token) {
    banner("Для підключення запусти main.py --web-ui та відкрий посилання з термінала. Ця сторінка без ключа сесії не керує Валерою.");
    renderState({status: "offline", provider: "—"}); return;
  }
  while (true) {
    try {
      const data = await request("/api/events?after=" + cursor);
      if (!connected) banner("");
      connected = true;
      if (data.truncated && !dismissedWarning) {
        appendEvent({kind: "notice", text: "Показано лише останню частину подій сесії."}); dismissedWarning = true;
      }
      for (const event of data.events) if (event.id > cursor) appendEvent(event);
      cursor = data.cursor;
      renderState(data.state);
    } catch (error) {
      connected = false;
      renderState(state || {status: "offline", provider: "—"});
      banner("Зв’язок перервано. Мікрофон Валери міг залишитися ввімкненим. " + error.message);
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    if (document.hidden) await new Promise((resolve) => setTimeout(resolve, 2500));
  }
}
poll();
