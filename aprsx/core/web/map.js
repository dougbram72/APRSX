"use strict";

// Stations heard on a Leaflet map. Tiles come from the core (tiles/…), which
// serves MBTiles packs and cached OSM tiles offline.

const DEFAULT_VIEW = { center: [39.8, -98.6], zoom: 4 };   // continental US
const STALE_S = 2 * 3600;

const state = { units: "imperial", markers: new Map(), me: null, fitted: false };

const map = L.map("map", { zoomControl: true, worldCopyJump: true });
L.tileLayer("tiles/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    + ' · symbols <a href="https://github.com/hessu/aprs-symbols">aprs.fi</a>',
}).addTo(map);

// Remember the view between visits (per browser; optional).
try {
  const saved = JSON.parse(localStorage.getItem("aprsx.mapView"));
  if (saved) { map.setView(saved.center, saved.zoom); state.fitted = true; }
} catch { /* storage unavailable */ }
if (!state.fitted) map.setView(DEFAULT_VIEW.center, DEFAULT_VIEW.zoom);
map.on("moveend", () => {
  try {
    const c = map.getCenter();
    localStorage.setItem("aprsx.mapView", JSON.stringify({ center: [c.lat, c.lng], zoom: map.getZoom() }));
  } catch { /* ignore */ }
});

function distance(km) {
  if (km == null) return "";
  return state.units === "metric" ? `${km.toFixed(1)} km` : `${(km * 0.621371).toFixed(1)} mi`;
}

function icon(table, code, label, cls = "") {
  const node = el("div", { class: `map-sym ${cls}` }, symbolEl(table, code), el("span", { class: "map-label" }, label));
  return L.divIcon({ className: "", html: node, iconSize: [24, 24], iconAnchor: [12, 12], popupAnchor: [0, -12] });
}

function popup(s) {
  const lines = [
    el("div", { class: "pop-name" }, symbolEl(s.symbol_table, s.symbol), s.name),
    s.comment ? el("div", { class: "pop-comment" }, s.comment) : "",
    el("div", { class: "pop-meta" },
      `Heard ${ago(s.last_heard)} ago`,
      s.distance_km != null ? ` · ${distance(s.distance_km)} ${s.bearing}°` : "",
      ` · ${s.channel === "is" ? "via APRS-IS" : s.heard_direct ? "direct" : s.path || ""}`),
  ];
  if (!s.is_object) {
    lines.push(el("a", { href: `chat.html?to=${encodeURIComponent(s.name)}`, class: "pop-link" }, "Message"));
  }
  return el("div", {}, ...lines);
}

function upsertStation(s) {
  if (s.lat == null || s.lon == null) return;
  const stale = [Date.now() / 1000 - s.last_heard > STALE_S ? "stale" : "",
                 s.channel === "is" ? "is" : ""].join(" ");
  let m = state.markers.get(s.name);
  if (!m) {
    m = L.marker([s.lat, s.lon]).addTo(map);
    state.markers.set(s.name, m);
  } else {
    m.setLatLng([s.lat, s.lon]);
  }
  m.setIcon(icon(s.symbol_table, s.symbol, s.name, stale));
  m.bindPopup(() => popup(s));
}

function showMe(status, config) {
  const { lat, lon } = status.position || {};
  if (lat == null || lon == null) return;
  const i = icon(config.symbol_table, config.symbol, status.station, "me");
  if (!state.me) state.me = L.marker([lat, lon], { icon: i, zIndexOffset: 1000 }).addTo(map);
  else state.me.setLatLng([lat, lon]).setIcon(i);
}

function fitAll() {
  if (state.fitted) return;
  const pts = [...state.markers.values()].map((m) => m.getLatLng());
  if (state.me) pts.push(state.me.getLatLng());
  if (pts.length) {
    map.fitBounds(L.latLngBounds(pts).pad(0.2), { maxZoom: 12 });
    state.fitted = true;
  }
}

// --- MeshCore: nodes and war-drive coverage --------------------------------

const MESH_TYPES = { 1: ["companion", "#8250df"], 2: ["repeater", "#0969da"], 3: ["room", "#bf8700"], 4: ["sensor", "#57606a"] };
const onlySession = Number(new URLSearchParams(location.search).get("wardrive")) || null;
const mesh = {
  nodes: L.layerGroup().addTo(map), coverage: L.layerGroup().addTo(map), links: L.layerGroup().addTo(map),
  nodeMarkers: new Map(), pingMarkers: new Map(), pings: new Map(),
};
L.control.layers(null, { "Mesh nodes": mesh.nodes, "Coverage pings": mesh.coverage, "Ping answers": mesh.links },
  { collapsed: true }).addTo(map);

const legend = L.control({ position: "bottomleft" });
legend.onAdd = () => el("div", { class: "cov-legend hidden", id: "cov-legend" },
  ...[["#1a7f37", "SNR ≥ 5"], ["#9a6700", "0 – 5"], ["#d1242f", "< 0"], ["#8c959f", "no answer"]]
    .flatMap(([c, t]) => [el("i", { style: `background:${c}` }), t, el("br")]));
legend.addTo(map);

function snrColor(p) {
  if (!p.heard) return "#8c959f";
  return p.best_snr == null || p.best_snr >= 5 ? "#1a7f37" : p.best_snr >= 0 ? "#9a6700" : "#d1242f";
}

function upsertMeshNode(n) {
  if (n.lat == null || n.lon == null) return;
  const [type, color] = MESH_TYPES[n.type] ?? ["node", "#57606a"];
  let m = mesh.nodeMarkers.get(n.pubkey);
  if (!m) {
    m = L.circleMarker([n.lat, n.lon], { radius: 7, weight: 2, fillOpacity: 0.8 }).addTo(mesh.nodes);
    mesh.nodeMarkers.set(n.pubkey, m);
  }
  m.setLatLng([n.lat, n.lon]).setStyle({ color: "#fff", fillColor: color });
  m.bindTooltip(n.name ?? n.pubkey.slice(0, 8), { direction: "right", offset: [8, 0] });
  m.bindPopup(() => el("div", {},
    el("div", { class: "pop-name" }, n.name ?? n.pubkey.slice(0, 12)),
    el("div", { class: "pop-meta" }, `MeshCore ${type} · heard ${ago(n.last_heard)} ago`
      + (n.snr != null ? ` · SNR ${n.snr}` : "") + (n.hops != null ? ` · ${n.hops} hops` : ""))));
}

function upsertPing(p) {
  if (p.kind === "advert" || (onlySession && p.session_id !== onlySession)) return;
  mesh.pings.set(p.id, p);
  let m = mesh.pingMarkers.get(p.id);
  if (!m) {
    m = L.circleMarker([p.lat, p.lon], { radius: 6, weight: 1, fillOpacity: 0.85 }).addTo(mesh.coverage);
    mesh.pingMarkers.set(p.id, m);
  }
  m.setStyle({ color: "#fff", fillColor: snrColor(p) });
  m.bindPopup(() => el("div", {},
    el("div", { class: "pop-name" }, p.kind === "chan" ? "Channel ping" : "Discovery"),
    el("div", { class: "pop-meta" }, `${new Date(p.ts * 1000).toLocaleString()} · `
      + (p.heard ? `${p.heard} heard · best SNR ${p.best_snr}` : "no answer"))));
  $("#cov-legend")?.classList.remove("hidden");
}

function addAnswer(o) {
  if (o.node_lat == null || o.node_lon == null) return;
  const p = mesh.pings.get(o.ping_id);
  if (!p) return;
  L.polyline([[o.my_lat, o.my_lon], [o.node_lat, o.node_lon]],
    { color: snrColor({ heard: 1, best_snr: o.snr }), weight: 2, opacity: 0.6, dashArray: o.kind === "echo" ? "4 4" : null })
    .bindTooltip(`${o.node_name ?? o.node} · SNR ${o.snr}` + (o.remote_snr != null ? ` / ${o.remote_snr}` : ""))
    .addTo(mesh.links);
}

async function loadMesh() {
  const [nodes, cov] = await Promise.all([
    getJSON("api/mesh/nodes"),
    getJSON(`api/wardrive/coverage${onlySession ? `?session=${onlySession}` : ""}`),
  ]);
  mesh.links.clearLayers();
  for (const n of nodes) upsertMeshNode(n);
  for (const p of cov.pings) upsertPing(p);
  for (const o of cov.obs) addAnswer(o);
  if (onlySession && cov.pings.length) {
    map.fitBounds(L.latLngBounds(cov.pings.map((p) => [p.lat, p.lon])).pad(0.2), { maxZoom: 15 });
    state.fitted = true;
  }
}

async function loadInitial() {
  const [status, config, stations] = await Promise.all([
    getJSON("api/status"), getJSON("api/config"), getJSON("api/stations"),
  ]);
  state.units = status.units;
  renderHeader(status);
  state.config = config;
  showMe(status, config);
  for (const s of stations) upsertStation(s);
  await loadMesh();
  fitAll();
}

function onEvent(type, data) {
  if (type === "status") {
    renderHeader(data);
    if (state.config) showMe(data, state.config);  // follows the GPS
  }
  else if (type === "station") upsertStation(data);
  else if (type === "mesh_node") upsertMeshNode(data);
  else if (type === "wardrive_ping") upsertPing(data);
  else if (type === "wardrive_obs") addAnswer(data);
}

const reload = () => loadInitial().catch((e) => console.error("initial load failed", e));
reload();
connectLive(onEvent, reload);
