"use strict";

// System status: Pi health, links, GPS detail and receive audio levels.
// Polls api/system/status; the header follows the live event stream as on other pages.

const POLL_MS = 3000;
const SKY_MAX_DB = 50;
const POWER_TEXT = {
  undervoltage: "Undervoltage", arm_freq_capped: "CPU speed capped",
  throttled: "Throttled", soft_temp_limit: "Temperature limit",
};

function bytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
}

function duration(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
}

function fmt(v, digits = 1, unit = "") {
  return v == null ? "—" : `${Number(v).toFixed(digits)}${unit}`;
}

function kv(target, rows) {
  $(target).replaceChildren(...rows.filter(Boolean).flatMap(([k, v, cls]) =>
    [el("dt", {}, k), el("dd", cls ? { class: cls } : {}, v ?? "—")]));
}

function tone(ok) { return ok ? "good-text" : "bad-text"; }

// --- Pi --------------------------------------------------------------------

function renderPi(pi) {
  $("#pi-host").textContent = pi.hostname ? `(${pi.hostname})` : "";
  const t = pi.throttled;
  const now = t?.now ?? [], past = t?.since_boot ?? [];
  const alert = $("#power");
  alert.classList.toggle("hidden", !now.length && !past.length);
  alert.className = `alert ${now.length ? "bad" : "warn"}${!now.length && !past.length ? " hidden" : ""}`;
  alert.textContent = now.length
    ? `Now: ${now.map((f) => POWER_TEXT[f]).join(", ")}. Use a stronger power supply (5.1 V, 2.5 A or more).`
    : past.length ? `Since boot: ${past.map((f) => POWER_TEXT[f]).join(", ")} (not now).` : "";
  const mem = pi.mem;
  kv("#pi", [
    ["CPU", `${fmt(pi.cpu_percent, 0, "%")} · load ${pi.load?.join(" ") ?? "—"}` + (pi.arm_mhz ? ` · ${pi.arm_mhz} MHz` : "")],
    ["Temperature", fmt(pi.temp_c, 1, " °C"), pi.temp_c >= 80 ? "bad-text" : pi.temp_c >= 70 ? "warn-text" : ""],
    pi.core_volts != null && ["Core voltage", fmt(pi.core_volts, 3, " V")],
    ["Power", t ? (now.length ? "problem now" : past.length ? "OK now, dipped since boot" : "OK") : "—",
      t ? (now.length ? "bad-text" : past.length ? "warn-text" : "good-text") : ""],
    mem && ["Memory", `${bytes(mem.total - mem.available)} used of ${bytes(mem.total)}`
      + (mem.swap_total ? ` · swap ${bytes(mem.swap_total - mem.swap_free)}` : "")],
    ["aprsx-core", `${bytes(pi.core_rss)} RAM`],
    pi.disk && ["Disk", `${bytes(pi.disk.free)} free of ${bytes(pi.disk.total)}`,
      pi.disk.free / pi.disk.total < 0.1 ? "bad-text" : ""],
    ["Uptime", duration(pi.uptime_s)],
  ]);
  const services = Object.entries(pi.services ?? {});
  $("#services").replaceChildren(...services.map(([name, state]) =>
    el("li", { class: state === "active" ? "good" : state === "inactive" ? "" : "bad" }, `${name}: ${state}`)));
}

// --- links -----------------------------------------------------------------

function renderLinks(s) {
  const l = s.links, is = l.aprsis, m = l.mesh;
  kv("#links", [
    ...(l.radio === "ftm200" ? [
      ["Radio", "FTM-200D data port (receive only)"],
      ["FTM-200", l.ftm200.connected ? `open: ${l.ftm200.device} at ${l.ftm200.baud}`
        : `not open: ${l.ftm200.error ?? ""}`, tone(l.ftm200.connected)],
    ] : [
      ["TNC (KISS)", l.kiss ? "connected" : "not connected", tone(l.kiss)],
      ["Direwolf", l.direwolf_error ? `error: ${l.direwolf_error}` : l.direwolf_managed ? "managed by APRS-X" : "not managed",
        l.direwolf_error ? "bad-text" : ""],
    ]),
    ["APRS-IS", !is.enabled ? "off" : is.verified ? `logged in (${is.server ?? "?"})` : is.connected ? "connected, receive-only" : "not connected",
      is.enabled ? tone(is.verified) : ""],
    ["MeshCore", !m.enabled ? "off" : m.connected ? `connected: ${m.name ?? ""}` : `not connected: ${m.error ?? ""}`,
      m.enabled ? tone(m.connected) : ""],
    m.connected && m.radio && ["Mesh radio", `${m.radio.freq} MHz · BW ${m.radio.bw} · SF${m.radio.sf} · CR${m.radio.cr} · ${m.radio.tx_power} dBm`],
    ["Packets", `${s.counts.rx} received · ${s.counts.tx} sent since start`],
    ["Core uptime", duration(s.uptime_s)],
  ]);
}

// --- GPS -------------------------------------------------------------------

function renderGps(g) {
  const f = g.fix;
  const state = !g.connected ? ["gpsd not connected", "bad-text"]
    : g.used ? [`Using a ${f.mode === 3 ? "3D" : "2D"} fix`, "good-text"]
    : g.rejected ? [`Ignoring the receiver's fix: ${g.rejected}`, "bad-text"]
    : ["No fix: searching for satellites", "warn-text"];
  $("#gps-state").textContent = state[0];
  $("#gps-state").className = `state ${state[1]}`;
  const e = g.errors ?? {}, d = g.dop ?? {};
  kv("#gps-kv", [
    ["Position", f ? `${f.lat.toFixed(5)}, ${f.lon.toFixed(5)}` : "—"],
    ["Altitude", f?.alt_m != null ? `${f.alt_m.toFixed(0)} m` : "—"],
    ["Speed / course", f ? `${fmt(f.speed_kmh, 0, " km/h")} · ${fmt(f.course, 0, "°")}` : "—"],
    ["Satellites", `${g.sats_used ?? "—"} used of ${g.sats_seen ?? "—"} listed`],
    ["DOP", Object.keys(d).length ? `H ${fmt(d.hdop)} · V ${fmt(d.vdop)} · P ${fmt(d.pdop)}` : "—"],
    ["Error estimate", e.epx != null ? `±${fmt(Math.max(e.epx, e.epy ?? 0), 0)} m horiz` + (e.epv != null ? ` · ±${fmt(e.epv, 0)} m vert` : "") : "—"],
    ["GPS time", g.time ? new Date(g.time).toLocaleString() : "—"],
    ["Fix age", f ? `${f.age_s} s` : "—"],
  ]);
  const sats = [...(g.satellites ?? [])].sort((a, b) => (a.gnssid ?? 0) - (b.gnssid ?? 0) || a.PRN - b.PRN);
  $("#sky").replaceChildren(
    ...sats.map((s) => {
      const pct = Math.min(100, ((s.ss ?? 0) / SKY_MAX_DB) * 100);
      return el("div", { class: "sat", title: `PRN ${s.PRN} · ${s.ss ?? 0} dB-Hz · elevation ${s.el ?? "?"}° · ${s.used ? "used" : "not used"}` },
        el("span", { class: "ss" }, s.ss ? String(Math.round(s.ss)) : ""),
        el("div", { class: "bar-wrap" }, el("div", { class: `bar ${s.used ? "used" : ""}`, style: `height:${pct}%` })),
        el("span", { class: "prn" }, String(s.PRN)));
    }));
  if (!sats.length) $("#sky").append(el("p", { class: "empty" }, "No satellite data from gpsd."));
}

// --- audio -----------------------------------------------------------------

function renderAudio(a) {
  const advice = a.advice && Date.now() / 1000 - a.advice.ts < 600 ? a.advice : null;
  $("#audio-state").textContent = !a.available ? `Direwolf's log isn't available${a.error ? `: ${a.error}` : ""}.`
    : advice ? `Direwolf says the input level is too ${advice.too} (${ago(advice.ts)} ago). ${advice.too === "low" ? "Turn the radio's volume up" : "Turn the radio's volume down"}, so packets come in around 50.`
    : a.median_level != null ? "Receive level looks fine." : "Waiting for packets.";
  $("#audio-state").className = `state ${!a.available || advice ? "warn-text" : a.median_level != null ? "good-text" : ""}`;

  const med = a.median_level;
  $("#median").textContent = med ?? "—";
  $("#median-bar").style.left = `${Math.min(100, med ?? 0)}%`;
  $("#median-bar").classList.toggle("hidden", med == null);
  const st = a.stats;
  $("#noise").textContent = st ? st.level : "—";
  $("#noise-bar").style.width = `${Math.min(100, st?.level ?? 0)}%`;
  $("#noise-meta").textContent = st ? `(${st.rate_k} k samples/s, ${st.errors} errors, ${ago(st.ts)} ago)` : "(reported every 30 s)";

  $("#packets").replaceChildren(...a.packets.map((p) => el("tr", {},
    el("td", {}, clock(p.ts)),
    el("td", { class: "mono" }, p.station),
    el("td", {}, p.via ?? "direct"),
    el("td", {}, el("span", { class: `lvl ${p.level < 20 || p.level > 90 ? "bad-text" : p.level < 30 || p.level > 70 ? "warn-text" : "good-text"}` }, String(p.level))),
    el("td", {}, `${p.mark}/${p.space}`))));
  $("#packets-empty").classList.toggle("hidden", a.packets.length > 0);
}

// --- loading -----------------------------------------------------------------

async function poll() {
  try {
    const s = await getJSON("api/system/status");
    renderPi(s.pi);
    renderLinks(s);
    renderGps(s.gps);
    renderAudio(s.audio);
  } catch (e) {
    $("#gps-state").textContent = `Can't reach the core: ${e.message}`;
  }
}

poll();
setInterval(() => { if (document.visibilityState === "visible") poll(); }, POLL_MS);
getJSON("api/status").then(renderHeader).catch(() => {});
connectLive((type, data) => { if (type === "status") renderHeader(data); }, () => {});
