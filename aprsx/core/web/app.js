"use strict";

const MAX_PACKETS = 300;
const state = { stations: new Map(), units: "imperial" };

function distance(km) {
  if (km == null) return "";
  return state.units === "metric" ? `${km.toFixed(1)} km` : `${(km * 0.621371).toFixed(1)} mi`;
}

// --- status -----------------------------------------------------------------

function renderStatus(s) {
  state.units = s.units;
  renderHeader(s);
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
  return el("li", p.direction === "tx" ? { class: "tx" } : {},
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
    getJSON("api/status"),
    getJSON("api/stations"),
    getJSON(`api/packets?limit=${MAX_PACKETS}`),
  ]);
  renderStatus(status);
  state.stations = new Map(stations.map((s) => [s.name, s]));
  renderStations();
  $("#packets").replaceChildren(...packets.map(packetItem));
  $("#packets-empty").classList.toggle("hidden", packets.length > 0);
}

function onEvent(type, data) {
  if (type === "status") renderStatus(data);
  else if (type === "packet") {
    addPacket(data);
    if (data.direction === "rx") {
      const rx = $("#rx");
      rx.textContent = `${parseInt(rx.textContent, 10) + 1} rx`;
    }
  } else if (type === "station") upsertStation(data);
  else if (type === "message" && data.direction === "in") {
    const unread = $("#nav-unread");
    unread.textContent = String((parseInt(unread.textContent, 10) || 0) + 1);
  } else if (type === "read") $("#nav-unread").textContent = data.unread ? String(data.unread) : "";
}

// Keep "heard N ago" fresh without re-rendering everything.
setInterval(() => {
  for (const td of document.querySelectorAll("#stations td[data-ts]")) {
    td.textContent = ago(Number(td.dataset.ts));
  }
}, 10000);

const reload = () => loadInitial().catch((e) => console.error("initial load failed", e));
reload();
connectLive(onEvent, reload);
