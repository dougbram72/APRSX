"use strict";

// MeshCore: channels and direct messages, nodes heard, war-driving.
// Conversations are "ch:<index>" or "dm:<pubkey prefix>" (see aprsx/core/mesh.py).

const MAX_TEXT = 140;
const TYPES = { 1: "companion", 2: "repeater", 3: "room", 4: "sensor" };

const state = {
  conv: null, messages: new Map(), convs: new Map(), channels: [], nodes: new Map(),
  units: "imperial", mesh: null, answers: [],
};

const nodeFor = (prefix) => [...state.nodes.values()].find((n) => n.pubkey.startsWith(prefix));

function convName(conv) {
  if (conv.startsWith("ch:")) {
    const idx = Number(conv.slice(3));
    return state.channels.find((c) => c.idx === idx)?.name ?? `Channel ${idx}`;
  }
  return nodeFor(conv.slice(3))?.name ?? conv.slice(3);
}

function distance(km) {
  if (km == null) return "";
  return state.units === "metric" ? `${km.toFixed(1)} km` : `${(km * 0.621371).toFixed(1)} mi`;
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
  });
  if (r.status === 401) throw new Error("A settings password is set: log in on the Settings page first.");
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `Failed (${r.status})`);
  return data;
}

function showError(text) {
  $("#error").textContent = text ?? "";
  $("#error").classList.toggle("hidden", !text);
}

// --- conversations ----------------------------------------------------------

function renderConvs() {
  // Every channel on the device is listed, even before it has messages.
  const rows = new Map(state.channels.map((c) => [`ch:${c.idx}`, { conv: `ch:${c.idx}`, ts: 0, text: "", unread: 0 }]));
  for (const c of state.convs.values()) rows.set(c.conv, c);
  const list = [...rows.values()].sort((a, b) => b.ts - a.ts);
  $("#convs").replaceChildren(...list.map((c) =>
    el("li", { class: c.conv === state.conv ? "active" : "", "data-conv": c.conv },
      el("span", { class: "peer" }, (c.conv.startsWith("ch:") ? "" : "✉ ") + convName(c.conv)),
      c.unread ? el("span", { class: "badge" }, String(c.unread)) : "",
      c.ts ? el("time", { "data-ts": c.ts }, ago(c.ts)) : el("time"),
      el("span", { class: "preview" }, c.text ? (c.direction === "out" ? "You: " : c.sender ? `${c.sender}: ` : "") + c.text : ""),
    )));
  $("#convs-empty").classList.toggle("hidden", list.length > 0);
}

function noteInConvs(m) {
  const prev = state.convs.get(m.conv);
  const unread = (prev?.unread ?? 0) + (m.direction === "in" && !m.read && m.conv !== state.conv ? 1 : 0);
  if (!prev || m.ts >= prev.ts) state.convs.set(m.conv, { ...m, unread });
  else prev.unread = unread;
  renderConvs();
}

// --- thread -----------------------------------------------------------------

function stateLabel(m) {
  if (m.direction === "in") return [m.snr != null ? `SNR ${m.snr}` : "", m.hops != null ? `${m.hops} hops` : ""].filter(Boolean).join(" · ");
  switch (m.state) {
    case "acked": return "✓ delivered";
    case "sent": return "sent";
    case "failed": return "✗ no ack";
    default: return m.attempts ? `sending ${m.attempts}/3` : "waiting for device";
  }
}

function renderThread() {
  const rows = [...state.messages.values()].sort((a, b) => a.id - b.id);
  $("#thread").replaceChildren(...rows.map((m) =>
    el("li", { class: `msg ${m.direction} ${m.state}` },
      m.sender && m.direction === "in" ? el("div", { class: "meta" }, m.sender) : "",
      el("div", { class: "bubble" }, m.text),
      el("div", { class: "meta" }, el("time", {}, clock(m.ts)), " ", stateLabel(m)))));
  $("#thread-scroll").scrollTop = $("#thread-scroll").scrollHeight;
}

async function openConv(conv) {
  state.conv = conv;
  state.messages = new Map();
  $("#thread-title").textContent = conv ? convName(conv) : "Pick a channel or node";
  for (const n of [$("#text"), $("#compose button")]) n.disabled = !conv;
  renderThread();
  renderConvs();
  if (!conv) return;
  const rows = await getJSON(`api/mesh/messages?conv=${encodeURIComponent(conv)}&limit=200`);
  if (state.conv !== conv) return;
  state.messages = new Map(rows.map((m) => [m.id, m]));
  renderThread();
  markRead(conv);
  $("#text").focus();
}

async function markRead(conv) {
  const c = state.convs.get(conv);
  if (c) c.unread = 0;
  renderConvs();
  await post("api/mesh/messages/read", { conv }).catch(() => {});
}

async function send(ev) {
  ev.preventDefault();
  const text = $("#text").value.trim();
  if (!state.conv || !text) return;
  try {
    await post("api/mesh/messages", { conv: state.conv, text });
  } catch (e) {
    showError(e.message);
    return;
  }
  showError(null);
  $("#text").value = "";
  updateCounter();
}

function updateCounter() {
  const left = MAX_TEXT - new TextEncoder().encode($("#text").value).length;
  $("#counter").textContent = String(left);
  $("#counter").classList.toggle("low", left < 10);
}

// --- nodes ------------------------------------------------------------------

function renderNodes() {
  const rows = [...state.nodes.values()].sort((a, b) => b.last_heard - a.last_heard);
  $("#nodes-count").textContent = rows.length ? `(${rows.length})` : "";
  $("#nodes").replaceChildren(...rows.map((n) => {
    const dm = el("button", { type: "button", class: "small" }, "Message");
    dm.onclick = () => openConv(`dm:${n.pubkey.slice(0, 12)}`);
    return el("tr", {},
      el("td", { class: "mono", title: n.pubkey }, n.name ?? n.pubkey.slice(0, 12)),
      el("td", {}, TYPES[n.type] ?? ""),
      el("td", {}, n.snr ?? ""), el("td", {}, n.rssi ?? ""), el("td", {}, n.hops ?? ""),
      el("td", {}, distance(n.distance_km)),
      el("td", { "data-ts": n.last_heard }, ago(n.last_heard)),
      el("td", {}, n.type === 1 ? dm : ""));
  }));
}

// --- war-drive --------------------------------------------------------------

function renderWardrive() {
  const m = state.mesh;
  $("#mesh-off").classList.toggle("hidden", !!m?.enabled);
  $("#node-name").textContent = m?.connected ? `${m.name ?? ""} ` : `${m?.error ?? "Device offline"} `;
  const wd = m?.wardrive ?? {};
  $("#wd-toggle").textContent = wd.active ? "Stop" : "Start";
  $("#wd-toggle").classList.toggle("secondary", !!wd.active);
  $("#wd-state").textContent = !wd.active ? "Not running."
    : wd.paused ? `Paused: ${wd.paused}.`
    : wd.listening ? `Listening for answers to a ${wd.listening === "chan" ? "channel ping" : "discovery"}…`
    : `Running since ${clock(wd.started)}.`;
  $("#wd-pings").textContent = wd.pings ?? 0;
  $("#wd-heard").textContent = wd.pings_heard ?? 0;
  $("#wd-nodes").textContent = wd.nodes ?? 0;
  $("#wd-rx").textContent = wd.rx ?? 0;
  for (const b of [$("#advert"), $("#advert-flood")]) b.disabled = !m?.connected;
}

function renderAnswers() {
  $("#wd-answers").replaceChildren(...state.answers.map((o) =>
    el("li", {}, el("time", {}, clock(o.ts)), " ",
      `${o.kind === "echo" ? "echo" : "answer"} from `,
      el("b", {}, o.node_name ?? o.node ?? "?"),
      ` · SNR ${o.snr ?? "?"}` + (o.remote_snr != null ? ` (they heard us ${o.remote_snr})` : ""))));
}

function fmtLength(s) {
  const secs = (s.ended ?? Date.now() / 1000) - s.started;
  return secs < 3600 ? `${Math.round(secs / 60)} min` : `${(secs / 3600).toFixed(1)} h`;
}

async function loadSessions() {
  const rows = await getJSON("api/wardrive/sessions");
  $("#sessions").replaceChildren(...rows.map((s) => el("tr", {},
    el("td", {}, new Date(s.started * 1000).toLocaleString()),
    el("td", {}, s.ended ? fmtLength(s) : "running"),
    el("td", {}, s.pings), el("td", {}, s.pings_heard), el("td", {}, s.nodes),
    el("td", {}, ...["geojson", "csv", "gpx"].flatMap((f) => [
      el("a", { href: `api/wardrive/sessions/${s.id}/export?fmt=${f}` }, f), " "]),
      el("a", { href: `map.html?wardrive=${s.id}` }, "map")))));
}

async function toggleWardrive() {
  const b = $("#wd-toggle");
  b.disabled = true;
  try {
    const st = await post(`api/wardrive/${state.mesh?.wardrive?.active ? "stop" : "start"}`);
    state.mesh = { ...state.mesh, wardrive: st };
    if (st.active) state.answers = [];
    renderWardrive();
    renderAnswers();
    loadSessions();
  } catch (e) {
    $("#wd-state").textContent = e.message;
  } finally {
    b.disabled = false;
  }
}

async function advert(flood) {
  const b = flood ? $("#advert-flood") : $("#advert");
  b.disabled = true;
  try {
    await post("api/mesh/advert", { flood });
    b.textContent = "Sent ✓";
  } catch (e) {
    b.textContent = "Not sent";
    b.title = e.message;
  }
  setTimeout(() => { b.textContent = flood ? "Flood advert" : "Advert"; b.disabled = false; }, 2500);
}

// --- data loading -----------------------------------------------------------

async function loadInitial() {
  const [status, channels, convs, nodes] = await Promise.all([
    getJSON("api/status"), getJSON("api/mesh/channels"), getJSON("api/mesh/conversations"),
    getJSON("api/mesh/nodes"),
  ]);
  renderHeader(status);
  state.units = status.units;
  state.mesh = status.mesh;
  state.channels = channels;
  state.convs = new Map(convs.map((c) => [c.conv, c]));
  state.nodes = new Map(nodes.map((n) => [n.pubkey, n]));
  renderWardrive();
  renderNodes();
  renderConvs();
  await openConv(state.conv);
  await loadSessions();
}

function onEvent(type, data) {
  if (type === "status") {
    renderHeader(data);
    const had = state.mesh?.connected;
    state.mesh = data.mesh;
    renderWardrive();
    if (data.mesh?.connected && !had) getJSON("api/mesh/channels").then((c) => { state.channels = c; renderConvs(); });
  } else if (type === "wardrive") {
    state.mesh = { ...state.mesh, wardrive: data };
    renderWardrive();
  } else if (type === "wardrive_obs") {
    state.answers = [data, ...state.answers].slice(0, 8);
    renderAnswers();
  } else if (type === "mesh_message" || type === "mesh_ack") {
    if (data.conv === state.conv) {
      state.messages.set(data.id, data);
      renderThread();
    }
    if (type === "mesh_message") {
      noteInConvs(data);
      if (data.direction === "in" && data.conv === state.conv && document.visibilityState === "visible") markRead(data.conv);
    }
  } else if (type === "mesh_read") {
    $("#nav-mesh-unread").textContent = data.unread ? String(data.unread) : "";
  } else if (type === "mesh_node") {
    const prev = state.nodes.get(data.pubkey);
    state.nodes.set(data.pubkey, { ...data, distance_km: prev?.distance_km });
    renderNodes();
  } else if (type === "mesh_nodes") {
    getJSON("api/mesh/nodes").then((rows) => { state.nodes = new Map(rows.map((n) => [n.pubkey, n])); renderNodes(); renderConvs(); });
  }
}

$("#convs").addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-conv]");
  if (li) openConv(li.dataset.conv);
});
$("#compose").onsubmit = send;
$("#text").oninput = updateCounter;
$("#wd-toggle").onclick = toggleWardrive;
$("#advert").onclick = () => advert(false);
$("#advert-flood").onclick = () => advert(true);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.conv) markRead(state.conv);
});
setInterval(() => {
  for (const t of document.querySelectorAll("time[data-ts], td[data-ts]")) t.textContent = ago(Number(t.dataset.ts));
}, 10000);

const reload = () => loadInitial().catch((e) => console.error("initial load failed", e));
reload();
connectLive(onEvent, reload);
