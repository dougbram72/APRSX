"use strict";

// Settings form. Inputs are bound to config keys by their name attribute
// ("aprsis.port" = config.aprsis.port); data-type says how to convert.

const form = $("#settings");
const state = { config: null, symbol: { table: "/", code: ">" } };

function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  const target = keys.reduce((o, k) => (o[k] ??= {}), obj);
  target[last] = value;
}

function fields() {
  return [...form.querySelectorAll("[name]")].filter((f) => !("skip" in f.dataset));
}

function fillForm(config) {
  for (const f of fields()) {
    const v = getPath(config, f.name);
    if (f.type === "checkbox") f.checked = !!v;
    else if (f.dataset.type === "csv") f.value = (v || []).join(",");
    else if (f.dataset.type === "lines") f.value = (v || []).join("\n");
    else f.value = v ?? "";
  }
  state.symbol = { table: config.symbol_table, code: config.symbol };
  renderSymbolPicker();
}

function readForm() {
  const out = structuredClone(state.config);
  for (const f of fields()) {
    let v;
    if (f.type === "checkbox") v = f.checked;
    else if (f.dataset.type === "csv") v = f.value.split(",").map((s) => s.trim()).filter(Boolean);
    else if (f.dataset.type === "lines") v = f.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else if (f.dataset.type === "optnum") v = f.value === "" ? null : Number(f.value);
    else if (f.type === "number") v = f.value === "" ? null : Number(f.value);
    else v = f.value.trim();
    setPath(out, f.name, v);
  }
  out.symbol_table = state.symbol.table;
  out.symbol = state.symbol.code;
  return out;
}

// --- symbol picker ------------------------------------------------------------

function tableKind(table) {
  return table === "/" ? "/" : table === "\\" ? "\\" : "overlay";
}

function renderSymbolPicker() {
  const { table, code } = state.symbol;
  const kind = tableKind(table);
  form.elements.symbol_table_kind.value = kind;
  $("#symbol-overlay").classList.toggle("hidden", kind !== "overlay");
  if (kind === "overlay") $("#symbol-overlay").value = table;
  $("#symbol-preview").replaceChildren(symbolEl(table, code), el("code", {}, table + code));

  const gridTable = kind === "/" ? "/" : "\\";
  const cells = [];
  for (let i = 0; i < 94; i++) {
    const c = String.fromCharCode(33 + i);
    const b = el("button", { type: "button", title: gridTable + c, class: c === code ? "picked" : "" },
      symbolEl(gridTable, c));
    b.onclick = () => { state.symbol.code = c; renderSymbolPicker(); };
    cells.push(b);
  }
  $("#symbol-grid").replaceChildren(...cells);
}

form.elements.symbol_table_kind.onchange = (e) => {
  const kind = e.target.value;
  state.symbol.table = kind === "overlay" ? ($("#symbol-overlay").value.toUpperCase() || "1") : kind;
  renderSymbolPicker();
};
$("#symbol-overlay").oninput = (e) => {
  const c = e.target.value.toUpperCase();
  if (/^[0-9A-Z]$/.test(c)) { state.symbol.table = c; renderSymbolPicker(); }
};

// --- devices, previews ----------------------------------------------------------

function renderDevices(devices) {
  $("#audio-devices").replaceChildren(
    ...devices.audio.map((d) => el("option", { value: d.id }, d.name)));
  const ptt = [el("option", { value: "CM108" }, "CM108/CM119 GPIO (sound card)")];
  for (const s of devices.serial) {
    ptt.push(el("option", { value: `${s.id} RTS` }, `${s.name} (RTS)`));
    ptt.push(el("option", { value: `${s.id} DTR` }, `${s.name} (DTR)`));
  }
  $("#ptt-options").replaceChildren(...ptt);
  $("#mesh-devices").replaceChildren(...devices.serial.map((d) => el("option", { value: d.id }, d.name)));
}

function renderMesh(status) {
  const m = status.mesh ?? {};
  $("#mesh-state").textContent = !m.enabled ? "MeshCore is off."
    : m.connected ? `Connected to ${m.name ?? "the device"}` + (m.radio?.freq ? ` (${m.radio.freq} MHz, SF${m.radio.sf}, BW ${m.radio.bw}).` : ".")
    : `Not connected: ${m.error ?? "retrying"}.`;
}

async function refreshPreviews() {
  $("#direwolf-conf").textContent = await fetch("api/direwolf.conf").then((r) => r.text());
  const t = await getJSON("api/tiles");
  $("#tile-status").textContent = t.packs.length
    ? `MBTiles packs: ${t.packs.join(", ")}` : "No MBTiles packs installed.";
}

function renderAprsIs(status) {
  const s = !status.aprsis_enabled ? "APRS-IS is off."
    : status.aprsis_verified ? `Logged in to ${status.aprsis_server || "APRS-IS"} (verified).`
    : status.aprsis_connected ? "Connected, receive-only (passcode not accepted)."
    : "Not connected: no internet, or the server can't be reached. Retrying.";
  const g = status.gated || {};
  $("#aprsis-state").textContent = `${s} Gated: ${g.rf_to_is ?? 0} RF→IS, ${g.is_to_rf ?? 0} IS→RF.`;
}

$("#calc-passcode").onclick = async () => {
  const call = form.elements.callsign.value.trim();
  if (!call) return;
  const r = await getJSON(`api/aprsis/passcode?callsign=${encodeURIComponent(call)}`);
  form.elements["aprsis.passcode"].value = r.passcode;
};

function renderPassword(config) {
  $("#password-state").textContent = config.has_password
    ? "A password is set. Enter a new one to change it." : "No password: anyone on this network can change settings.";
  $("#remove-password").classList.toggle("hidden", !config.has_password);
}

// --- auth -------------------------------------------------------------------

function setLocked(locked) {
  $("#login").classList.toggle("hidden", !locked);
  form.querySelectorAll("input, select, textarea, button").forEach((f) => { f.disabled = locked; });
}

$("#login").onsubmit = async (e) => {
  e.preventDefault();
  const r = await fetch("api/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("#login-password").value }),
  });
  $("#login-error").classList.toggle("hidden", r.ok);
  if (!r.ok) { $("#login-error").textContent = "Wrong password."; return; }
  $("#login-password").value = "";
  setLocked(false);
};

// --- save -------------------------------------------------------------------

function clearErrors() {
  form.querySelectorAll(".field-error").forEach((n) => n.remove());
  form.querySelectorAll(".invalid").forEach((n) => n.classList.remove("invalid"));
}

function showErrors(detail) {
  let first = null;
  for (const { field, msg } of detail) {
    // "path.1" -> the path input; "symbol" -> the picker.
    const name = field.replace(/\.\d+$/, "");
    const input = form.querySelector(`[name="${CSS.escape(name)}"]`) || $("#symbol-preview");
    input.classList.add("invalid");
    input.closest("label, .symbol-row")?.append(el("span", { class: "field-error" }, msg.replace(/^Value error, /, "")));
    first ??= input;
  }
  first?.scrollIntoView({ block: "center" });
}

async function save(body, message) {
  clearErrors();
  const result = $("#save-result");
  result.className = "";
  result.textContent = "Saving…";
  const r = await fetch("api/config", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { setLocked(true); result.textContent = ""; return; }
  if (r.status === 422) {
    showErrors(data.detail);
    result.className = "error";
    result.textContent = "Not saved: fix the highlighted fields.";
    return;
  }
  if (!r.ok) { result.className = "error"; result.textContent = `Not saved (${r.status}).`; return; }

  state.config = data.config;
  fillForm(state.config);
  renderPassword(state.config);
  const status = await getJSON("api/status");
  renderHeader(status);
  renderAprsIs(status);
  $("#new-password").value = $("#new-password2").value = "";
  const dw = data.direwolf;
  $("#direwolf-result").className = dw?.error ? "error" : "hint";
  $("#direwolf-result").textContent = !dw ? ""
    : dw.error ? `Direwolf: ${dw.error}` : "Direwolf config written and Direwolf restarted.";
  result.className = "saved";
  result.textContent = message;
  refreshPreviews();
}

form.onsubmit = (e) => {
  e.preventDefault();
  const body = readForm();
  const pw = $("#new-password").value;
  if (pw || $("#new-password2").value) {
    if (pw !== $("#new-password2").value) {
      $("#save-result").className = "error";
      $("#save-result").textContent = "The passwords don't match.";
      return;
    }
    body.admin_password = pw;
  }
  save(body, "Saved.").catch((err) => { $("#save-result").textContent = String(err); });
};

$("#remove-password").onclick = () => {
  if (confirm("Remove the settings password? Anyone on this network could then change settings.")) {
    save({ ...readForm(), admin_password: "" }, "Password removed.");
  }
};

// --- load -------------------------------------------------------------------

async function loadInitial() {
  const [status, config, session, devices] = await Promise.all([
    getJSON("api/status"), getJSON("api/config"), getJSON("api/session"), getJSON("api/system/devices"),
  ]);
  renderHeader(status);
  renderAprsIs(status);
  renderMesh(status);
  state.config = config;
  fillForm(config);
  renderDevices(devices);
  renderPassword(config);
  setLocked(session.password_set && !session.authenticated);
  refreshPreviews();
}

loadInitial().catch((e) => console.error("load failed", e));
connectLive((type, data) => {
  if (type === "status") { renderHeader(data); renderAprsIs(data); renderMesh(data); }
}, () => {});
