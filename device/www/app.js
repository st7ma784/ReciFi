"use strict";

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body ? { "content-type": "application/json" } : {},
    ...opts,
  });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : text || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function show(el, message, ok = true) {
  el.textContent = typeof message === "string" ? message : JSON.stringify(message, null, 2);
  el.className = "result " + (ok ? "ok" : "err");
}

function esc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function payload() {
  const raw = $("print-data").value.trim();
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return raw; }  // plain text is valid too
}

document.querySelectorAll("#tabs button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab").forEach((t) =>
      t.classList.toggle("active", t.id === "tab-" + btn.dataset.tab));
  };
});

// ------------------------------------------------------------- status

let STATUS = null;
let configPristine = true;

function renderStatus(s) {
  STATUS = s;
  $("pill").className = "pill " + s.state;
  $("pill").textContent = s.state;
  $("health-detail").textContent = s.last_error || s.detail || "—";

  const w = s.wifi || {};
  const net = w.connected ? `${w.ssid} ${w.ip}${w.rssi != null ? ` (${w.rssi} dBm)` : ""}`
            : w.mode === "ap" ? `setup AP ${w.ssid} — ${w.ip}`
            : `wifi: ${w.error || "down"}`;
  $("health-sub").textContent =
    `${net} · ${s.config.transport}/${s.config.protocol} · ${s.config.width_dots} dots · ` +
    `${s.jobs_printed} printed · ${(s.mem_free / 1024).toFixed(0)}K free`;
  $("wifi-state").textContent = net;

  $("btn-connect").textContent = s.connected ? "Reconnect" : "Connect";
  $("btn-disconnect").disabled = !s.connected;

  $("event-log").innerHTML = (s.events || []).map((e) =>
    `<li class="${e.level}"><time>${new Date(e.at * 1000).toLocaleTimeString()}</time>${esc(e.message)}</li>`
  ).join("") || '<li class="muted">nothing yet</li>';

  // Never stomp on edits in flight -- only track server values while untouched.
  fillSelect($("c-transport"), s.transports.map((t) => ({
    value: t.name,
    label: t.name + (t.available ? "" : ` — unavailable (${t.reason})`),
    disabled: !t.available,
  })), configPristine ? s.config.transport : $("c-transport").value);
  fillSelect($("c-protocol"), s.protocols.map((p) => ({ value: p.name, label: p.name })),
    configPristine ? s.config.protocol : $("c-protocol").value);
  fillSelect($("scan-transport"),
    [{ value: "", label: "all transports" }].concat(
      s.transports.filter((t) => t.available).map((t) => ({ value: t.name, label: t.name }))),
    $("scan-transport").value);

  if (configPristine) fillConfigForm(s);
  updateHints();
}

function fillSelect(sel, options, current) {
  const want = current ?? sel.value;
  sel.innerHTML = options.map((o) =>
    `<option value="${esc(o.value)}"${o.disabled ? " disabled" : ""}>${esc(o.label)}</option>`).join("");
  if (options.some((o) => String(o.value) === String(want))) sel.value = want;
}

function fillConfigForm(s) {
  const c = s.config;
  $("c-address").value = c.address;
  $("c-width").value = c.width_dots;
  $("c-density").value = c.density;
  $("c-feed").value = c.feed_dots;
  $("c-maxh").value = c.max_height;
  $("c-label-type").value = c.label_type;
  $("c-lw").value = c.label_width_mm;
  $("c-lh").value = c.label_height_mm;
  $("c-dpi").value = c.dpi;
  $("c-speed").value = c.speed;
  $("c-direction").value = String(c.direction);
  $("c-post").value = c.post_print;
  $("c-gap").value = c.gap_mm == null ? "" : c.gap_mm;
  $("c-auto").checked = c.auto_connect;
  $("c-qr-level").value = c.qr_level;
  $("c-qr-mask").value = c.qr_mask == null ? "" : String(c.qr_mask);
  if (!$("wifi-host").value) $("wifi-host").value = (c.wifi || {}).hostname || "";
}

function updateHints() {
  if (!STATUS) return;
  const t = STATUS.transports.find((x) => x.name === $("c-transport").value);
  $("c-hint").textContent = t ? t.hint : "";
  const p = STATUS.protocols.find((x) => x.name === $("c-protocol").value);
  $("c-protocol-desc").textContent = p ? p.description : "";
}

document.querySelectorAll("#tab-connection input, #tab-connection select").forEach((el) => {
  if (el.id === "scan-transport" || el.id.startsWith("wifi-")) return;
  el.addEventListener("input", () => { configPristine = false; updateHints(); });
});

async function refresh() {
  try { renderStatus(await api("/api/status")); }
  catch { $("pill").className = "pill error"; $("pill").textContent = "offline"; }
}

// ----------------------------------------------------------------- wifi

$("btn-wifi-scan").onclick = async () => {
  const btn = $("btn-wifi-scan");
  btn.disabled = true;
  show($("wifi-result"), "scanning…");
  try {
    const { networks } = await api("/api/wifi/scan");
    fillSelect($("wifi-ssid"),
      [{ value: "", label: "— pick a network —" }].concat(
        networks.map((n) => ({ value: n.ssid, label: `${n.ssid}  (${n.rssi} dBm)` }))), "");
    show($("wifi-result"), `${networks.length} network(s) in range`);
  } catch (e) { show($("wifi-result"), e.message, false); }
  finally { btn.disabled = false; }
};

$("btn-wifi-join").onclick = async () => {
  const ssid = $("wifi-ssid-manual").value.trim() || $("wifi-ssid").value;
  if (!ssid) { show($("wifi-result"), "pick or type an SSID first", false); return; }
  show($("wifi-result"), `joining ${ssid}…`);
  try {
    const r = await api("/api/wifi", {
      method: "POST",
      body: JSON.stringify({ ssid, password: $("wifi-pass").value, hostname: $("wifi-host").value }),
    });
    show($("wifi-result"), `joined ${ssid} — ${r.wifi.ip}\nThe board may now be at a new address.`);
    $("wifi-pass").value = "";
  } catch (e) { show($("wifi-result"), e.message, false); }
  refresh();
};

// ----------------------------------------------------------- connection

$("btn-connect").onclick = () => doConnect();
$("btn-connect-2").onclick = async () => { await saveConfig(); doConnect(); };

async function doConnect() {
  const el = $("config-result");
  show(el, "connecting…");
  try {
    const s = await api("/api/connect", {
      method: "POST",
      body: JSON.stringify({
        transport: $("c-transport").value || STATUS.config.transport,
        address: $("c-address").value,
        protocol: $("c-protocol").value || null,
      }),
    });
    configPristine = true;
    renderStatus(Object.assign({}, STATUS, s));
    show(el, "connected: " + s.detail);
  } catch (e) { show(el, e.message, false); }
  refresh();
}

$("btn-disconnect").onclick = async () => {
  try { await api("/api/disconnect", { method: "POST" }); refresh(); }
  catch (e) { show($("config-result"), e.message, false); }
};

// Round to a whole byte: the raster is packed 8 dots per byte.
function dotsForMm(mm, dpi) {
  return Math.ceil((mm * dpi) / 25.4 / 8) * 8;
}

$("btn-derive").onclick = () => {
  $("c-width").value = dotsForMm(+$("c-lw").value, +$("c-dpi").value);
  configPristine = false;
  show($("config-result"), `render width set to ${$("c-width").value} dots`);
};

$("btn-gapdetect").onclick = async () => {
  show($("config-result"), "calibrating — the printer will feed a couple of labels…");
  try {
    await api("/api/debug/calibrate-media", { method: "POST", body: "{}" });
    show($("config-result"), "media calibrated; print the test pattern again to check");
  } catch (e) { show($("config-result"), e.message, false); }
};

async function saveConfig() {
  const mask = $("c-qr-mask").value;
  const gap = $("c-gap").value;
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify({
        transport: $("c-transport").value,
        protocol: $("c-protocol").value,
        address: $("c-address").value,
        width_dots: +$("c-width").value,
        density: +$("c-density").value,
        feed_dots: +$("c-feed").value,
        max_height: +$("c-maxh").value,
        label_type: +$("c-label-type").value,
        label_width_mm: +$("c-lw").value,
        label_height_mm: +$("c-lh").value,
        dpi: +$("c-dpi").value,
        speed: +$("c-speed").value,
        direction: +$("c-direction").value,
        post_print: $("c-post").value,
        gap_mm: gap === "" ? null : +gap,
        auto_connect: $("c-auto").checked,
        qr_level: $("c-qr-level").value,
        qr_mask: mask === "" ? null : +mask,
      }),
    });
    configPristine = true;
    show($("config-result"), "settings saved");
    await refresh();
  } catch (e) { show($("config-result"), e.message, false); }
}
$("btn-save-config").onclick = saveConfig;

$("btn-scan").onclick = async () => {
  const status = $("scan-status");
  const btn = $("btn-scan");
  btn.disabled = true;
  status.textContent = "scanning (BLE takes about 5 seconds)…";
  try {
    const q = $("scan-transport").value ? "?transport=" + $("scan-transport").value : "";
    const { devices } = await api("/api/discover" + q);
    $("device-list").innerHTML = devices.map((d, i) => `
      <tr data-i="${i}" class="${d.likely ? "likely" : ""}">
        <td>${d.likely ? "★" : ""}</td><td>${esc(d.name)}</td><td>${esc(d.transport)}</td>
        <td><code>${esc(d.address)}</code></td><td class="muted">${esc(d.detail)}</td>
      </tr>`).join("") || '<tr><td colspan="5" class="muted">nothing found</td></tr>';
    $("device-list").querySelectorAll("tr[data-i]").forEach((tr) => {
      tr.onclick = () => {
        const d = devices[+tr.dataset.i];
        $("c-transport").value = d.transport;
        $("c-address").value = d.address;
        configPristine = false;
        updateHints();
        show($("config-result"), `selected ${d.name} — press "Save & connect"`);
      };
    });
    status.textContent = `${devices.length} device(s); ★ = looks like a printer`;
  } catch (e) { status.textContent = e.message; }
  finally { btn.disabled = false; }
};

// ----------------------------------------------------------- templates

let TEMPLATES = [];
let editing = null;

async function loadTemplates(selectId) {
  const { templates } = await api("/api/templates");
  TEMPLATES = templates;
  fillSelect($("print-template"), templates.map((t) => ({ value: t.id, label: t.name })),
    $("print-template").value);
  describeTemplate();
  $("template-list").innerHTML = templates.map((t) => `
    <li data-id="${esc(t.id)}" class="${editing && editing.id === t.id ? "active" : ""}">
      ${esc(t.name)}${t.builtin ? '<span class="tag">built-in</span>' : ""}
      <small>${esc(t.description || t.id)}</small>
    </li>`).join("");
  $("template-list").querySelectorAll("li").forEach((li) => {
    li.onclick = () => editTemplate(TEMPLATES.find((t) => t.id === li.dataset.id));
  });
  if (selectId) {
    const t = TEMPLATES.find((x) => x.id === selectId);
    if (t) editTemplate(t);
  }
}

$("print-template").onchange = describeTemplate;
function describeTemplate() {
  const t = TEMPLATES.find((x) => x.id === $("print-template").value);
  $("print-template-desc").textContent = t ? t.description : "";
}

function fieldRow(f = {}) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="text" class="f-label" placeholder="label"></td>
    <td><input type="text" class="f-value" placeholder="{{key}}"></td>
    <td><label class="check" title="bigger, bold"><input type="checkbox" class="f-em"></label></td>
    <td><button class="small-btn danger">×</button></td>`;
  tr.querySelector(".f-label").value = f.label || "";
  tr.querySelector(".f-value").value = f.value || "";
  tr.querySelector(".f-em").checked = !!f.emphasis;
  tr.querySelector("button").onclick = () => tr.remove();
  return tr;
}

function editTemplate(t) {
  editing = t || { id: "", name: "", fields: [] };
  $("editor-title").textContent = t ? `Edit “${t.name}”` : "New template";
  $("t-id").value = editing.id || "";
  $("t-name").value = editing.name || "";
  $("t-desc").value = editing.description || "";
  $("t-title").value = editing.title || "";
  $("t-footer").value = editing.footer || "";
  $("t-auto").checked = !!editing.auto_fields;
  $("t-title-scale").value = editing.title_scale || 3;
  $("t-text-scale").value = editing.text_scale || 2;
  $("t-margin").value = editing.margin ?? 8;
  $("t-gap").value = editing.line_gap ?? 4;
  $("t-align").value = editing.align || "left";

  const body = $("t-fields");
  body.innerHTML = "";
  (editing.fields || []).forEach((f) => body.appendChild(fieldRow(f)));

  const qr = editing.qr;
  $("t-qr-on").checked = !!qr;
  $("t-qr-value").value = qr ? qr.value : "";
  $("t-qr-pos").value = qr ? qr.position : "bottom";
  $("t-qr-scale").value = qr ? qr.scale : 3;
  $("t-qr-caption").value = qr ? qr.caption : "";

  $("btn-delete-template").disabled = !t || t.builtin;
  document.querySelectorAll("#template-list li").forEach((li) =>
    li.classList.toggle("active", t && li.dataset.id === t.id));
  $("template-result").textContent = t && t.builtin
    ? "Built-in. Saving keeps the id and shadows it with your copy; deleting that copy restores the original."
    : "";
}

function collectTemplate() {
  const fields = [...$("t-fields").children].map((tr) => ({
    label: tr.querySelector(".f-label").value,
    value: tr.querySelector(".f-value").value,
    emphasis: tr.querySelector(".f-em").checked,
  })).filter((f) => f.label || f.value);
  return {
    id: $("t-id").value.trim(),
    name: $("t-name").value.trim() || "Untitled",
    description: $("t-desc").value,
    title: $("t-title").value,
    fields,
    footer: $("t-footer").value,
    auto_fields: $("t-auto").checked,
    qr: $("t-qr-on").checked ? {
      value: $("t-qr-value").value,
      position: $("t-qr-pos").value,
      scale: +$("t-qr-scale").value,
      caption: $("t-qr-caption").value,
    } : null,
    title_scale: +$("t-title-scale").value,
    text_scale: +$("t-text-scale").value,
    margin: +$("t-margin").value,
    line_gap: +$("t-gap").value,
    align: $("t-align").value,
  };
}

$("btn-add-field").onclick = () => $("t-fields").appendChild(fieldRow());
$("btn-new-template").onclick = () => editTemplate(null);

$("btn-save-template").onclick = async () => {
  const tpl = collectTemplate();
  try {
    const saved = tpl.id
      ? await api("/api/templates/" + encodeURIComponent(tpl.id), { method: "PUT", body: JSON.stringify(tpl) })
      : await api("/api/templates", { method: "POST", body: JSON.stringify(tpl) });
    await loadTemplates(saved.id);
    show($("template-result"), "saved as " + saved.id);
  } catch (e) { show($("template-result"), e.message, false); }
};

$("btn-delete-template").onclick = async () => {
  if (!editing || !editing.id || !confirm(`Delete template "${editing.name}"?`)) return;
  try {
    await api("/api/templates/" + encodeURIComponent(editing.id), { method: "DELETE" });
    editing = null;
    await loadTemplates();
    editTemplate(null);
    show($("template-result"), "deleted");
  } catch (e) { show($("template-result"), e.message, false); }
};

$("btn-try-template").onclick = async () => {
  try {
    await doPreview({ inline: collectTemplate(), data: payload() });
    document.querySelector('#tabs button[data-tab="print"]').click();
  } catch (e) { show($("template-result"), e.message, false); }
};

// ------------------------------------------------------- print/preview

let previewURL = null;

async function doPreview(body) {
  const res = await fetch("/api/preview", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail);
  }
  const blob = await res.blob();
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = URL.createObjectURL(blob);
  $("preview-wrap").innerHTML = `<img src="${previewURL}" alt="label preview">`;
}

$("btn-preview").onclick = async () => {
  show($("print-result"), "rendering…");
  try {
    await doPreview({ template: $("print-template").value, data: payload() });
    show($("print-result"), "preview ok");
  } catch (e) { show($("print-result"), e.message, false); }
};

$("btn-print").onclick = async () => {
  const btn = $("btn-print");
  btn.disabled = true;
  show($("print-result"), "printing…");
  try {
    const r = await api("/api/print", {
      method: "POST",
      body: JSON.stringify({
        template: $("print-template").value,
        data: payload(),
        copies: +$("print-copies").value,
      }),
    });
    show($("print-result"), `printed ${r.copies} label(s) — ${r.bytes} bytes, ${r.height} dots tall`);
    doPreview({ template: $("print-template").value, data: payload() }).catch(() => {});
  } catch (e) { show($("print-result"), e.message, false); }
  finally { btn.disabled = false; refresh(); }
};

// -------------------------------------------------------------- console

$("btn-raw").onclick = async () => {
  try {
    const r = await api("/api/debug/raw", {
      method: "POST",
      body: JSON.stringify({
        hex: $("raw-hex").value, text: $("raw-text").value,
        read_reply: $("raw-reply").checked,
      }),
    });
    show($("raw-result"), `sent ${r.sent} bytes` +
      (r.reply_hex ? `\n\nreply hex:\n${r.reply_hex}\n\nreply ascii:\n${r.reply_ascii}` : "\n(no reply read)"));
  } catch (e) { show($("raw-result"), e.message, false); }
};

$("btn-encode").onclick = async () => {
  try {
    const r = await api("/api/debug/encode", {
      method: "POST",
      body: JSON.stringify({ template: $("print-template").value, data: payload() }),
    });
    show($("raw-result"),
      `protocol ${r.protocol}: ${r.blocks} blocks, ${r.bytes} bytes for a ${r.width}×${r.height} label\n\n${r.head_hex}`);
  } catch (e) { show($("raw-result"), e.message, false); }
};

refresh();
loadTemplates().then(() => editTemplate(TEMPLATES[0]));
setInterval(refresh, 4000);
