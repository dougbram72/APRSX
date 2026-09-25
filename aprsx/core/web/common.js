"use strict";

// Helpers shared by every page.

const $ = (sel) => document.querySelector(sel);

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

// APRS symbol from the sprite sheets in aprsx/symbols: 16x6 cells, index = code - 33.
// table "/" = primary, "\\" = alternate, 0-9/A-Z = alternate with that overlay.
const SYMBOL_PX = 24;

function symbolCell(sheet, index) {
  const x = -(index % 16) * SYMBOL_PX;
  const y = -Math.floor(index / 16) * SYMBOL_PX;
  return el("span", {
    class: "sym",
    style: `background-image:url(symbols/aprs-symbols-64-${sheet}.png);background-position:${x}px ${y}px`,
  });
}

function symbolEl(table, code) {
  const index = code ? code.charCodeAt(0) - 33 : -1;
  if (!table || index < 0 || index >= 96) return el("span", { class: "sym" });
  const node = symbolCell(table === "/" ? 0 : 1, index);
  if (/^[0-9A-Z]$/.test(table)) node.append(symbolCell(2, table.charCodeAt(0) - 33));
  return node;
}

function setPill(id, text, cls) {
  const p = $(id);
  p.textContent = text;
  p.className = `pill ${cls ?? ""}`;
}

function renderHeader(s) {
  $("#station").textContent = s.station;
  setPill("#tnc", s.kiss_connected ? "TNC connected" : "TNC offline", s.kiss_connected ? "good" : "bad");
  const is = $("#is");
  if (is) {
    is.classList.toggle("hidden", !s.aprsis_enabled);
    setPill("#is", s.aprsis_verified ? "APRS-IS" : "APRS-IS offline", s.aprsis_verified ? "good" : "bad");
  }
  const gps = $("#gps");
  if (gps) {
    const text = s.gps_fix ? `GPS ${s.gps.mode === 3 ? "3D" : "2D"}` + (s.gps.sats ? ` · ${s.gps.sats} sats` : "")
      : s.gps_connected ? "GPS searching" : "GPS —";
    setPill("#gps", text, s.gps_fix ? "good" : s.gps_connected ? "warn" : "");
  }
  const beacon = $("#beacon");
  if (beacon) {
    beacon.disabled = !s.can_beacon;
    beacon.title = s.last_beacon ? `Last beacon ${clock(s.last_beacon)}` : "Send a position beacon now";
  }
  const unread = $("#nav-unread");
  if (unread) unread.textContent = s.unread ? String(s.unread) : "";
}

// Live event stream. onOpen runs after every (re)connect so the page can
// reload over REST whatever it missed while disconnected.
function connectLive(onEvent, onOpen) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    setPill("#link", "Live", "good");
    onOpen();
  };
  ws.onmessage = (ev) => {
    const { type, data } = JSON.parse(ev.data);
    onEvent(type, data);
  };
  ws.onclose = () => {
    setPill("#link", "Reconnecting…", "bad");
    setTimeout(() => connectLive(onEvent, onOpen), 3000);
  };
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// The header's Beacon button (every page has one).
async function sendBeacon() {
  const b = $("#beacon");
  b.disabled = true;
  const r = await fetch("api/beacon", { method: "POST" }).catch(() => null);
  const data = r ? await r.json().catch(() => ({})) : {};
  b.textContent = r?.ok ? "Sent ✓" : "Not sent";
  if (!r?.ok) b.title = data.detail ?? "Core not reachable";
  setTimeout(() => { b.textContent = "Beacon"; b.disabled = false; }, 2500);
}
document.addEventListener("DOMContentLoaded", () => {
  const b = $("#beacon");
  if (b) b.onclick = sendBeacon;
});
