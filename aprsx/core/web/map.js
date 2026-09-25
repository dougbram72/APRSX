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

async function loadInitial() {
  const [status, config, stations] = await Promise.all([
    getJSON("api/status"), getJSON("api/config"), getJSON("api/stations"),
  ]);
  state.units = status.units;
  renderHeader(status);
  state.config = config;
  showMe(status, config);
  for (const s of stations) upsertStation(s);
  fitAll();
}

function onEvent(type, data) {
  if (type === "status") {
    renderHeader(data);
    if (state.config) showMe(data, state.config);  // follows the GPS
  }
  else if (type === "station") upsertStation(data);
}

const reload = () => loadInitial().catch((e) => console.error("initial load failed", e));
reload();
connectLive(onEvent, reload);
