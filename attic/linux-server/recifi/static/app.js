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

function payload() {
  const raw = $("print-data").value.trim();
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return raw; }  // plain text is valid too
}

// --------------------------------------------------------------- tabs

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

function fmtAge(ts) {
  if (!ts) return "never";
  const secs = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (secs < 60) return secs + "s ago";
  if (secs < 3600) return Math.round(secs / 60) + "m ago";
  return Math.round(secs / 3600) + "h ago";
}

function renderStatus(s) {
  STATUS = s;
  $("pill").className = "pill " + s.state;
  $("pill").textContent = s.state;
  $("health-detail").textContent = s.last_error || s.detail || "—";
  $("health-sub").textContent =
    `${s.config.transport} · ${s.config.protocol} · ${s.config.width_dots} dots · ` +
    `${s.jobs_printed} printed · last ok ${fmtAge(s.last_ok)}`;
  $("btn-connect").textContent = s.connected ? "Reconnect" : "Connect";
  $("btn-disconnect").disabled = !s.connected;

  $("event-log").innerHTML = s.events.map((e) =>
    `<li class="${e.level}"><time>${new Date(e.at * 1000).toLocaleTimeString()}</time>${esc(e.message)}</li>`
  ).join("") || '<li class="muted">nothing yet</li>';

  // Never stomp on edits the user has in flight -- only track the server
  // values while the form is untouched.
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

function esc(str) {
  return String(str).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
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
  $("c-dpi").value = c.dpi;
  $("c-density").value = c.density;
  $("c-feed").value = c.feed_dots;
  $("c-label-type").value = c.label_type;
  $("c-auto").checked = c.auto_connect;
  $("c-health").value = c.health_interval;
}

function updateHints() {
  if (!STATUS) return;
  const t = STATUS.transports.find((x) => x.name === $("c-transport").value);
  $("c-hint").textContent = t ? t.hint : "";
  const p = STATUS.protocols.find((x) => x.name === $("c-protocol").value);
  $("c-protocol-desc").textContent = p ? p.description : "";
}

document.querySelectorAll("#tab-connection input, #tab-connection select").forEach((el) => {
  if (el.id === "scan-transport") return;
  el.addEventListener("input", () => { configPristine = false; updateHints(); });
});

async function refresh() {
  try { renderStatus(await api("/api/status")); }
  catch { $("pill").className = "pill error"; $("pill").textContent = "offline"; }
}

// ---------------------------------------------------------- connection

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
    renderStatus(s);
    show(el, "connected: " + s.detail);
  } catch (e) { show(el, e.message, false); refresh(); }
}

$("btn-disconnect").onclick = async () => {
  try { renderStatus(await api("/api/disconnect", { method: "POST" })); }
  catch (e) { show($("config-result"), e.message, false); }
};

async function saveConfig() {
  const body = {
    transport: $("c-transport").value,
    protocol: $("c-protocol").value,
    address: $("c-address").value,
    width_dots: +$("c-width").value,
    dpi: +$("c-dpi").value,
    density: +$("c-density").value,
    feed_dots: +$("c-feed").value,
    label_type: +$("c-label-type").value,
    auto_connect: $("c-auto").checked,
    health_interval: +$("c-health").value,
  };
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(body) });
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
  status.textContent = "scanning (Bluetooth takes a few seconds)…";
  try {
    const q = $("scan-transport").value ? "?transport=" + $("scan-transport").value : "";
    const { devices } = await api("/api/discover" + q);
    $("device-list").innerHTML = devices.map((d, i) => `
      <tr data-i="${i}" class="${d.likely ? "likely" : ""}">
        <td>${d.likely ? "★" : ""}</td>
        <td>${esc(d.name)}</td><td>${esc(d.transport)}</td>
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
  fillSelect($("print-template"),
    templates.map((t) => ({ value: t.id, label: t.name })), $("print-template").value);
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

function fieldRow(f = { label: "", value: "", emphasis: false }) {
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
  $("t-title-size").value = editing.title_size || 30;
  $("t-text-size").value = editing.text_size || 22;
  $("t-margin").value = editing.margin ?? 10;
  $("t-gap").value = editing.line_gap ?? 4;
  $("t-align").value = editing.align || "left";

  const body = $("t-fields");
  body.innerHTML = "";
  (editing.fields || []).forEach((f) => body.appendChild(fieldRow(f)));

  const qr = editing.qr;
  $("t-qr-on").checked = !!qr;
  $("t-qr-value").value = qr ? qr.value : "";
  $("t-qr-pos").value = qr ? qr.position : "bottom";
  $("t-qr-scale").value = qr ? qr.scale : 4;
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
    title_size: +$("t-title-size").value,
    text_size: +$("t-text-size").value,
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

// --------------------------------------------------------- print/preview

let previewURL = null;

async function doPreview(body) {
  const res = await fetch("/api/preview?scale=1", {
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
  return res.headers.get("X-Label-Height");
}

$("btn-preview").onclick = async () => {
  try {
    const height = await doPreview({ template: $("print-template").value, data: payload() });
    show($("print-result"), `preview ok — ${height} dots tall`);
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
        hex: $("raw-hex").value,
        text: $("raw-text").value,
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

// ----------------------------------------------------------------- boot

refresh();
loadTemplates().then(() => editTemplate(TEMPLATES[0]));
setInterval(refresh, 3000);
