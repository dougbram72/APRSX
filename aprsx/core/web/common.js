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

function setPill(id, text, cls) {
  const p = $(id);
  p.textContent = text;
  p.className = `pill ${cls ?? ""}`;
}

function renderHeader(s) {
  $("#station").textContent = s.station;
  setPill("#tnc", s.kiss_connected ? "TNC connected" : "TNC offline", s.kiss_connected ? "good" : "bad");
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
