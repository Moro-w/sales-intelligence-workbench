let ws = null;
let mediaStream = null;
let audioCtx = null;
let proc = null;
let meetingId = null;
let timerInterval = null;
let startTime = 0;
let stopFallbackTimer = null;  // B6: stop 后等不到 final 的兜底跳转

// 断线续录状态：断线不跳走，自动重连，服务端宽限期内复用同一引擎无缝续录
let stopping = false;          // 用户点了停止：其后的 onclose 是正常收尾
let disconnected = false;      // 当前处于断线重连中
let disconnectAt = 0;
let reconnectAttempts = 0;
let reconnectTimer = null;
let pendingStop = false;       // 断线中点了停止：重连成功后补发 stop
const RECONNECT_DELAYS_S = [1, 2, 4, 8, 16, 30];
const RECONNECT_MAX_MS = 10 * 60 * 1000;  // 与服务端宽限期对齐
const origTitle = document.title;

const $ = (id) => document.getElementById(id);

// escapeHtml 见 static/common.js
// v0.7：增强模式（GPU sidecar）暂不提供，实时页只保留标准模式，引擎选择器已移除。

function setStatus(msg) {
  $("live-status-line").textContent = msg;
}

function formatTimer(ms) {
  const totalSec = Math.floor(ms / 1000);
  const m = String(Math.floor(totalSec / 60)).padStart(2, "0");
  const s = String(totalSec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function updateTimer() {
  if (!startTime) return;
  $("live-timer").textContent = formatTimer(Date.now() - startTime);
}

function appendSentence(s) {
  const ul = $("live-lines");
  const placeholder = ul.querySelector(".live-placeholder");
  if (placeholder) placeholder.remove();

  const li = document.createElement("li");
  li.className = "live-line";
  li.innerHTML = `<span class="live-text">${escapeHtml(s.text)}</span>`;
  ul.appendChild(li);
  ul.scrollTop = ul.scrollHeight;
}

function updatePartial(text) {
  const ul = $("live-lines");
  let partial = ul.querySelector(".live-partial");
  if (!partial) {
    const placeholder = ul.querySelector(".live-placeholder");
    if (placeholder) placeholder.remove();
    partial = document.createElement("li");
    partial.className = "live-line live-partial";
    partial.innerHTML = `<span class="live-text"></span>`;
    ul.appendChild(partial);
  }
  partial.querySelector(".live-text").textContent = text;
  ul.scrollTop = ul.scrollHeight;
}

function clearPartial() {
  const partial = $("live-lines").querySelector(".live-partial");
  if (partial) partial.remove();
}

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const tokQs = window.__tingjiLanToken ? `?token=${encodeURIComponent(window.__tingjiLanToken)}` : "";
  return `${proto}//${location.host}/ws/realtime/${meetingId}${tokQs}`;
}

function attachWsHandlers(sock) {
  sock.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (e) { return; }  // B6: 畸形帧忽略，不抛
    if (d.type === "sentence") {
      clearPartial();
      appendSentence(d);
    } else if (d.type === "partial") {
      updatePartial(d.text);
    } else if (d.type === "resumed") {
      // 宽限期内重连成功：用服务端快照重建列表（页面挂起/重载后 DOM 可能不完整）
      disconnected = false;
      reconnectAttempts = 0;
      document.title = origTitle;
      setStatus("已重连，继续实时记录…");
      const ul = $("live-lines");
      ul.innerHTML = "";
      (d.sentences || []).forEach(appendSentence);
      if (pendingStop) {
        pendingStop = false;
        stop();
      }
    } else if (d.type === "final") {
      if (stopFallbackTimer) { clearTimeout(stopFallbackTimer); stopFallbackTimer = null; }
      setStatus("保存完成，正在跳转…");
      location.href = `/m/${d.meeting_id}`;
    } else if (d.type === "error") {
      setStatus("错误：" + d.message);
      if ($("live-stop").classList.contains("hidden")) {
        // 未进入录音态（如该会议已有活跃连接）：复位开始按钮
        $("live-start").disabled = false;
        cleanupAudio();
      } else {
        stop();
      }
    }
  };

  sock.onerror = () => { /* onclose 会紧跟着触发，统一在那里处理 */ };

  sock.onclose = () => {
    if (sock !== ws) return;   // 失败的重连尝试的旧 socket，忽略
    if (stopping) return;      // 用户主动停止：stop() 里已安排兜底跳转
    if (meetingId && !$("live-stop").classList.contains("hidden")) {
      enterReconnect();        // 断线续录：不跳走，自动重连
      return;
    }
    setStatus("连接已断开");
    cleanupAudio();
    $("live-start").disabled = false;
  };
}

function enterReconnect() {
  if (disconnected) return;
  disconnected = true;
  disconnectAt = Date.now();
  reconnectAttempts = 0;
  document.title = "⚠ 连接中断 - 听记实时记录";
  setStatus("连接中断，正在自动重连…（恢复后继续记录，中断期间的内容不录制）");
  scheduleReconnect();
}

function scheduleReconnect() {
  if (!disconnected) return;
  if (Date.now() - disconnectAt > RECONNECT_MAX_MS) {
    // 超过服务端宽限期：服务端已自动保存，跳详情页看结果
    setStatus("重连超时，已录制内容已自动保存，正在跳转详情页…");
    cleanupAudio();
    location.href = `/m/${meetingId}`;
    return;
  }
  const d = RECONNECT_DELAYS_S[Math.min(reconnectAttempts, RECONNECT_DELAYS_S.length - 1)];
  reconnectAttempts++;
  reconnectTimer = setTimeout(tryReconnect, d * 1000);
}

function tryReconnect() {
  if (!disconnected) return;
  const nws = new WebSocket(wsUrl());
  nws.onopen = () => {
    ws = nws;
    attachWsHandlers(ws);      // 后续动作等 resumed / final 消息驱动
  };
  nws.onclose = () => { if (disconnected && ws !== nws) scheduleReconnect(); };
}

async function start() {
  if (!window.isSecureContext) {
    setStatus("当前地址不是安全上下文，浏览器不会授予麦克风权限。请使用 http://localhost:8000 访问，或在 config.yaml 中设置 server.ssl.enabled: true 后通过 HTTPS 访问。");
    $("live-start").disabled = false;
    return;
  }

  const title = $("live-title").value.trim() || "实时会议";
  $("live-start").disabled = true;
  setStatus("创建会议…");

  try {
    const r = await fetch("/api/live/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const data = await r.json();
    meetingId = data.meeting_id;
  } catch (e) {
    setStatus("创建会议失败：" + e.message);
    $("live-start").disabled = false;
    return;
  }

  stopping = false;
  ws = new WebSocket(wsUrl());
  attachWsHandlers(ws);

  ws.onopen = async () => {
    setStatus("连接成功，正在请求麦克风…");
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true },
      });
    } catch (e) {
      setStatus("麦克风授权失败：" + e.message + "（请检查浏览器权限设置，并确保通过 localhost 或 HTTPS 访问）");
      $("live-start").disabled = false;
      cleanupAudio();
      if (ws) {
        ws.close();
        ws = null;
      }
      return;
    }

    audioCtx = new AudioContext({ sampleRate: 16000 });
    const src = audioCtx.createMediaStreamSource(mediaStream);
    proc = audioCtx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (e) => {
      // 断线重连期间保持实时语义：不缓存断线期间的音频，恢复后从新内容继续
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const floats = e.inputBuffer.getChannelData(0);
      const out = new Int16Array(floats.length);
      for (let i = 0; i < floats.length; i++) {
        out[i] = Math.max(-32768, Math.min(32767, Math.round(floats[i] * 32768)));
      }
      ws.send(out.buffer);
    };
    src.connect(proc);
    proc.connect(audioCtx.destination);

    $("live-start").classList.add("hidden");
    $("live-stop").classList.remove("hidden");
    $("live-stop").disabled = false;
    $("live-timer").classList.remove("hidden");
    startTime = Date.now();
    timerInterval = setInterval(updateTimer, 1000);
    setStatus("正在实时记录…");
  };
}

function cleanupAudio() {
  if (proc) { proc.disconnect(); proc = null; }
  if (audioCtx) { audioCtx.close(); audioCtx = null; }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
}

function stop() {
  stopping = true;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: "stop" }));
  } else if (disconnected) {
    // 断线中点停止：立刻尝试重连，成功后补发 stop；连不上就靠服务端宽限期落盘
    pendingStop = true;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    tryReconnect();
  }
  $("live-stop").disabled = true;
  setStatus("正在停止并保存…");
  cleanupAudio();
  // B6: 若服务端不回 type:'final'（崩了/超时），8s 后跳详情页让 resume 接管，
  // 避免用户永久卡在"正在停止并保存…"文案。
  if (stopFallbackTimer) clearTimeout(stopFallbackTimer);
  stopFallbackTimer = setTimeout(() => {
    stopFallbackTimer = null;
    if (meetingId) location.href = `/m/${meetingId}`;
  }, 8000);
}

window.addEventListener("beforeunload", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: "stop" }));
  }
  cleanupAudio();
});

$("live-start").addEventListener("click", start);
$("live-stop").addEventListener("click", stop);
