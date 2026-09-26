"use strict";
/* sisdefman GUI. Plain JavaScript, no build step. The server (gui.py) owns
   the project file; every change is a POST that returns the result or, in
   release mode, a 409 listing the live items it would change. */

const TOKEN = document.querySelector('meta[name="sisdefman-token"]').content;
const CONFIRM_PHRASE = "CHANGE PLAYER INVENTORIES";

let S = null; // server state (GET /api/state)
const ui = {
  page: "items",          // items | kinds | tables | series | settings | check
  scope: { type: "all" }, // all | series(key) | other | kind(name)
  search: "",
  selected: null,         // itemdefid being edited, or "new"
  checked: new Set(),
  draft: null,
  kindSel: null, kindDraft: null,
  tableSel: null, tableDraft: null,
  seriesSel: null, seriesDraft: null,
  diff: null,
};
const refs = {};

/* ------------------------------------------------------------ helpers */

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
const byId = (id) => S.items.find((e) => e.id === id);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
function debounce(fn, ms) {
  let t = null;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
function show(v) {
  if (v === undefined || v === null) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}
function toast(text, error) {
  const el = h("div", { class: "toast" + (error ? " error" : "") }, text);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), error ? 7000 : 3500);
}

/* ------------------------------------------------------------ API */

async function request(path, body) {
  const res = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Sisdefman-Token": TOKEN },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (res.status === 409 && data.needs_confirmation) return { conflict: data };
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return { result: data.result };
}

async function refresh() {
  const { result } = await request("state");
  S = result;
  if (!S.open) { render(); return; }
  // An item open in the editor without unsaved edits follows the new state.
  const d = ui.draft;
  if (d && d.mode === "edit" && !d.dirty) openDraft(d.id);
  render();
  if (ui.draft) requestPreview();
}

/* Send a change; in release mode show the warning if the server asks. */
async function mutate(path, body, success) {
  try {
    let res = await request(path, body);
    if (res.conflict) {
      if (!(await confirmInventory(res.conflict))) return null;
      res = await request(path, { ...body, confirm: true });
    }
    await refresh();
    if (success) toast(typeof success === "function" ? success(res.result) : success);
    return res.result === undefined ? {} : res.result;
  } catch (e) {
    toast(e.message, true);
    return null;
  }
}

/* ------------------------------------------------------------ dialogs */

function modal({ title, body, actions, danger, wide }) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    const close = (v) => { overlay.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    const buttons = (actions || [{ label: "Close", value: null }]).map((a) =>
      h("button", {
        class: "btn " + (a.class || ""), disabled: a.disabled,
        onclick: async () => {
          if (a.run) { const v = await a.run(); if (v === undefined) return; close(v); } else close(a.value);
        },
      }, a.label));
    (actions || []).forEach((a, n) => { if (a.bind) a.bind(buttons[n]); });
    const overlay = h("div", { class: "overlay", onmousedown: (e) => { if (e.target === overlay) close(null); } },
      h("div", { class: "dialog" + (danger ? " danger" : "") + (wide ? " wide" : ""), role: "dialog" },
        h("div", { class: "d-title" }, title),
        h("div", { class: "d-body" }, body),
        h("div", { class: "d-actions" }, buttons)));
    root.append(overlay);
    document.addEventListener("keydown", onKey);
    const focus = overlay.querySelector("input, textarea, select");
    if (focus) focus.focus();
  });
}

function confirmModal(title, text, label, danger) {
  return modal({
    title, danger, body: h("p", {}, text),
    actions: [{ label: "Cancel", value: false }, { label, value: true, class: danger ? "danger solid" : "primary" }],
  });
}

/* The release-mode warning: lists affected items and wants the phrase typed. */
function confirmInventory(conflict) {
  const input = h("input", { type: "text", placeholder: CONFIRM_PHRASE, autocomplete: "off", spellcheck: "false" });
  let go = null;
  input.addEventListener("input", () => { go.disabled = input.value.trim() !== CONFIRM_PHRASE; });
  const impacts = conflict.impacts || [];
  const body = h("div", { class: "stack" },
    impacts.length ? [
      h("p", {}, h("b", {}, conflict.action), ` changes what ${plural(impacts.length, "live item definition")} `
        + "are. Steam inventories store itemdefids, so once this is uploaded every player who owns one of these "
        + "IDs will have a different item:"),
      h("div", { class: "impacts" }, impacts.map((i) => h("div", {}, i.text))),
      h("p", {}, h("b", {}, "This cannot be undone for players once it is uploaded to Steam."),
        impacts.some((i) => i.kind === "reassigned") ? " If an entry is only a rename of the same item, that one is harmless." : ""),
    ] : h("p", {}, conflict.message || conflict.action),
    h("div", { class: "field" }, h("div", { class: "label" }, h("span", {}, "Type ", h("b", {}, CONFIRM_PHRASE), " to continue")), input));
  return modal({
    title: impacts.length ? "Release mode: this changes items players already own" : conflict.action,
    danger: true, body,
    actions: [
      { label: "Cancel", value: false },
      { label: "Go ahead", value: true, class: "danger solid", disabled: true, bind: (b) => { go = b; } },
    ],
  }).then((v) => !!v);
}

function promptModal(title, fields, okLabel) {
  // fields: [{name, label, value, type, options, hint}]
  const inputs = {};
  const body = h("div", {}, fields.map((f) => {
    let input;
    if (f.type === "select") {
      input = h("select", {}, f.options.map(([v, l]) => h("option", { value: v }, l)));
      input.value = f.value ?? "";
    } else if (f.type === "checkbox") {
      input = h("input", { type: "checkbox", checked: f.value });
      inputs[f.name] = input;
      return h("div", { class: "field" }, h("label", { class: "check" }, input, f.label),
        f.hint ? h("div", { class: "hint" }, f.hint) : null);
    } else if (f.type === "textarea") {
      input = h("textarea", { rows: 4 }); input.value = f.value ?? "";
    } else {
      input = h("input", { type: f.type || "text", list: f.list });
      input.value = f.value ?? "";
    }
    inputs[f.name] = input;
    return h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, f.label)), input,
      f.hint ? h("div", { class: "hint" }, f.hint) : null);
  }));
  return modal({
    title, body,
    actions: [{ label: "Cancel", value: null }, {
      label: okLabel || "OK", class: "primary",
      run: () => Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.type === "checkbox" ? el.checked : el.value])),
    }],
  });
}

/* ------------------------------------------------------------ shell */

function render() {
  const app = document.getElementById("app");
  if (!S.open) { app.replaceChildren(renderLauncher()); return; }
  app.replaceChildren(h("div", { class: "shell" }, renderTopbar(), h("div", { class: "body" }, renderSidebar(), renderPage())));
}

function renderTopbar() {
  const errors = S.issues.filter((i) => i.level === "error").length;
  const warnings = S.issues.filter((i) => i.level === "warning").length;
  const lastUndo = S.undo[S.undo.length - 1];
  return h("header", { class: "topbar" },
    h("button", { class: "btn icon menu-toggle", title: "Menu", onclick: () => { ui.menuOpen = !ui.menuOpen; render(); } }, "☰"),
    h("span", { class: "brand" }, "sisdefman"),
    h("button", { class: "file-switch", title: S.path + "\nClick to open another project", onclick: switchProject },
      h("span", { class: "file" }, S.file + (S.appid ? ` · app ${S.appid}` : "")), h("span", { class: "hint" }, " ⇄")),
    h("button", {
      class: "badge " + S.mode, title: "Change the mode in Settings",
      onclick: () => go("settings"),
    }, S.mode === "release" ? "RELEASE MODE" : "prerelease"),
    h("span", { class: "spacer" }),
    errors || warnings ? h("button", { class: "badge " + (errors ? "danger" : "warn"), onclick: () => go("check") },
      errors ? plural(errors, "error") : plural(warnings, "warning")) : h("span", { class: "badge ok" }, "no problems"),
    h("button", {
      class: "btn", disabled: !lastUndo, title: lastUndo ? "Undo: " + lastUndo : "Nothing to undo",
      onclick: () => mutate("undo", {}, (r) => "Undone: " + r.undone),
    }, "Undo"),
    h("button", { class: "btn primary", onclick: exportDialog }, "Export"));
}

function go(page) {
  if (!leaveDraft()) return;
  ui.page = page;
  ui.menuOpen = false;
  render();
}

function leaveDraft() {
  if (ui.draft && ui.draft.dirty && !window.confirm("Discard your unsaved changes to this item?")) return false;
  ui.draft = null;
  ui.selected = null;
  return true;
}

function renderSidebar() {
  const counts = { all: 0, other: 0 };
  const kindCounts = {};
  for (const e of S.items) {
    if (e.dummy) continue;
    counts.all++;
    if (!e.series) counts.other++;
    if (e.record.kind) kindCounts[e.record.kind] = (kindCounts[e.record.kind] || 0) + 1;
  }
  const nav = (label, count, active, onclick) =>
    h("button", { class: "nav" + (active ? " active" : ""), onclick }, h("span", {}, label),
      count === null ? null : h("span", { class: "count" }, count));
  const scopeIs = (t, v) => ui.page === "items" && ui.scope.type === t && (v === undefined || ui.scope.value === v);
  const setScope = (type, value) => () => {
    if (!leaveDraft()) return;
    ui.page = "items"; ui.scope = { type, value }; ui.menuOpen = false; ui.checked.clear(); render();
  };
  const issues = S.issues.filter((i) => i.level !== "note").length;
  return h("nav", { class: "sidebar" + (ui.menuOpen ? " open" : "") },
    h("h4", {}, "Items"),
    nav("All items", counts.all, scopeIs("all"), setScope("all")),
    Object.entries(S.series).map(([key, s]) =>
      nav(s.display_name, s.members.length, scopeIs("series", key), setScope("series", key))),
    nav("Other definitions", counts.other, scopeIs("other"), setScope("other")),
    Object.keys(S.kinds).length ? h("h4", {}, "By kind") : null,
    Object.keys(S.kinds).map((k) => nav(k, kindCounts[k] || 0, scopeIs("kind", k), setScope("kind", k))),
    h("h4", {}, "Schema"),
    nav("Item kinds", Object.keys(S.kinds).length, ui.page === "kinds", () => go("kinds")),
    nav("Lookup tables", Object.keys(S.tables).length, ui.page === "tables", () => go("tables")),
    h("h4", {}, "Project"),
    nav("Series setup", Object.keys(S.series).length, ui.page === "series", () => go("series")),
    nav("Settings & export", null, ui.page === "settings", () => go("settings")),
    nav("Check", issues, ui.page === "check", () => go("check")));
}

function renderPage() {
  const pages = { items: renderItemsPage, kinds: renderKindsPage, tables: renderTablesPage,
    series: renderSeriesPage, settings: renderSettingsPage, check: renderCheckPage };
  return (pages[ui.page] || renderItemsPage)();
}

/* ------------------------------------------------------------ project chooser */

const launch = { listing: null, loading: false, checked: new Set(), filename: "sisdefman.json", appid: "", error: null };

function resetUi() {
  Object.assign(ui, {
    page: "items", scope: { type: "all" }, search: "", selected: null, draft: null, menuOpen: false,
    kindSel: null, kindDraft: null, tableSel: null, tableDraft: null, seriesSel: null, seriesDraft: null,
  });
  ui.checked.clear();
  launch.listing = null;
  launch.checked.clear();
  launch.filename = "sisdefman.json";
}

async function switchProject() {
  if (!confirmDiscard()) return;
  try {
    await request("launcher/close", {});
    resetUi();
    await refresh();
  } catch (e) { toast(e.message, true); }
}

async function openProject(path) {
  try {
    await request("launcher/open", { path });
    resetUi();
    await refresh();
    toast(`Opened ${S.file}.`);
  } catch (e) { toast(e.message, true); }
}

async function browseTo(path) {
  launch.loading = true;
  try {
    launch.listing = (await request("launcher/browse", { path })).result;
    launch.checked.clear();
    launch.error = null;
  } catch (e) {
    if (launch.listing) toast(e.message, true); else launch.error = e.message;
  }
  launch.loading = false;
  render();
}

async function quitApp() {
  if (S.open && !confirmDiscard()) return;
  if (!(await confirmModal("Quit sisdefman", "Stop the sisdefman server? This page stops working until you start it again.", "Quit"))) return;
  try { await request("app/quit", {}); } catch (e) { /* it may already be gone */ }
  ui.draft = null;
  document.getElementById("app").replaceChildren(h("div", { class: "placeholder" },
    h("h3", {}, "sisdefman has stopped"),
    h("p", {}, "You can close this tab. Start it again with ", h("code", {}, "sisdefman gui"), ".")));
}

function renderLauncher() {
  if (!launch.listing && !launch.loading && !launch.error) setTimeout(() => browseTo(null), 0);
  const L = launch.listing;

  const recent = S.recent.length
    ? h("div", { class: "recent-list" }, S.recent.map((r) => h("div", { class: "recent" + (r.exists ? "" : " missing") },
      h("button", { class: "recent-open", disabled: !r.exists, title: r.path, onclick: () => openProject(r.path) },
        h("b", {}, r.name), h("span", { class: "hint mono" }, r.folder),
        h("span", { class: "hint" }, r.exists ? `opened ${r.opened_at}` : "file not found")),
      h("button", { class: "btn icon", title: "Remove from the list", onclick: async () => {
        try { S.recent = (await request("launcher/forget", { path: r.path })).result.recent; render(); } catch (e) { toast(e.message, true); }
      } }, "✕"))))
    : h("p", { class: "hint" }, "Projects you open appear here.");

  const pathInput = h("input", { type: "text", class: "mono", value: L ? L.path : "",
    onkeydown: (ev) => { if (ev.key === "Enter") browseTo(pathInput.value); } });
  const entries = !L ? h("div", { class: "empty" }, launch.error || "Loading…") : h("div", { class: "files" },
    L.entries.length ? L.entries.map((e) => {
      if (e.type === "dir") {
        return h("button", { class: "file-row", onclick: () => browseTo(e.path) },
          h("span", { class: "ftype dir" }, "folder"), h("span", { class: "fname" }, e.name));
      }
      if (e.type === "project") {
        return h("div", { class: "file-row project" },
          h("span", { class: "ftype project" }, "project"), h("span", { class: "fname" }, e.name),
          h("button", { class: "btn small primary", onclick: () => openProject(e.path) }, "Open"));
      }
      const cb = h("input", { type: "checkbox", checked: launch.checked.has(e.path), onchange: (ev) => {
        ev.target.checked ? launch.checked.add(e.path) : launch.checked.delete(e.path); render();
      } });
      return h("label", { class: "file-row" }, h("span", { class: "ftype" }, "json"), h("span", { class: "fname" }, e.name),
        h("span", { class: "hint" }, e.size === null ? "" : `${Math.max(1, Math.round(e.size / 1024))} KB`), cb);
    }) : h("div", { class: "empty" }, "No folders or JSON files here."));

  const clash = h("div", { class: "hint", style: "color:var(--danger)" });
  const createBtn = h("button", { class: "btn primary" });
  const checkName = () => {
    let name = launch.filename.trim() || "sisdefman.json";
    if (!name.toLowerCase().endsWith(".json")) name += ".json";
    const exists = L && L.entries.some((e) => e.name.toLowerCase() === name.toLowerCase());
    clash.textContent = exists ? `${name} already exists in this folder.` : "";
    createBtn.disabled = !!exists;
  };
  const nameInput = h("input", { type: "text", class: "mono", value: launch.filename, oninput: (ev) => { launch.filename = ev.target.value; checkName(); } });
  const appidInput = h("input", { type: "number", min: 1, value: launch.appid, placeholder: "optional", oninput: (ev) => { launch.appid = ev.target.value; } });
  const ticked = [...launch.checked];
  const create = async () => {
    try {
      const { result } = await request("launcher/create", {
        folder: L.path, filename: launch.filename, files: ticked, appid: launch.appid ? Number(launch.appid) : null,
      });
      resetUi();
      await refresh();
      modal({ title: `Created ${S.file}`, body: h("pre", { class: "json" }, result.report.join("\n")) });
    } catch (e) { toast(e.message, true); }
  };
  createBtn.addEventListener("click", create);
  createBtn.textContent = ticked.length ? `Create project from ${plural(ticked.length, "file")}` : "Create empty project";
  checkName();

  return h("div", { class: "launcher" },
    h("header", { class: "topbar" }, h("span", { class: "brand" }, "sisdefman"), h("span", { class: "hint" }, "v" + S.version),
      h("span", { class: "spacer" }), h("button", { class: "btn", onclick: quitApp }, "Quit")),
    h("div", { class: "launcher-body" },
      h("h2", {}, "Choose a project"),
      h("p", { class: "lead" }, "A project is the one file that holds all of a game's item definitions. Open one, or create one from your Steam item definition files."),
      h("div", { class: "card" }, h("h3", {}, "Recent projects"), recent),
      h("div", { class: "card" },
        h("h3", {}, "Browse"),
        h("div", { class: "row", style: "margin-bottom:8px" },
          h("button", { class: "btn", disabled: !L || !L.parent, title: "Up one folder", onclick: () => browseTo(L.parent) }, "↑ Up"),
          h("button", { class: "btn", disabled: !L, title: "Home folder", onclick: () => browseTo(L.home) }, "Home"),
          L && L.drives.length ? (() => {
            const sel = h("select", { style: "width:auto", onchange: (ev) => browseTo(ev.target.value) },
              h("option", { value: "" }, "Drive…"), L.drives.map((d) => h("option", { value: d }, d)));
            return sel;
          })() : null,
          h("div", { class: "grow" }, pathInput),
          h("button", { class: "btn", onclick: () => browseTo(pathInput.value) }, "Go")),
        entries,
        L ? h("div", { class: "new-project" },
          h("h3", {}, "New project in this folder"),
          h("div", { class: "row" },
            h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "File name")), nameInput),
            ticked.length ? null : h("div", { class: "field", style: "width:160px" }, h("div", { class: "label" }, h("b", {}, "Steam app ID")), appidInput)),
          h("p", { class: "hint" }, ticked.length
            ? `The ${plural(ticked.length, "ticked file")} will be imported, as with sisdefman import.`
            : "Tick Steam item definition files above to import them, or create an empty project."),
          clash, createBtn) : null)));
}

/* ------------------------------------------------------------ items page */

function scopedItems() {
  const sc = ui.scope;
  let list = S.items.filter((e) => {
    if (sc.type === "series") return e.series === sc.value;
    if (e.dummy) return false;
    if (sc.type === "other") return !e.series;
    if (sc.type === "kind") return e.record.kind === sc.value;
    return true;
  });
  const q = ui.search.trim().toLowerCase();
  if (q) {
    list = list.filter((e) => {
      const hay = [e.id, e.item.name, e.item.type, e.item.tags, e.record && e.record.kind,
        ...Object.values(e.record || {}).map(show)].join("\n").toLowerCase();
      return q.split(/\s+/).every((w) => hay.includes(w));
    });
  }
  return list;
}

function scopeTitle() {
  const sc = ui.scope;
  if (sc.type === "series") return S.series[sc.value] ? `${S.series[sc.value].display_name} (${sc.value})` : sc.value;
  if (sc.type === "other") return "Other definitions";
  if (sc.type === "kind") return `Kind: ${sc.value}`;
  return "All items";
}

function renderItemsPage() {
  if (ui.scope.type === "series" && !S.series[ui.scope.value]) ui.scope = { type: "all" };
  const list = scopedItems();
  const sc = ui.scope;
  const series = sc.type === "series" ? S.series[sc.value] : null;
  const search = h("input", {
    class: "search", type: "text", placeholder: "Search name, ID, tags, fields…", value: ui.search,
    oninput: debounce((e) => { ui.search = e.target.value; renderGridOnly(); }, 150),
  });
  const addButton = series
    ? h("button", { class: "btn primary", onclick: () => startCreate(sc.value, null) }, "+ Add item")
    : h("button", { class: "btn", onclick: () => startCreate(null, null) }, "+ New definition");
  const toolbar = h("div", { class: "toolbar" },
    h("b", {}, scopeTitle()),
    series ? h("span", { class: "hint" }, `IDs ${series.first_id}–${series.last_id} · next free ${series.next_id}`) : null,
    h("span", { style: "flex:1" }), search, addButton);
  refs.gridwrap = h("div", { class: "gridwrap" });
  refs.bulk = h("div");
  renderGridOnly(list);
  return h("div", { class: "main" },
    h("div", { class: "content" }, toolbar, refs.bulk, refs.gridwrap),
    renderEditorPanel());
}

function renderGridOnly(list) {
  list = list || scopedItems();
  const inSeries = ui.scope.type === "series";
  const all = h("input", {
    type: "checkbox", title: "Select all shown",
    checked: list.length && list.filter((e) => !e.dummy).every((e) => ui.checked.has(e.id)),
    onclick: (ev) => {
      for (const e of list) if (!e.dummy) ev.target.checked ? ui.checked.add(e.id) : ui.checked.delete(e.id);
      renderGridOnly(); renderBulk();
    },
  });
  const rows = list.map((e) => {
    const flags = [];
    if (e.live !== null && e.live !== undefined) flags.push(h("span", { class: "badge ok", title: "Live on Steam as: " + e.live }, "live"));
    if (e.overrides.length) flags.push(h("span", { class: "badge warn", title: "Overrides: " + e.overrides.join(", ") }, plural(e.overrides.length, "override")));
    if (e.problems.length) flags.push(h("span", { class: "badge danger", title: e.problems.join("\n") }, "⚠ " + e.problems.length));
    const cb = e.dummy ? "" : h("input", {
      type: "checkbox", checked: ui.checked.has(e.id),
      onclick: (ev) => { ev.stopPropagation(); ev.target.checked ? ui.checked.add(e.id) : ui.checked.delete(e.id); renderBulk(); },
    });
    return h("tr", {
      class: (e.dummy ? "dummy " : "") + (ui.selected === e.id ? "selected" : ""),
      onclick: () => (e.dummy ? null : selectItem(e.id)),
    },
    h("td", { class: "cb" }, cb),
    inSeries ? h("td", { class: "num" }, e.index ?? "") : null,
    h("td", { class: "id" }, e.id),
    h("td", {}, e.dummy ? "unused slot (exported as a dummy item)" : e.item.name || h("span", { class: "hint" }, "(no name)")),
    h("td", {}, e.record && e.record.kind ? h("span", { class: "badge accent" }, e.record.kind) : ""),
    h("td", { class: "hint" }, e.item.type || ""),
    h("td", { class: "flags" }, flags));
  });
  refs.gridwrap.replaceChildren(list.length ? h("table", { class: "grid" },
    h("thead", {}, h("tr", {}, h("th", { class: "cb" }, all), inSeries ? h("th", {}, "#") : null,
      h("th", {}, "ID"), h("th", {}, "Name"), h("th", {}, "Kind"), h("th", {}, "Type"), h("th", {}, ""))),
    h("tbody", {}, rows)) : h("div", { class: "empty" }, ui.search ? "Nothing matches the search." : "No items here yet."));
  renderBulk();
}

function renderBulk() {
  if (!refs.bulk) return;
  const ids = [...ui.checked].filter((id) => byId(id));
  if (!ids.length) { refs.bulk.replaceChildren(); return; }
  const raw = ids.filter((id) => !byId(id).record.kind);
  const kinded = ids.length - raw.length;
  refs.bulk.replaceChildren(h("div", { class: "bulkbar" },
    h("b", {}, `${ids.length} selected`),
    h("button", { class: "btn small", onclick: () => bulkSet(ids) }, "Set a field…"),
    Object.keys(S.kinds).length && raw.length ? h("button", { class: "btn small", onclick: () => adoptDialog(raw) }, `Convert ${raw.length} to a kind…`) : null,
    kinded ? h("button", { class: "btn small", onclick: () => detachDialog(ids.filter((id) => byId(id).record.kind)) }, `Detach ${kinded} from kind…`) : null,
    h("span", { style: "flex:1" }),
    h("button", { class: "btn small", onclick: () => { ui.checked.clear(); renderGridOnly(); } }, "Clear selection")));
}

async function bulkSet(ids) {
  const names = new Set(S.steam_fields);
  for (const k of Object.values(S.kinds)) for (const f of Object.keys(k.fields || {})) names.add(f);
  for (const id of ids) for (const f of Object.keys(byId(id).record)) names.add(f);
  const dl = h("datalist", { id: "bulk-fields" }, [...names].sort().map((n) => h("option", { value: n })));
  document.body.append(dl);
  const v = await promptModal(`Set a field on ${plural(ids.length, "item")}`, [
    { name: "field", label: "Field", list: "bulk-fields", hint: "A kind field (e.g. flavor), a Steam field, or a derived field to override it." },
    { name: "value", label: "Value", type: "textarea" },
    { name: "json", label: "The value is JSON (true, false, a number…)", type: "checkbox", value: false },
    { name: "unset", label: "Remove the field instead (derived fields go back to their rule)", type: "checkbox", value: false },
  ], "Apply");
  dl.remove();
  if (!v || !v.field.trim()) return;
  let value = v.value;
  if (v.json && !v.unset) {
    try { value = JSON.parse(v.value); } catch (e) { toast("That is not valid JSON.", true); return; }
  }
  const body = v.unset ? { ids, assignments: [], unset: [v.field.trim()] } : { ids, assignments: [[v.field.trim(), value]] };
  await mutate("items/set", body, `Updated ${plural(ids.length, "item")}.`);
}

async function adoptDialog(ids) {
  const kinds = Object.keys(S.kinds);
  const pick = await promptModal(`Convert ${plural(ids.length, "item")} to a kind`, [
    { name: "kind", label: "Kind", type: "select", value: kinds[0], options: kinds.map((k) => [k, k]),
      hint: "sisdefman works out the kind's fields from each item's current definition and fills in lookup tables. The exported definitions stay exactly the same; values the rules don't reproduce are kept as overrides." },
  ], "Preview");
  if (!pick) return;
  let report;
  try {
    report = (await request("items/adopt", { kind: pick.kind, ids, dry_run: true })).result;
  } catch (e) { toast(e.message, true); return; }
  const skipped = Object.entries(report.skipped);
  const overrides = Object.entries(report.overrides);
  const ok = await modal({
    title: `Convert to ${pick.kind}: preview`, wide: true,
    body: h("div", { class: "stack" },
      h("p", {}, h("b", {}, plural(report.adopted.length, "item")), " can be converted.",
        report.learned.length ? ` ${plural(report.learned.length, "lookup table value")} will be filled in.` : ""),
      overrides.length ? h("div", {}, h("b", {}, "Kept as overrides (the rule gives something else):"),
        h("ul", {}, overrides.map(([id, f]) => h("li", {}, `${id}: ${f.join(", ")}`)))) : null,
      skipped.length ? h("div", {}, h("b", {}, "Not converted:"),
        h("ul", {}, skipped.map(([id, why]) => h("li", {}, `${id}: ${why}`)))) : null,
      report.learned.length ? h("details", {}, h("summary", {}, "Table values"),
        h("pre", { class: "json" }, report.learned.join("\n"))) : null),
    actions: [{ label: "Cancel", value: false }, { label: "Convert", value: true, class: "primary", disabled: !report.adopted.length }],
  });
  if (!ok) return;
  const r = await mutate("items/adopt", { kind: pick.kind, ids }, (res) => `${plural(res.adopted.length, "item")} now use ${pick.kind}.`);
  if (r) { ui.checked.clear(); render(); }
}

async function detachDialog(ids) {
  const ok = await confirmModal("Detach from kind",
    `${plural(ids.length, "item")} will become plain definitions holding their current fields. Their exported definitions do not change.`,
    "Detach");
  if (ok && await mutate("items/detach", { ids }, `Detached ${plural(ids.length, "item")}.`)) { ui.checked.clear(); render(); }
}

async function exportDialog() {
  const v = await promptModal("Export for Steam", [
    { name: "path", label: "File", value: S.export_path, hint: "Every item definition in one file, ready to upload in Steamworks." },
    { name: "mark_live", label: "Also record it as live (use when you are about to upload it)", type: "checkbox", value: false },
  ], "Export");
  if (!v) return;
  await mutate("export", { path: v.path, mark_live: v.mark_live },
    (r) => `Wrote ${r.items} definitions to ${r.path}` + (r.marked_live ? ` and recorded ${r.marked_live} as live.` : "."));
}

/* ------------------------------------------------------------ item editor */

function confirmDiscard() {
  if (ui.draft && ui.draft.dirty && !window.confirm("Discard your unsaved changes to this item?")) return false;
  return true;
}

function openDraft(id) {
  const e = byId(id);
  if (!e || e.dummy) { ui.draft = null; ui.selected = null; return; }
  ui.selected = id;
  ui.draft = { mode: "edit", id, record: clone(e.record), dirty: false, preview: null };
}

function selectItem(id) {
  if (ui.selected === id) return;
  if (!confirmDiscard()) return;
  openDraft(id);
  render();
  requestPreview();
}

function commonKind(seriesKey) {
  const counts = {};
  for (const id of S.series[seriesKey].members) {
    const k = byId(id).record.kind;
    if (k) counts[k] = (counts[k] || 0) + 1;
  }
  if (!Object.keys(counts).length) {
    // An empty series: suggest the kind used most in the project.
    for (const e of S.items) if (e.record && e.record.kind) counts[e.record.kind] = (counts[e.record.kind] || 0) + 1;
  }
  const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  return best ? best[0] : null;
}

function suggestId() {
  const taken = new Set(S.items.map((e) => e.id));
  const inSeries = (id) => Object.values(S.series).some((s) => s.first_id <= id && id <= s.last_id);
  const ids = S.items.filter((e) => !e.series && e.id < 900000).map((e) => e.id);
  let id = (ids.length ? Math.max(...ids) : 0) + 1;
  while (taken.has(id) || inSeries(id)) id++;
  return id;
}

function startCreate(seriesKey, position, base) {
  if (!confirmDiscard()) return;
  let record;
  if (base) {
    record = clone(base);
    delete record.itemdefid;
  } else {
    const kind = seriesKey ? commonKind(seriesKey) : null;
    record = kind ? { kind } : { type: "item", name: "", description: "" };
  }
  ui.selected = "new";
  ui.draft = { mode: "create", series: seriesKey, position, itemdefid: seriesKey ? null : suggestId(),
    record, dirty: true, preview: null };
  render();
  requestPreview();
}

function draftRecordForServer() {
  const d = ui.draft;
  const rec = { itemdefid: d.mode === "edit" ? d.id : (d.itemdefid || 0), ...clone(d.record) };
  rec.itemdefid = d.mode === "edit" ? d.id : (d.itemdefid || 0);
  const kind = rec.kind ? S.kinds[rec.kind] : null;
  if (!kind) return rec;
  // Keep a tidy key order: itemdefid, kind, the kind's fields, then the rest.
  const out = { itemdefid: rec.itemdefid, kind: rec.kind };
  for (const f of Object.keys(kind.fields || {})) if (f in rec) out[f] = rec[f];
  for (const [k, v] of Object.entries(rec)) if (!(k in out)) out[k] = v;
  return out;
}

const requestPreview = debounce(async () => {
  const d = ui.draft;
  if (!d) return;
  try {
    const { result } = await request("preview", { record: draftRecordForServer(), series: d.series, position: d.position });
    if (ui.draft !== d) return;
    d.preview = result;
    updatePreviewDom();
  } catch (e) {
    if (refs.problems) refs.problems.replaceChildren(h("div", { class: "problems" }, e.message));
  }
}, 180);

function changed() {
  ui.draft.dirty = true;
  if (refs.saveBtn) refs.saveBtn.disabled = false;
  requestPreview();
}

function updatePreviewDom() {
  const d = ui.draft;
  const p = d && d.preview;
  if (!p || !refs.json) return;
  refs.title.textContent = p.item.name || "(no name)";
  refs.json.textContent = JSON.stringify(p.item, null, 2);
  const probs = p.problems || [];
  refs.problems.replaceChildren(probs.length ? h("div", { class: "problems" }, h("b", {}, "Problems"),
    h("ul", {}, probs.map((x) => h("li", {}, x)))) : "");
  for (const [field, el] of Object.entries(refs.derivedValues || {})) {
    const overridden = field in d.record;
    el.textContent = overridden ? "Rule gives: " + show(p.rules[field]) : show(p.item[field]);
  }
}

function renderEditorPanel() {
  const d = ui.draft;
  if (!d) {
    return h("aside", { class: "editor empty-editor" }, h("div", { class: "placeholder" },
      h("h3", {}, "Select an item to edit it"),
      h("p", {}, "Items that have a kind are edited through their fields (weapon, finish, flavor text…); everything else is derived from the kind's rules and lookup tables."),
      h("p", {}, "Tick several items to change a field on all of them, or to convert them to a kind.")));
  }
  refs.derivedValues = {};
  const rec = d.record;
  const e = d.mode === "edit" ? byId(d.id) : null;
  const kind = rec.kind ? S.kinds[rec.kind] : null;
  refs.title = h("h3", {}, (d.preview && d.preview.item.name) || (e && e.item.name) || "New item");
  refs.problems = h("div");
  refs.json = h("pre", { class: "json" }, e ? JSON.stringify(e.item, null, 2) : "");
  const meta = [];
  if (e) {
    meta.push(h("span", { class: "badge" }, "ID " + e.id));
    if (e.series) meta.push(h("span", { class: "badge" }, `${e.series} #${e.index}`));
    if (e.live !== null && e.live !== undefined) meta.push(h("span", { class: "badge ok", title: "Live on Steam as " + e.live }, "live"));
  } else {
    meta.push(h("span", { class: "badge accent" }, d.series ? `new item in ${d.series}` : "new definition"));
  }
  if (rec.kind) meta.push(h("span", { class: "badge accent" }, "kind " + rec.kind));

  const body = [refs.problems];
  if (d.mode === "create") body.push(renderPlacement(d));
  if (d.mode === "create" && Object.keys(S.kinds).length) {
    const sel = h("select", {
      onchange: () => {
        d.record = sel.value ? { kind: sel.value } : { type: "item", name: "", description: "" };
        render(); requestPreview();
      },
    }, h("option", { value: "" }, "(none: edit the Steam fields directly)"),
    Object.keys(S.kinds).map((k) => h("option", { value: k }, k)));
    sel.value = rec.kind || "";
    body.push(h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "Kind")), sel));
  }
  if (kind) {
    body.push(renderKindFields(rec, kind), renderDerived(rec, kind));
    const exclude = new Set(["itemdefid", "kind", ...Object.keys(kind.fields || {}), ...Object.keys(kind.derive || {})]);
    body.push(h("div", { class: "section" }, h("h5", {}, "Other fields",
      h("span", { class: "hint", style: "text-transform:none;letter-spacing:0" }, "exported as they are")),
    kvEditor(rec, exclude)));
  } else {
    body.push(h("div", { class: "section" }, h("h5", {}, "Steam fields"), kvEditor(rec, new Set(["itemdefid"]))));
  }
  body.push(h("div", { class: "section" }, h("h5", {}, "Exported definition"), refs.json));

  refs.saveBtn = h("button", { class: "btn primary", disabled: !d.dirty, onclick: saveDraft },
    d.mode === "create" ? "Add item" : "Save");
  const actions = [refs.saveBtn];
  if (d.mode === "edit") {
    actions.push(h("button", { class: "btn", onclick: () => { openDraft(d.id); render(); requestPreview(); } }, "Revert"));
    actions.push(h("span", { style: "flex:1" }));
    actions.push(h("button", { class: "btn", title: "Copy this item into a new one", onclick: () => startCreate(e.series, null, e.record) }, "Duplicate"));
    if (e.series) actions.push(h("button", { class: "btn", onclick: () => moveDialog(e) }, "Move…"));
    if (!rec.kind && Object.keys(S.kinds).length) actions.push(h("button", { class: "btn", onclick: () => adoptDialog([e.id]) }, "Convert…"));
    if (rec.kind) actions.push(h("button", { class: "btn", onclick: () => detachDialog([e.id]) }, "Detach"));
    actions.push(h("button", { class: "btn danger", onclick: () => removeDialog(e) }, e.series ? "Remove…" : "Delete…"));
  } else {
    actions.push(h("button", { class: "btn", onclick: () => { ui.draft = null; ui.selected = null; render(); } }, "Cancel"));
  }
  return h("aside", { class: "editor" },
    h("div", { class: "head" }, refs.title, h("div", { class: "meta" }, meta)),
    h("div", { class: "scroll" }, body),
    h("div", { class: "actions" }, actions));
}

function renderPlacement(d) {
  if (!d.series) {
    const input = h("input", { type: "number", min: 1, value: d.itemdefid,
      oninput: () => { d.itemdefid = Number(input.value) || null; changed(); } });
    return h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "itemdefid")), input,
      h("div", { class: "hint" }, "Definitions outside a series keep the ID you give them."));
  }
  const s = S.series[d.series];
  const sel = h("select", {
    onchange: () => { d.position = sel.value ? Number(sel.value) : null; changed(); render(); },
  },
  h("option", { value: "" }, `At the end: #${s.members.length + 1} (nothing is renumbered)`),
  s.members.map((id, n) => h("option", { value: n + 1 }, `Before #${n + 1}: ${byId(id).item.name} (${id})`)));
  sel.value = d.position ? String(d.position) : "";
  const warn = d.position ? h("div", { class: "hint", style: "color:var(--warn)" },
    `Items from #${d.position} on move up one ID.` + (S.mode === "release" ? " Players who own them will get different items." : "")) : null;
  return h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "Position in " + s.display_name)), sel, warn);
}

function refLabel(table, key) {
  const t = S.tables[table];
  const row = t && t.rows[key];
  const first = t && t.columns[0];
  return row && first && row[first] ? `${key} — ${row[first]}` : key;
}

function renderKindFields(rec, kind) {
  const rows = Object.entries(kind.fields || {}).map(([name, spec]) => {
    const set = (v) => {
      if (v === "" || v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) delete rec[name];
      else rec[name] = v;
      changed();
    };
    let input;
    const value = rec[name];
    if (spec.type === "multiline") {
      input = h("textarea", { rows: 3, oninput: (ev) => set(ev.target.value) });
      input.value = value ?? "";
    } else if (spec.type === "number") {
      input = h("input", { type: "number", value: value ?? "", oninput: (ev) => set(ev.target.value === "" ? "" : Number(ev.target.value)) });
    } else if (spec.type === "bool") {
      input = h("label", { class: "check" }, h("input", { type: "checkbox", checked: value === true, onchange: (ev) => set(ev.target.checked) }), "yes");
    } else if (spec.type === "ref") {
      const rows = (S.tables[spec.table] || { rows: {} }).rows;
      const keys = Object.keys(rows);
      const info = h("div", { class: "ref-info" });
      const showRow = (key) => {
        const t = S.tables[spec.table];
        const row = t && t.rows[key];
        const parts = row ? t.columns.slice(1).filter((c) => row[c] !== undefined && row[c] !== "")
          .map((c) => h("span", {}, h("b", {}, c + ": "), String(row[c]))) : [];
        if (parts.length <= 3) { info.replaceChildren(...parts); return; }
        info.replaceChildren(h("details", {},
          h("summary", {}, parts.slice(0, 2), h("span", { class: "hint" }, ` +${parts.length - 2} more`)),
          h("div", { class: "ref-info" }, parts.slice(2))));
      };
      const sel = h("select", { onchange: (ev) => { set(ev.target.value); showRow(ev.target.value); } },
        h("option", { value: "" }, "—"),
        value && !keys.includes(String(value)) ? h("option", { value }, `${value} (not in table ${spec.table})`) : null,
        keys.map((k) => h("option", { value: k }, refLabel(spec.table, k))));
      sel.value = value ?? "";
      showRow(value);
      input = h("div", {}, sel, info);
    } else {
      input = h("input", { type: "text", value: value ?? "", oninput: (ev) => set(ev.target.value) });
    }
    if (spec.default && "placeholder" in input) input.placeholder = "default: " + spec.default;
    return h("div", { class: "field" },
      h("div", { class: "label" }, h("b", {}, name),
        h("span", {}, spec.type === "ref" ? `from table ${spec.table}` : spec.type || "text", spec.optional ? " · optional" : "")),
      input);
  });
  return h("div", { class: "section" }, h("h5", {}, "Fields"), rows.length ? rows : h("div", { class: "hint" }, "This kind has no fields."));
}

function renderDerived(rec, kind) {
  const d = ui.draft;
  const rows = Object.entries(kind.derive || {}).filter(([f]) => !(f in (kind.fields || {}))).map(([field, rule]) => {
    const overridden = field in rec;
    const valueEl = h("div", { class: "d-value" });
    refs.derivedValues[field] = valueEl;
    const head = h("div", { class: "d-head" },
      h("span", { class: "d-name", title: "Rule: " + JSON.stringify(rule) }, field),
      overridden
        ? h("span", {}, h("span", { class: "badge warn" }, "override"), " ",
          h("button", { class: "btn small", onclick: () => { delete rec[field]; changed(); render(); } }, "Use rule"))
        : h("button", {
          class: "btn small", title: "Store a value for this item instead of using the rule",
          onclick: () => {
            const p = d.preview;
            rec[field] = clone(p ? p.rules[field] : "") ?? "";
            changed(); render();
          },
        }, "Override"));
    const editor = overridden ? valueEditor(rec[field], (v) => { rec[field] = v; changed(); }, field) : null;
    return h("div", { class: "d-row" + (overridden ? " override" : "") }, head, editor, valueEl);
  });
  const section = h("div", { class: "section" },
    h("h5", {}, "Derived fields", h("button", { class: "linkish", onclick: () => { ui.kindSel = rec.kind; go("kinds"); } }, "edit rules")),
    rows.length ? h("div", { class: "derived" }, rows) : h("div", { class: "hint" }, "This kind derives no fields."));
  setTimeout(updatePreviewDom, 0);
  return section;
}

function typeOf(v) {
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return "number";
  if (typeof v === "string") return "text";
  return "json";
}

function convert(v, type) {
  if (type === "text") return typeof v === "string" ? v : v === null || v === undefined ? "" : JSON.stringify(v);
  if (type === "number") { const n = Number(v); return Number.isFinite(n) ? n : 0; }
  if (type === "bool") return v === true || v === "true";
  return v;
}

function valueEditor(value, onChange, name) {
  const type = typeOf(value);
  if (type === "bool") {
    return h("label", { class: "check" }, h("input", { type: "checkbox", checked: value, onchange: (ev) => onChange(ev.target.checked) }), "true");
  }
  if (type === "number") {
    return h("input", { type: "number", value, oninput: (ev) => onChange(Number(ev.target.value)) });
  }
  if (type === "json") {
    const ta = h("textarea", { rows: 3, class: "mono" });
    ta.value = JSON.stringify(value, null, 2);
    ta.addEventListener("input", () => {
      try { onChange(JSON.parse(ta.value)); ta.classList.remove("invalid"); } catch (e) { ta.classList.add("invalid"); }
    });
    return ta;
  }
  const lines = String(value).split("\n").length;
  const long = name === "description" || lines > 1 || String(value).length > 60;
  const ta = h("textarea", { rows: long ? Math.min(10, Math.max(3, lines + 1)) : 1, oninput: (ev) => onChange(ev.target.value) });
  ta.value = value;
  return ta;
}

function kvEditor(rec, exclude) {
  const wrap = h("div");
  const keys = Object.keys(rec).filter((k) => !exclude.has(k));
  const renameKey = (oldKey, newKey) => {
    const entries = Object.entries(rec);
    for (const k of Object.keys(rec)) delete rec[k];
    for (const [k, v] of entries) rec[k === oldKey ? newKey : k] = v;
  };
  for (const key of keys) {
    const keyInput = h("input", { type: "text", value: key, class: "mono" });
    keyInput.addEventListener("change", () => {
      const nk = keyInput.value.trim();
      if (!nk || nk === key) { keyInput.value = key; return; }
      if (nk in rec || exclude.has(nk)) { toast(`There is already a field called ${nk}.`, true); keyInput.value = key; return; }
      renameKey(key, nk); changed(); render();
    });
    const typeSel = h("select", { onchange: () => { rec[key] = convert(rec[key], typeSel.value); changed(); render(); } },
      ["text", "number", "bool", "json"].map((t) => h("option", { value: t }, t)));
    typeSel.value = typeOf(rec[key]);
    wrap.append(h("div", { class: "kv" }, keyInput, typeSel,
      valueEditor(rec[key], (v) => { rec[key] = v; changed(); }, key),
      h("button", { class: "btn icon", title: "Remove this field", onclick: () => { delete rec[key]; changed(); render(); } }, "✕")));
  }
  const listId = "steam-fields-list";
  const add = h("input", { type: "text", placeholder: "new field name", list: listId, class: "mono" });
  const addField = () => {
    const nk = add.value.trim();
    if (!nk) return;
    if (nk in rec || exclude.has(nk)) { toast(`There is already a field called ${nk}.`, true); return; }
    rec[nk] = ["tradable", "marketable", "hidden", "store_hidden", "game_only", "auto_stack", "granted_manually"].includes(nk) ? false : "";
    changed(); render();
  };
  add.addEventListener("keydown", (ev) => { if (ev.key === "Enter") addField(); });
  wrap.append(h("datalist", { id: listId }, S.steam_fields.map((f) => h("option", { value: f }))),
    h("div", { class: "row" }, h("div", { class: "grow" }, add), h("button", { class: "btn small", onclick: addField }, "+ Add field")));
  return wrap;
}

async function saveDraft() {
  const d = ui.draft;
  const record = draftRecordForServer();
  if (d.mode === "edit") {
    const r = await mutate("item/save", { record }, "Saved.");
    if (r) { openDraft(d.id); render(); requestPreview(); }
    return;
  }
  const body = { record, series: d.series, position: d.position, itemdefid: d.itemdefid };
  const r = await mutate("item/create", body, (res) => `Added item ${res.id}.`);
  if (r) {
    ui.draft = null;
    openDraft(r.id);
    render();
    requestPreview();
  }
}

async function moveDialog(e) {
  const s = S.series[e.series];
  const v = await promptModal(`Move ${e.item.name}`, [{
    name: "position", label: `New position in ${s.display_name} (1–${s.members.length})`, type: "number", value: e.index,
    hint: "Moving renumbers the items between the old and new position.",
  }], "Move");
  if (!v) return;
  const position = Number(v.position);
  if (!Number.isInteger(position) || position < 1 || position > s.members.length) { toast("Enter a position in range.", true); return; }
  const r = await mutate("item/move", { id: e.id, position }, "Moved.");
  if (r) { ui.draft = null; openDraft(r.moved[String(e.id)] ?? e.id); render(); requestPreview(); }
}

async function removeDialog(e) {
  if (!e.series) {
    const ok = await confirmModal("Delete this definition",
      `${e.item.name || e.id} will be removed from the project. Steam keeps the definition it already has for ID ${e.id}.`,
      "Delete", true);
    if (ok && await mutate("item/remove", { id: e.id }, "Deleted.")) { ui.draft = null; ui.selected = null; render(); }
    return;
  }
  const choice = await modal({
    title: `Remove ${e.item.name}`,
    body: h("div", { class: "stack" },
      h("p", {}, h("b", {}, "Close the gap: "), "later items in the series move down one ID, so their IDs change."),
      h("p", {}, h("b", {}, "Leave a gap: "), `ID ${e.id} becomes a dummy item and nothing else moves.`)),
    actions: [{ label: "Cancel", value: null }, { label: "Leave a gap", value: "gap" },
      { label: "Close the gap", value: "shift", class: "danger" }],
  });
  if (!choice) return;
  if (await mutate("item/remove", { id: e.id, shift: choice === "shift" }, "Removed.")) {
    ui.draft = null; ui.selected = null; render();
  }
}

/* ------------------------------------------------------------ kinds page */

function makeKindDraft(name) {
  if (!name || name === "__new__") {
    return { for: name, name: "", old: null, fields: [], rules: [], sample: null };
  }
  const k = S.kinds[name];
  return {
    for: name, name, old: name, sample: null,
    fields: Object.entries(k.fields || {}).map(([n, spec]) => ({
      name: n, type: spec.type || "text", table: spec.table || "", optional: !!spec.optional, default: spec.default || "",
    })),
    rules: Object.entries(k.derive || {}).map(([field, r]) => ({
      field,
      mode: Array.isArray(r) ? "paragraphs" : typeof r === "string" ? "template" : "value",
      text: typeof r === "string" ? r : "",
      paragraphs: Array.isArray(r) ? r.map(String) : [""],
      json: typeof r === "string" || Array.isArray(r) ? "" : JSON.stringify(r),
    })),
  };
}

function kindFromDraft(d) {
  const fields = {};
  for (const f of d.fields) {
    const n = f.name.trim();
    if (!n) throw new Error("Every field needs a name.");
    if (n in fields) throw new Error(`Two fields are called ${n}.`);
    const spec = { type: f.type };
    if (f.type === "ref") spec.table = f.table;
    if (f.optional) spec.optional = true;
    if (f.default) spec.default = f.default;
    fields[n] = spec;
  }
  const derive = {};
  for (const r of d.rules) {
    const n = r.field.trim();
    if (!n) throw new Error("Every derived field needs a Steam field name.");
    if (n in derive) throw new Error(`${n} is derived twice.`);
    if (r.mode === "template") derive[n] = r.text;
    else if (r.mode === "paragraphs") derive[n] = r.paragraphs;
    else {
      try { derive[n] = JSON.parse(r.json); } catch (e) { throw new Error(`The value for ${n} is not valid JSON (e.g. true, false, 5 or "text").`); }
    }
  }
  return { fields, derive };
}

const previewKind = debounce(async () => {
  const d = ui.kindDraft;
  if (!d || !refs.kindPreview) return;
  const users = S.items.filter((e) => e.record && e.record.kind === d.old);
  const sample = users.find((e) => e.id === d.sample) || users[0];
  if (!sample) { refs.kindPreview.textContent = "No item uses this kind yet."; return; }
  let kind;
  try { kind = kindFromDraft(d); } catch (e) { refs.kindPreview.textContent = e.message; return; }
  const name = d.name.trim() || d.old;
  const record = { ...sample.record, kind: name };
  try {
    const { result } = await request("preview", { record, kind_draft: { name, old_name: d.old, kind } });
    refs.kindPreview.textContent = (result.problems.length ? "Problems:\n  " + result.problems.join("\n  ") + "\n\n" : "")
      + JSON.stringify(result.item, null, 2);
  } catch (e) { refs.kindPreview.textContent = e.message; }
}, 250);

function renderKindsPage() {
  const names = Object.keys(S.kinds);
  if (ui.kindSel && ui.kindSel !== "__new__" && !S.kinds[ui.kindSel]) ui.kindSel = null;
  if (!ui.kindSel) ui.kindSel = names[0] || "__new__";
  if (!ui.kindDraft || ui.kindDraft.for !== ui.kindSel) ui.kindDraft = makeKindDraft(ui.kindSel);
  const d = ui.kindDraft;
  const usage = (k) => S.items.filter((e) => e.record && e.record.kind === k).length;
  const list = h("div", { class: "list" },
    names.map((k) => h("button", { class: ui.kindSel === k ? "active" : "", onclick: () => { ui.kindSel = k; render(); } },
      h("span", {}, k), h("span", { class: "hint" }, usage(k)))),
    h("button", { class: ui.kindSel === "__new__" ? "active" : "", onclick: () => { ui.kindSel = "__new__"; render(); } }, "+ New kind"));

  const again = () => { render(); };
  const fieldRows = d.fields.map((f, n) => {
    const tableSel = h("select", { disabled: f.type !== "ref", onchange: (ev) => { f.table = ev.target.value; previewKind(); } },
      h("option", { value: "" }, "—"), Object.keys(S.tables).map((t) => h("option", { value: t }, t)));
    tableSel.value = f.table;
    return h("tr", {},
      h("td", {}, h("input", { type: "text", value: f.name, class: "mono", oninput: (ev) => { f.name = ev.target.value; previewKind(); } })),
      h("td", { class: "narrow" }, (() => {
        const sel = h("select", { onchange: (ev) => { f.type = ev.target.value; again(); previewKind(); } },
          S.field_types.map((t) => h("option", { value: t }, t)));
        sel.value = f.type; return sel;
      })()),
      h("td", {}, tableSel),
      h("td", { class: "narrow" }, h("input", { type: "checkbox", checked: f.optional, onchange: (ev) => { f.optional = ev.target.checked; previewKind(); } })),
      h("td", {}, h("input", { type: "text", value: f.default, class: "mono", placeholder: "none", oninput: (ev) => { f.default = ev.target.value; previewKind(); } })),
      h("td", { class: "narrow" }, h("button", { class: "btn icon", onclick: () => { d.fields.splice(n, 1); again(); previewKind(); } }, "✕")));
  });

  const ruleBlocks = d.rules.map((r, n) => {
    const modeSel = h("select", { onchange: (ev) => {
      r.mode = ev.target.value;
      if (r.mode === "paragraphs" && !r.paragraphs.length) r.paragraphs = [r.text || ""];
      again(); previewKind();
    } }, [["template", "Template"], ["paragraphs", "Paragraphs"], ["value", "Fixed value (JSON)"]].map(([v, l]) => h("option", { value: v }, l)));
    modeSel.value = r.mode;
    let editor;
    if (r.mode === "template") {
      editor = h("textarea", { rows: 1, class: "mono", oninput: (ev) => { r.text = ev.target.value; previewKind(); } });
      editor.value = r.text;
    } else if (r.mode === "paragraphs") {
      editor = h("div", { class: "stack" }, r.paragraphs.map((p, i) => {
        const ta = h("textarea", { rows: 1, class: "mono", oninput: (ev) => { r.paragraphs[i] = ev.target.value; previewKind(); } });
        ta.value = p;
        return h("div", { class: "row" }, h("div", { class: "grow" }, ta),
          h("button", { class: "btn icon", title: "Remove paragraph", onclick: () => { r.paragraphs.splice(i, 1); again(); previewKind(); } }, "✕"));
      }), h("button", { class: "btn small", onclick: () => { r.paragraphs.push(""); again(); } }, "+ Paragraph"),
      h("div", { class: "hint" }, "Empty paragraphs are left out; the rest are joined with a blank line. Handy for optional flavor text: a paragraph that is just {flavor}."));
    } else {
      editor = h("input", { type: "text", class: "mono", value: r.json, placeholder: "true", oninput: (ev) => { r.json = ev.target.value; previewKind(); } });
    }
    const move = (delta) => { const t = n + delta; if (t < 0 || t >= d.rules.length) return; [d.rules[n], d.rules[t]] = [d.rules[t], d.rules[n]]; again(); previewKind(); };
    return h("div", { class: "rule" },
      h("div", { class: "row" },
        h("input", { type: "text", class: "mono", style: "max-width:220px", value: r.field, list: "steam-fields-kind", placeholder: "Steam field",
          oninput: (ev) => { r.field = ev.target.value; previewKind(); } }),
        modeSel, h("span", { style: "flex:1" }),
        h("button", { class: "btn icon", title: "Move up", onclick: () => move(-1) }, "↑"),
        h("button", { class: "btn icon", title: "Move down", onclick: () => move(1) }, "↓"),
        h("button", { class: "btn icon", title: "Remove", onclick: () => { d.rules.splice(n, 1); again(); previewKind(); } }, "✕")),
      editor);
  });

  const users = S.items.filter((e) => e.record && e.record.kind === d.old);
  const sampleSel = users.length ? h("select", { onchange: (ev) => { d.sample = Number(ev.target.value); previewKind(); } },
    users.map((e) => h("option", { value: e.id }, `${e.id}: ${e.item.name}`))) : null;
  if (sampleSel && d.sample) sampleSel.value = String(d.sample);
  refs.kindPreview = h("pre", { class: "json" }, "…");
  setTimeout(previewKind, 0);

  const save = async () => {
    let kind;
    try { kind = kindFromDraft(d); } catch (e) { toast(e.message, true); return; }
    const name = d.name.trim();
    if (!name) { toast("Give the kind a name.", true); return; }
    const r = await mutate("kind/save", { name, old_name: d.old, kind }, `Saved kind ${name}.`);
    if (r) { ui.kindSel = name; ui.kindDraft = null; render(); }
  };
  const del = async () => {
    const n = usage(d.old);
    const ok = await confirmModal(`Delete kind ${d.old}`, n
      ? `${plural(n, "item")} use this kind. They will be detached first: they become plain definitions holding their current fields, so the export does not change.`
      : "The kind is not used by any item.", "Delete", true);
    if (ok && await mutate("kind/delete", { name: d.old, detach_items: n > 0 }, `Deleted kind ${d.old}.`)) {
      ui.kindSel = null; ui.kindDraft = null; render();
    }
  };

  return h("div", { class: "page" },
    h("h2", {}, "Item kinds"),
    h("p", { class: "lead" }, "A kind describes a family of similar items. Its fields are what you enter for each item; its rules build the Steam fields from them. To use a kind for existing items, tick them in the item list and choose Convert."),
    h("div", { class: "two-pane" }, list,
      h("div", {},
        h("div", { class: "card" },
          h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "Name"), d.old ? h("span", {}, plural(usage(d.old), "item")) : null),
            h("input", { type: "text", value: d.name, class: "mono", placeholder: "e.g. skin", oninput: (ev) => { d.name = ev.target.value; } })),
          h("h3", {}, "Fields entered for each item"),
          h("table", { class: "edit" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Type"), h("th", {}, "Table (for ref)"),
            h("th", {}, "Optional"), h("th", {}, "Default (template)"), h("th", {}))), h("tbody", {}, fieldRows)),
          h("button", { class: "btn small", style: "margin-top:6px", onclick: () => { d.fields.push({ name: "", type: "text", table: "", optional: false, default: "" }); again(); } }, "+ Field")),
        h("div", { class: "card" },
          h("h3", {}, "Derived Steam fields"),
          h("datalist", { id: "steam-fields-kind" }, S.steam_fields.map((f) => h("option", { value: f }))),
          ruleBlocks,
          h("button", { class: "btn small", onclick: () => { d.rules.push({ field: "", mode: "template", text: "", paragraphs: [""], json: "" }); again(); } }, "+ Derived field"),
          h("div", { class: "help", style: "margin-top:12px" },
            h("b", {}, "Templates. "), "Write ", h("code", {}, "{field}"), " to insert a field, ", h("code", {}, "{weapon.name}"),
            " for a column of the lookup-table row a ref field points to, ", h("code", {}, "{series}"), ", ",
            h("code", {}, "{series.name}"), ", ", h("code", {}, "{series.index}"), ", ", h("code", {}, "{series.count}"),
            " / ", h("code", {}, "{series.count_no_secret}"), " / ", h("code", {}, "{series.count_secret}"), ", ", h("code", {}, "{itemdefid}"),
            ", or another derived field such as ", h("code", {}, "{name}"), ". ", h("code", {}, "{series.index:03d}"),
            " pads a number; ", h("code", {}, "{{"), " is a literal brace. Example: ",
            h("code", {}, "https://example.com/{weapon}_{mat_id}_small.png"), ".")),
        h("div", { class: "card" },
          h("div", { class: "row" }, h("h3", { class: "grow", style: "margin:0" }, "Preview"), sampleSel),
          h("p", { class: "hint" }, "How a real item of this kind would be exported with the rules above (before saving)."),
          refs.kindPreview),
        h("div", { class: "row" },
          h("button", { class: "btn primary", onclick: save }, d.old ? "Save kind" : "Create kind"),
          d.old ? h("button", { class: "btn", onclick: () => { ui.kindDraft = null; render(); } }, "Revert") : null,
          h("span", { style: "flex:1" }),
          d.old ? h("button", { class: "btn danger", onclick: del }, "Delete kind…") : null))));
}

/* ------------------------------------------------------------ tables page */

function tableUsage(name) {
  const fields = {};
  for (const [kname, k] of Object.entries(S.kinds)) {
    for (const [f, spec] of Object.entries(k.fields || {})) if (spec.type === "ref" && spec.table === name) (fields[kname] ||= []).push(f);
  }
  const counts = {};
  for (const e of S.items) {
    if (!e.record) continue;
    for (const f of fields[e.record.kind] || []) {
      const v = e.record[f];
      if (v !== undefined && v !== "") counts[v] = (counts[v] || 0) + 1;
    }
  }
  return counts;
}

function makeTableDraft(name) {
  if (!name || name === "__new__") return { for: name, name: "", old: null, columns: ["name"], rows: [] };
  const t = S.tables[name];
  return {
    for: name, name, old: name, columns: [...(t.columns || [])],
    rows: Object.entries(t.rows || {}).map(([key, values]) => ({ key, orig: key, values: { ...values } })),
  };
}

function renderTablesPage() {
  const names = Object.keys(S.tables);
  if (ui.tableSel && ui.tableSel !== "__new__" && !S.tables[ui.tableSel]) ui.tableSel = null;
  if (!ui.tableSel) ui.tableSel = names[0] || "__new__";
  if (!ui.tableDraft || ui.tableDraft.for !== ui.tableSel) ui.tableDraft = makeTableDraft(ui.tableSel);
  const d = ui.tableDraft;
  const used = d.old ? tableUsage(d.old) : {};
  const list = h("div", { class: "list" },
    names.map((t) => h("button", { class: ui.tableSel === t ? "active" : "", onclick: () => { ui.tableSel = t; render(); } },
      h("span", {}, t), h("span", { class: "hint" }, Object.keys(S.tables[t].rows || {}).length))),
    h("button", { class: ui.tableSel === "__new__" ? "active" : "", onclick: () => { ui.tableSel = "__new__"; render(); } }, "+ New table"),
    h("button", { onclick: () => importCsvDialog(d.old) }, "Import CSV…"));

  const columnEditors = d.columns.map((c, i) => h("span", { class: "row", style: "display:inline-flex;margin:0 8px 6px 0" },
    h("input", { type: "text", class: "mono", style: "width:130px", value: c, oninput: (ev) => {
      const old = d.columns[i]; const nv = ev.target.value;
      d.columns[i] = nv;
      for (const r of d.rows) { if (old in r.values) { r.values[nv] = r.values[old]; delete r.values[old]; } }
    } }),
    h("button", { class: "btn icon", title: "Remove column", onclick: () => {
      const col = d.columns[i]; d.columns.splice(i, 1); for (const r of d.rows) delete r.values[col]; render();
    } }, "✕")));

  const rows = d.rows.map((r, n) => h("tr", {},
    h("td", {}, h("input", { type: "text", class: "mono", value: r.key, oninput: (ev) => { r.key = ev.target.value; } })),
    d.columns.map((c) => h("td", {}, h("input", { type: "text", value: r.values[c] ?? "", oninput: (ev) => {
      if (ev.target.value === "") delete r.values[c]; else r.values[c] = ev.target.value;
    } }))),
    h("td", { class: "narrow hint" }, r.orig && used[r.orig] ? plural(used[r.orig], "item") : ""),
    h("td", { class: "narrow" }, h("button", { class: "btn icon", onclick: () => { d.rows.splice(n, 1); render(); } }, "✕"))));

  const save = async () => {
    const name = d.name.trim();
    if (!name) { toast("Give the table a name.", true); return; }
    const table = { columns: d.columns.map((c) => c.trim()).filter(Boolean), rows: {} };
    const renames = {};
    for (const r of d.rows) {
      const key = r.key.trim();
      if (!key) { toast("Every row needs a key.", true); return; }
      if (key in table.rows) { toast(`Two rows have the key ${key}.`, true); return; }
      table.rows[key] = r.values;
      if (r.orig && r.orig !== key) renames[r.orig] = key;
    }
    const gone = Object.keys(used).filter((k) => !d.rows.some((r) => r.orig === k));
    if (gone.length && !(await confirmModal("Delete rows in use",
      `Rows ${gone.join(", ")} are used by items. Those items will show problems until you pick another value.`, "Save anyway", true))) return;
    const r = await mutate("table/save", { name, old_name: d.old, table, renames }, `Saved table ${name}.`);
    if (r) { ui.tableSel = name; ui.tableDraft = null; render(); }
  };
  const del = async () => {
    if (await confirmModal(`Delete table ${d.old}`, "The table and its rows will be deleted. Kinds that use it must be changed first.", "Delete", true)
      && await mutate("table/delete", { name: d.old }, `Deleted table ${d.old}.`)) {
      ui.tableSel = null; ui.tableDraft = null; render();
    }
  };

  return h("div", { class: "page" },
    h("h2", {}, "Lookup tables"),
    h("p", { class: "lead" }, "A table maps keys to values, e.g. the weapon pistol to the name \"Pistol\". A kind field of type ref holds a key; templates read the row's columns as {weapon.name}. Changing a value here changes every item that uses it. Renaming a key updates the items too."),
    h("div", { class: "two-pane" }, list,
      h("div", {},
        h("div", { class: "card" },
          h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "Name")),
            h("input", { type: "text", class: "mono", value: d.name, placeholder: "e.g. weapon", oninput: (ev) => { d.name = ev.target.value; } })),
          h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "Columns")),
            h("div", {}, columnEditors, h("button", { class: "btn small", onclick: () => { d.columns.push("column" + (d.columns.length + 1)); render(); } }, "+ Column")))),
        h("div", { class: "card" },
          h("div", { class: "hscroll" }, h("table", { class: "edit wide" },
            h("thead", {}, h("tr", {}, h("th", {}, "Key"), d.columns.map((c) => h("th", {}, c)), h("th", {}, "Items"), h("th", {}))),
            h("tbody", {}, rows))),
          h("button", { class: "btn small", style: "margin-top:6px", onclick: () => { d.rows.push({ key: "", orig: null, values: {} }); render(); } }, "+ Row")),
        h("div", { class: "row" },
          h("button", { class: "btn primary", onclick: save }, d.old ? "Save table" : "Create table"),
          d.old ? h("button", { class: "btn", onclick: () => { ui.tableDraft = null; render(); } }, "Revert") : null,
          h("span", { style: "flex:1" }),
          d.old ? h("button", { class: "btn danger", onclick: del }, "Delete table…") : null))));
}

/* Import a CSV file (e.g. an Unreal DataTable export) into a table, for reference. */
async function importCsvDialog(defaultTable) {
  const st = { csv: null, preview: null, fileName: "" };
  const file = h("input", { type: "file", accept: ".csv,text/csv" });
  const table = h("input", { type: "text", class: "mono", value: defaultTable || "", list: "csv-tables", placeholder: "table name" });
  const keyCase = h("select", {}, [["auto", "match the table (lower case if its keys are)"], ["lower", "lower case"], ["keep", "as in the file"]]
    .map(([v, l]) => h("option", { value: v }, l)));
  const keySel = h("select", { onchange: () => renderColumns(false) });
  const colsBox = h("div", { class: "hint" }, "Choose a CSV file. The first row must name the columns.");
  const fillsBox = h("div");
  const fills = [];
  const report = h("div");
  const colState = [];

  const renderFills = () => {
    const header = st.preview ? st.preview.header : [];
    const targetCols = (S.tables[table.value] || { columns: [] }).columns;
    fillsBox.replaceChildren(h("div", {}, h("datalist", { id: "csv-fill-targets" }, targetCols.map((c) => h("option", { value: c }))),
      fills.map((f, n) => {
        const src = h("select", { onchange: (ev) => { f.source = ev.target.value; } }, header.map((c) => h("option", { value: c }, c)));
        src.value = f.source;
        return h("div", { class: "row", style: "margin-bottom:6px" },
          h("input", { type: "text", class: "mono", value: f.target, list: "csv-fill-targets", placeholder: "table column, e.g. name",
            oninput: (ev) => { f.target = ev.target.value; } }),
          h("span", {}, "←"), src,
          h("button", { class: "btn icon", onclick: () => { fills.splice(n, 1); renderFills(); } }, "✕"));
      }),
      st.preview ? h("button", { class: "btn small", onclick: () => { fills.push({ target: "", source: header[1] || header[0] }); renderFills(); } }, "+ Fill a column where it is empty") : null));
  };

  const renderColumns = (fresh) => {
    const p = st.preview;
    if (fresh) {
      keySel.replaceChildren(...p.header.map((c, n) => h("option", { value: String(n) }, c)));
      keySel.value = "0";
      colState.length = 0;
      p.header.forEach((c, n) => {
        const samples = p.samples.map((r) => r[n]).filter(Boolean);
        const looksLikeClass = samples.length > 0 && samples.every((v) => /_C$/.test(v));
        colState.push({ include: !looksLikeClass, target: p.columns[n], samples });
      });
    }
    const key = Number(keySel.value || 0);
    const rows = p.header.map((c, n) => {
      const st2 = colState[n];
      const isKey = n === key;
      return h("tr", { "data-col": n, class: isKey ? "hint" : "" },
        h("td", { class: "narrow" }, isKey ? "" : h("input", { type: "checkbox", checked: st2.include, onchange: (ev) => { st2.include = ev.target.checked; } })),
        h("td", { class: "mono" }, c),
        h("td", {}, isKey ? h("i", {}, "row key") : h("input", { type: "text", class: "mono", value: st2.target, oninput: (ev) => { st2.target = ev.target.value; } })),
        h("td", { class: "hint", style: "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: st2.samples.join("\n") }, st2.samples[0] || ""));
    });
    colsBox.replaceChildren(
      h("p", { class: "hint" }, `${plural(p.rows, "row")}. Each ticked column is stored under the name on the right; columns that look like class references start unticked.`),
      h("div", { class: "hscroll" }, h("table", { class: "edit" },
        h("thead", {}, h("tr", {}, h("th", {}), h("th", {}, "CSV column"), h("th", {}, "Store as"), h("th", {}, "Example"))),
        h("tbody", {}, rows))));
    renderFills();
  };

  file.addEventListener("change", async () => {
    const f = file.files[0];
    if (!f) return;
    st.csv = await f.text();
    st.fileName = f.name;
    if (!table.value) table.value = f.name.replace(/\.[^.]+$/, "").replace(/\W+/g, "_").toLowerCase();
    try {
      st.preview = (await request("table/csv-preview", { csv: st.csv })).result;
      renderColumns(true);
    } catch (e) { colsBox.replaceChildren(h("div", { class: "problems" }, e.message)); }
  });

  const body = () => {
    const key = Number(keySel.value || 0);
    const only = [], rename = {};
    st.preview.header.forEach((c, n) => {
      if (n === key || !colState[n].include) return;
      only.push(c);
      if (colState[n].target && colState[n].target !== st.preview.columns[n]) rename[c] = colState[n].target;
    });
    return {
      name: table.value.trim(), csv: st.csv, key_column: st.preview.header[key], only, rename,
      fills: fills.filter((f) => f.target.trim()).map((f) => [f.target.trim(), f.source]), key_case: keyCase.value,
    };
  };
  const showReport = (r) => report.replaceChildren(h("pre", { class: "json" }, r.lines.join("\n")));

  const done = await modal({
    title: "Import a CSV file into a table", wide: true,
    body: h("div", { class: "stack" },
      h("p", { class: "hint" }, "For reference data such as an Unreal Engine DataTable export. NSLOCTEXT(...) text, row handles and asset paths are cleaned up. Keys match existing rows ignoring case. The file's columns are added next to the table's own columns, so nothing your items use changes unless you store a CSV column under one of those names."),
      h("div", { class: "field" }, file),
      h("div", { class: "row" },
        h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "Table")), table,
          h("datalist", { id: "csv-tables" }, Object.keys(S.tables).map((t) => h("option", { value: t })))),
        h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "Key column")), keySel),
        h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "New keys")), keyCase)),
      colsBox,
      h("div", {}, h("div", { class: "label hint" }, "Fill empty table columns from the file (existing values are kept; differences are reported):"), fillsBox),
      report),
    actions: [
      { label: "Cancel", value: null },
      { label: "Preview", run: async () => {
        if (!st.preview) { toast("Choose a CSV file first.", true); return undefined; }
        try { showReport((await request("table/import", { ...body(), dry_run: true })).result); } catch (e) { toast(e.message, true); }
        return undefined;
      } },
      { label: "Import", class: "primary", run: async () => {
        if (!st.preview) { toast("Choose a CSV file first.", true); return undefined; }
        const r = await mutate("table/import", body(), (res) => `Imported ${res.added.length + res.updated.length} rows into ${res.table}.`);
        return r ? r : undefined;
      } },
    ],
  });
  if (done) {
    ui.tableSel = done.table;
    ui.tableDraft = null;
    render();
    modal({ title: `Imported into ${done.table}`, body: h("pre", { class: "json" }, done.lines.join("\n")) });
  }
}

/* ------------------------------------------------------------ series page */

function makeSeriesDraft(key) {
  if (!key || key === "__new__") {
    return { for: key, is_new: true, key: "", name: "", first_id: "", last_id: "", template: "", secret: "", containers: [], generators: [] };
  }
  const s = S.series[key];
  return {
    for: key, is_new: false, key, name: s.name || "", first_id: s.first_id, last_id: s.last_id,
    template: typeof s.description_template === "string" ? s.description_template : "",
    secret: Array.isArray(s.secret) ? s.secret.join(", ") : (s.secret || ""),
    containers: Object.entries(s.containers || {}).map(([id, cfg]) => ({ id, exclude: ((cfg || {}).exclude || []).join(", ") })),
    generators: Object.entries(s.generators || {}).map(([id, rule]) => ({ id, rule })),
  };
}

function renderSeriesPage() {
  const keys = Object.keys(S.series);
  if (ui.seriesSel && ui.seriesSel !== "__new__" && !S.series[ui.seriesSel]) ui.seriesSel = null;
  if (!ui.seriesSel) ui.seriesSel = keys[0] || "__new__";
  if (!ui.seriesDraft || ui.seriesDraft.for !== ui.seriesSel) ui.seriesDraft = makeSeriesDraft(ui.seriesSel);
  const d = ui.seriesDraft;
  const s = d.is_new ? null : S.series[d.key];
  const list = h("div", { class: "list" },
    keys.map((k) => h("button", { class: ui.seriesSel === k ? "active" : "", onclick: () => { ui.seriesSel = k; render(); } },
      h("span", {}, S.series[k].display_name), h("span", { class: "hint" }, S.series[k].members.length))),
    h("button", { class: ui.seriesSel === "__new__" ? "active" : "", onclick: () => { ui.seriesSel = "__new__"; render(); } }, "+ New series"));
  const outside = S.items.filter((e) => !e.dummy && !e.series);
  const options = (filter) => [h("option", { value: "" }, "—"),
    outside.filter(filter).map((e) => h("option", { value: e.id }, `${e.id}: ${e.item.name || "(no name)"}`))];
  const input = (key, attrs) => h("input", { type: "text", value: d[key], ...attrs, oninput: (ev) => { d[key] = ev.target.value; } });

  const containerRows = d.containers.map((c, n) => {
    const sel = h("select", { onchange: (ev) => { c.id = ev.target.value; } }, options(() => true));
    sel.value = c.id;
    return h("tr", {}, h("td", {}, sel),
      h("td", {}, h("input", { type: "text", class: "mono", value: c.exclude, placeholder: "e.g. rarity:epic", oninput: (ev) => { c.exclude = ev.target.value; } })),
      h("td", { class: "narrow" }, h("button", { class: "btn icon", onclick: () => { d.containers.splice(n, 1); render(); } }, "✕")));
  });
  const generatorRows = d.generators.map((g, n) => {
    const sel = h("select", { onchange: (ev) => { g.id = ev.target.value; } }, options((e) => e.item.type === "generator"));
    sel.value = g.id;
    return h("tr", {}, h("td", {}, sel),
      h("td", {}, h("input", { type: "text", class: "mono", value: g.rule, placeholder: "e.g. rarity:common", oninput: (ev) => { g.rule = ev.target.value; } })),
      h("td", { class: "narrow" }, h("button", { class: "btn icon", onclick: () => { d.generators.splice(n, 1); render(); } }, "✕")));
  });

  const save = async () => {
    const config = {
      name: d.name.trim(), last_id: Number(d.last_id),
      description_template: d.template,
      secret: d.secret.includes(",") ? d.secret.split(",").map((x) => x.trim()).filter(Boolean) : d.secret.trim(),
      containers: Object.fromEntries(d.containers.filter((c) => c.id).map((c) => [c.id, { exclude: c.exclude.split(/[,\s]+/).filter(Boolean) }])),
      generators: Object.fromEntries(d.generators.filter((g) => g.id).map((g) => [g.id, g.rule])),
    };
    if (d.is_new) config.first_id = Number(d.first_id);
    const key = d.key.trim();
    const r = await mutate("series/save", { key, config, is_new: d.is_new }, `Saved series ${key}.`);
    if (r) {
      for (const note of r.notes || []) toast(note);
      ui.seriesSel = key; ui.seriesDraft = null; render();
    }
  };
  const del = async () => {
    if (await confirmModal(`Delete series ${d.key}`, "Only a series without items can be deleted.", "Delete", true)
      && await mutate("series/delete", { key: d.key }, "Deleted.")) { ui.seriesSel = null; ui.seriesDraft = null; render(); }
  };

  return h("div", { class: "page" },
    h("h2", {}, "Series setup"),
    h("p", { class: "lead" }, "A series is a range of itemdefids. Items are numbered by their position in it, and that number is written into their descriptions. Containers get a list of the series' items at {contents}, and can use {count}, {count_no_secret}, {count_secret} and {series_name}; generators listed here always hold every series item with the given tags."),
    h("div", { class: "two-pane" }, list,
      h("div", {},
        h("div", { class: "card" },
          h("div", { class: "row" },
            h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "Key"), h("span", {}, "as in the series: tag")),
              input("key", { class: "mono", disabled: !d.is_new, placeholder: "e.g. crate3" })),
            h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "Display name")), input("name", { placeholder: "e.g. Gamma Series" }))),
          h("div", { class: "row" },
            h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "First ID")), input("first_id", { type: "number", disabled: !d.is_new })),
            h("div", { class: "field grow" }, h("div", { class: "label" }, h("b", {}, "Last ID"), h("span", {}, "room to grow")), input("last_id", { type: "number" }))),
          s ? h("p", { class: "hint" }, `${plural(s.members.length, "item")} (${s.members.length - s.secret_ids.length} + ${s.secret_ids.length} secret rares); IDs used up to ${s.allocated_through ?? "—"}; next free ID ${s.next_id}.`) : null,
          h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "Secret rares"),
            h("span", {}, "items with these tags; empty = the tags the containers leave out")),
          input("secret", { class: "mono", placeholder: s && !("secret" in s) && s.secret_rules.length ? s.secret_rules.join(", ") + " (from the containers)" : "e.g. rarity:epic" })),
          h("div", { class: "field" }, h("div", { class: "label" }, h("b", {}, "Description template"), h("span", {}, "empty = the global one")),
            (() => { const ta = h("textarea", { rows: 2, class: "mono", placeholder: S.settings.description_template, oninput: (ev) => { d.template = ev.target.value; } }); ta.value = d.template; return ta; })())),
        h("div", { class: "card" },
          h("h3", {}, "Containers"),
          h("table", { class: "edit" }, h("thead", {}, h("tr", {}, h("th", {}, "Definition"), h("th", {}, "Leave out items tagged"), h("th", {}))), h("tbody", {}, containerRows)),
          h("button", { class: "btn small", style: "margin-top:6px", onclick: () => { d.containers.push({ id: "", exclude: "" }); render(); } }, "+ Container")),
        h("div", { class: "card" },
          h("h3", {}, "Generators"),
          h("table", { class: "edit" }, h("thead", {}, h("tr", {}, h("th", {}, "Generator"), h("th", {}, "Every series item tagged"), h("th", {}))), h("tbody", {}, generatorRows)),
          h("button", { class: "btn small", style: "margin-top:6px", onclick: () => { d.generators.push({ id: "", rule: "" }); render(); } }, "+ Generator")),
        h("div", { class: "row" },
          h("button", { class: "btn primary", onclick: save }, d.is_new ? "Create series" : "Save series"),
          !d.is_new ? h("button", { class: "btn", onclick: () => { ui.seriesDraft = null; render(); } }, "Revert") : null,
          h("span", { style: "flex:1" }),
          s && !s.members.length ? h("button", { class: "btn danger", onclick: del }, "Delete series…") : null))));
}

/* ------------------------------------------------------------ settings page */

function renderSettingsPage() {
  const release = S.mode === "release";
  const diffBox = h("div", { class: "hint" }, "…");
  request("diff", {}).then(({ result }) => {
    if (!result.recorded) { diffBox.textContent = "No live baseline is recorded."; return; }
    diffBox.replaceChildren(...[
      result.impacts.length
        ? h("div", {}, h("b", { style: "color:var(--danger)" }, `${plural(result.impacts.length, "live item")} changed since the baseline:`),
          h("div", { class: "impacts" }, result.impacts.map((i) => h("div", {}, i.text))))
        : h("div", {}, "No live item has changed since the baseline."),
      result.added.length ? h("div", {}, `${plural(result.added.length, "new item")} not live yet.`) : null,
    ].filter(Boolean));
  }).catch((e) => { diffBox.textContent = e.message; });

  const modeCard = h("div", { class: "card" },
    h("h3", {}, "Mode: ", h("span", { class: "badge " + S.mode }, release ? "RELEASE MODE" : "prerelease")),
    h("p", {}, release
      ? "Changes that would alter an item players may already own (renumbering, removing or renaming a live item) show a warning and need the confirmation phrase."
      : "Items can be inserted, moved and removed freely. Switch to release mode when players start owning items."),
    h("button", { class: "btn " + (release ? "danger" : "primary"), onclick: async () => {
      if (!release && !(await confirmModal("Switch to release mode",
        "The current definitions are recorded as live. From then on, changes that alter an item players may own need explicit confirmation. Make sure the current export is what is on Steam.",
        "Switch to release mode"))) return;
      await mutate("mode", { mode: release ? "prerelease" : "release" }, (r) => `Now in ${r.mode} mode.`);
    } }, release ? "Switch back to prerelease…" : "Switch to release mode…"));

  const liveCard = h("div", { class: "card" },
    h("h3", {}, "Live baseline"),
    h("p", { class: "hint" }, S.live ? `${S.live.count} items recorded as live on ${S.live.recorded_at}.` : "Nothing recorded yet. It is recorded when you switch to release mode."),
    diffBox,
    h("p", {}, "After uploading an export to Steam, record it as live so later changes are compared with what is actually on Steam."),
    h("button", { class: "btn", onclick: async () => {
      if (await confirmModal("Mark as live", "Record the current definitions as what is live on Steam? Do this after uploading them.", "Mark as live"))
        await mutate("mark-live", {}, (r) => `Recorded ${r.count} items as live.`);
    } }, "Mark current definitions as live"));

  const exportPath = h("input", { type: "text", value: S.export_path, class: "mono" });
  const markLive = h("input", { type: "checkbox" });
  const exportCard = h("div", { class: "card" },
    h("h3", {}, "Export for Steam"),
    h("p", { class: "hint" }, "Writes every item definition to one file, ready to upload in Steamworks."),
    h("div", { class: "field" }, exportPath),
    h("div", { class: "row" }, h("label", { class: "check" }, markLive, "also record it as live"), h("span", { style: "flex:1" }),
      h("button", { class: "btn primary", onclick: () => mutate("export", { path: exportPath.value, mark_live: markLive.checked },
        (r) => `Wrote ${r.items} definitions to ${r.path}.`) }, "Export")));

  const files = h("input", { type: "file", multiple: true, accept: ".json,application/json" });
  const replace = h("input", { type: "checkbox" });
  const importCard = h("div", { class: "card" },
    h("h3", {}, "Import Steam item definition files"),
    h("p", { class: "hint" }, "Adds the definitions in the files to the project. Series tags are detected as on the command line."),
    h("div", { class: "field" }, files),
    h("div", { class: "row" }, h("label", { class: "check" }, replace, "replace definitions already in the project"), h("span", { style: "flex:1" }),
      h("button", { class: "btn", onclick: async () => {
        if (!files.files.length) { toast("Choose one or more files.", true); return; }
        const payload = await Promise.all([...files.files].map(async (f) => ({ name: f.name, content: await f.text() })));
        const r = await mutate("import", { files: payload, replace: replace.checked }, "Imported.");
        if (r) modal({ title: "Import report", body: h("pre", { class: "json" }, r.report.join("\n")) });
      } }, "Import")));

  const tmpl = h("textarea", { rows: 3, class: "mono" });
  tmpl.value = typeof S.settings.description_template === "string" ? S.settings.description_template : JSON.stringify(S.settings.description_template);
  const templateCard = h("div", { class: "card" },
    h("h3", {}, "Series line in descriptions"),
    h("p", { class: "hint" }, "Applied to every series item's description. Fields: {description}, {series_name}, {index}, {count}, {count_no_secret}, {count_secret}, {series}, {itemdefid}, {name}, {tags[rarity]} and the item's own fields."),
    tmpl,
    h("div", { class: "row", style: "margin-top:8px" }, h("span", { style: "flex:1" }),
      h("button", { class: "btn primary", onclick: () => mutate("settings/save", { description_template: tmpl.value }, "Saved the template.") }, "Save")));

  const dummy = h("textarea", { rows: 9, class: "mono" });
  dummy.value = JSON.stringify(S.settings.dummy_item, null, 2);
  const dummyCard = h("div", { class: "card" },
    h("h3", {}, "Dummy item"),
    h("p", { class: "hint" }, "Exported for series IDs that are no longer used, so a stale definition never lingers on Steam. {itemdefid} is replaced by the ID."),
    dummy,
    h("div", { class: "row", style: "margin-top:8px" }, h("span", { style: "flex:1" }),
      h("button", { class: "btn primary", onclick: () => {
        let value;
        try { value = JSON.parse(dummy.value); } catch (e) { toast("That is not valid JSON.", true); return; }
        mutate("settings/save", { dummy_item: value }, "Saved the dummy item.");
      } }, "Save")));

  const appCard = h("div", { class: "card" },
    h("h3", {}, "Project file"),
    h("p", { class: "hint mono" }, S.path),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: switchProject }, "Open another project…"),
      h("span", { style: "flex:1" }),
      h("button", { class: "btn danger", onclick: quitApp }, "Quit sisdefman")));

  return h("div", { class: "page" },
    h("h2", {}, "Settings & export"),
    h("p", { class: "lead" }, S.path),
    h("div", { class: "cards" }, modeCard, liveCard, exportCard, importCard, templateCard, dummyCard, appCard));
}

/* ------------------------------------------------------------ check page */

function renderCheckPage() {
  const groups = { error: [], warning: [], note: [] };
  for (const i of S.issues) groups[i.level].push(i);
  const item = (i) => h("li", {},
    i.itemdefid !== null && i.itemdefid !== undefined && byId(i.itemdefid) && !byId(i.itemdefid).dummy
      ? h("button", { class: "linkish mono", onclick: () => { ui.page = "items"; ui.scope = { type: "all" }; openDraft(i.itemdefid); render(); requestPreview(); } }, `[${i.itemdefid}]`)
      : i.itemdefid !== null && i.itemdefid !== undefined ? h("span", { class: "mono" }, `[${i.itemdefid}]`) : null,
    " ", i.text);
  const block = (level, title, cls) => groups[level].length ? h("div", { class: "card" },
    h("h3", {}, h("span", { class: "badge " + cls }, groups[level].length), " ", title),
    h("ul", { class: "issues" }, groups[level].map(item))) : null;
  return h("div", { class: "page" },
    h("h2", {}, "Check"),
    h("p", { class: "lead" }, "Problems with the project. Errors block exporting; warnings are worth a look; notes are informational."),
    S.issues.length ? null : h("div", { class: "card" }, "No problems found."),
    block("error", "Errors", "danger"), block("warning", "Warnings", "warn"), block("note", "Notes", ""));
}

/* ------------------------------------------------------------ start */

document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s" && ui.page === "items" && ui.draft) {
    ev.preventDefault();
    if (ui.draft.dirty) saveDraft();
  }
});
window.addEventListener("beforeunload", (ev) => {
  if (ui.draft && ui.draft.dirty) { ev.preventDefault(); ev.returnValue = ""; }
});

refresh().catch((e) => {
  document.getElementById("app").replaceChildren(h("div", { class: "placeholder" }, h("h3", {}, "Could not load the project"), e.message));
});
