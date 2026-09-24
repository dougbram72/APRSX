"use strict";

const MAX_TEXT = 67;

// messages: id -> row for the open conversation; convs: peer -> summary row.
const state = { peer: null, messages: new Map(), convs: new Map(), maxTries: 5 };  // messaging.MAX_TRIES

// --- conversations ----------------------------------------------------------

function renderConvs() {
  const rows = [...state.convs.values()].sort((a, b) => b.ts - a.ts);
  $("#convs").replaceChildren(
    ...rows.map((c) =>
      el("li", { class: c.peer === state.peer ? "active" : "", "data-peer": c.peer },
        el("span", { class: "peer" }, c.peer),
        c.unread ? el("span", { class: "badge" }, String(c.unread)) : "",
        el("time", { "data-ts": c.ts }, ago(c.ts)),
        el("span", { class: "preview" }, (c.direction === "out" ? "You: " : "") + c.text),
      ),
    ),
  );
  $("#convs-empty").classList.toggle("hidden", rows.length > 0);
}

// Keep the summary list current from a message row.
function noteInConvs(m) {
  const prev = state.convs.get(m.peer);
  const unread = (prev?.unread ?? 0) + (m.direction === "in" && !m.read && m.peer !== state.peer ? 1 : 0);
  if (!prev || m.ts >= prev.ts) {
    state.convs.set(m.peer, { peer: m.peer, ts: m.ts, text: m.text, direction: m.direction, state: m.state, unread });
  } else prev.unread = unread;
  renderConvs();
}

// --- thread -----------------------------------------------------------------

function stateLabel(m) {
  if (m.direction === "in") return "";
  switch (m.state) {
    case "acked": return "✓ acked";
    case "rejected": return "✗ rejected";
    case "failed": return "✗ no ack";
    default: return m.tries ? `sending ${m.tries}/${state.maxTries}` : "waiting for TNC";
  }
}

function messageItem(m) {
  return el("li", { class: `msg ${m.direction} ${m.state}`, "data-id": m.id },
    el("div", { class: "bubble" }, m.text),
    el("div", { class: "meta" }, el("time", {}, clock(m.ts)), " ", stateLabel(m)),
  );
}

function renderThread() {
  const rows = [...state.messages.values()].sort((a, b) => a.id - b.id);
  $("#thread").replaceChildren(...rows.map(messageItem));
  const box = $("#thread-scroll");
  box.scrollTop = box.scrollHeight;
}

function upsertMessage(m) {
  if (m.peer === state.peer) {
    state.messages.set(m.id, m);
    renderThread();
  }
}

async function openPeer(peer) {
  state.peer = peer;
  $("#thread-title").textContent = peer ?? "New message";
  $("#to").value = peer ?? "";
  state.messages = new Map();
  renderThread();
  renderConvs();
  if (!peer) {
    $("#to").focus();
    return;
  }
  const rows = await getJSON(`api/messages?peer=${encodeURIComponent(peer)}&limit=200`);
  if (state.peer !== peer) return;  // switched again while loading
  state.messages = new Map(rows.map((m) => [m.id, m]));
  renderThread();
  markRead(peer);
  $("#text").focus();
}

async function markRead(peer) {
  const c = state.convs.get(peer);
  if (c) c.unread = 0;
  renderConvs();
  await fetch("api/messages/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ peer }),
  });
}

// --- compose ----------------------------------------------------------------

function showError(text) {
  const e = $("#error");
  e.textContent = text ?? "";
  e.classList.toggle("hidden", !text);
}

function updateCounter() {
  const left = MAX_TEXT - $("#text").value.length;
  $("#counter").textContent = String(left);
  $("#counter").classList.toggle("low", left < 10);
}

async function send(ev) {
  ev.preventDefault();
  const to = $("#to").value.trim().toUpperCase();
  const text = $("#text").value.trim();
  if (!to || !text) return;
  const r = await fetch("api/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to, text }),
  });
  if (!r.ok) {
    showError((await r.json().catch(() => ({}))).detail ?? `Send failed (${r.status})`);
    return;
  }
  showError(null);
  $("#text").value = "";
  updateCounter();
  if (state.peer !== to) await openPeer(to);
}

function renderCanned(list) {
  $("#canned").replaceChildren(
    ...list.map((t) => {
      const b = el("button", { type: "button", class: "small" }, t);
      b.onclick = () => {
        $("#text").value = t.slice(0, MAX_TEXT);
        updateCounter();
        $("#text").focus();
      };
      return b;
    }),
  );
}

function renderPeerOptions(favorites, stations) {
  const names = [...new Set([...favorites, ...stations.filter((s) => !s.is_object).map((s) => s.name)])];
  $("#peers").replaceChildren(...names.map((n) => el("option", { value: n })));
}

// --- data loading -----------------------------------------------------------

async function loadInitial() {
  const [status, config, convs, stations] = await Promise.all([
    getJSON("api/status"),
    getJSON("api/config"),
    getJSON("api/conversations"),
    getJSON("api/stations"),
  ]);
  renderHeader(status);
  renderCanned(config.canned_messages);
  renderPeerOptions(config.favorites, stations);
  state.convs = new Map(convs.map((c) => [c.peer, c]));
  renderConvs();
  await openPeer(state.peer ?? new URLSearchParams(location.search).get("to"));
}

function onEvent(type, data) {
  if (type === "status") renderHeader(data);
  else if (type === "message" || type === "ack") {
    upsertMessage(data);
    if (type === "message") noteInConvs(data);
    else if (state.convs.get(data.peer)?.ts === data.ts) {
      state.convs.get(data.peer).state = data.state;
    }
    if (type === "message" && data.direction === "in") {
      if (data.peer === state.peer && document.visibilityState === "visible") markRead(data.peer);
      else {
        const unread = $("#nav-unread");
        unread.textContent = String((parseInt(unread.textContent, 10) || 0) + 1);
      }
    }
  } else if (type === "read") {
    $("#nav-unread").textContent = data.unread ? String(data.unread) : "";
    const c = state.convs.get(data.peer);
    if (c) c.unread = 0;
    renderConvs();
  }
}

$("#convs").addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-peer]");
  if (li) openPeer(li.dataset.peer);
});
$("#new").onclick = () => openPeer(null);
$("#compose").onsubmit = (ev) => send(ev).catch((e) => showError(String(e)));
$("#text").oninput = updateCounter;
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.peer) markRead(state.peer);
});
setInterval(() => {
  for (const t of document.querySelectorAll("#convs time[data-ts]")) t.textContent = ago(Number(t.dataset.ts));
}, 10000);

const reload = () => loadInitial().catch((e) => console.error("initial load failed", e));
reload();
connectLive(onEvent, reload);
