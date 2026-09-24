"use strict";

const MAX_PACKETS = 300;
const $ = (sel) => document.querySelector(sel);

const state = { stations: new Map(), units: "imperial" };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c ?? "");
  return node;
}

function ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

function distance(km) {
  if (km == null) return "";
  return state.units === "metric" ? `${km.toFixed(1)} km` : `${(km * 0.621371).toFixed(1)} mi`;
}

// --- status -----------------------------------------------------------------

function setPill(id, text, cls) {
  const p = $(id);
  p.textContent = text;
  p.className = `pill ${cls ?? ""}`;
}

function renderStatus(s) {
  state.units = s.units;
  $("#station").textContent = s.station;
  setPill("#tnc", s.kiss_connected ? "TNC connected" : "TNC offline", s.kiss_connected ? "good" : "bad");
  setPill("#rx", `${s.rx_count} rx`);
}

// --- stations ---------------------------------------------------------------

function renderStations() {
  const rows = [...state.stations.values()].sort((a, b) => b.last_heard - a.last_heard);
  const body = $("#stations tbody");
  body.replaceChildren(
    ...rows.map((s) =>
      el("tr", { "data-name": s.name },
        el("td", { class: "name" }, s.name),
        el("td", { "data-ts": s.last_heard }, ago(s.last_heard)),
        el("td", { class: "num" }, distance(s.distance_km)),
        el("td", { class: "num" }, s.bearing == null ? "" : `${s.bearing}°`),
        el("td", s.heard_direct ? { class: "direct" } : {}, s.heard_direct ? "direct" : s.path),
        el("td", { class: "comment" }, s.comment ?? ""),
      ),
    ),
  );
  $("#station-count").textContent = rows.length ? `(${rows.length})` : "";
  $("#stations-empty").classList.toggle("hidden", rows.length > 0);
}

function upsertStation(s) {
  state.stations.set(s.name, s);
  renderStations();
  document.querySelector(`#stations tr[data-name="${CSS.escape(s.name)}"]`)?.classList.add("fresh");
}

// --- packets ----------------------------------------------------------------

function packetItem(p) {
  return el("li", {},
    el("time", {}, clock(p.ts)),
    p.raw,
    p.format ? el("span", { class: "fmt" }, p.format) : "",
  );
}

function addPacket(p, fresh = true) {
  const list = $("#packets");
  const li = packetItem(p);
  if (fresh) li.classList.add("fresh");
  list.prepend(li);
  while (list.children.length > MAX_PACKETS) list.lastChild.remove();
  $("#packets-empty").classList.add("hidden");
}

// --- data loading -----------------------------------------------------------

// Full refresh over REST: on page load and after every (re)connect, so the
// page shows current data even if the live WebSocket is down.
async function loadInitial() {
  const [status, stations, packets] = await Promise.all([
    fetch("api/status").then((r) => r.json()),
    fetch("api/stations").then((r) => r.json()),
    fetch(`api/packets?limit=${MAX_PACKETS}`).then((r) => r.json()),
  ]);
  renderStatus(status);
  state.stations = new Map(stations.map((s) => [s.name, s]));
  renderStations();
  $("#packets").replaceChildren(...packets.map(packetItem));
  $("#packets-empty").classList.toggle("hidden", packets.length > 0);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    setPill("#link", "Live", "good");
    loadInitial().catch((e) => console.error("initial load failed", e));
  };
  ws.onmessage = (ev) => {
    const { type, data } = JSON.parse(ev.data);
    if (type === "status") renderStatus(data);
    else if (type === "packet") {
      addPacket(data);
      const rx = $("#rx");
      rx.textContent = `${parseInt(rx.textContent, 10) + 1} rx`;
    } else if (type === "station") upsertStation(data);
  };
  ws.onclose = () => {
    setPill("#link", "Reconnecting…", "bad");
    setTimeout(connect, 3000);
  };
}

// Keep "heard N ago" fresh without re-rendering everything.
setInterval(() => {
  for (const td of document.querySelectorAll("#stations td[data-ts]")) {
    td.textContent = ago(Number(td.dataset.ts));
  }
}, 10000);

loadInitial().catch((e) => console.error("initial load failed", e));
connect();
