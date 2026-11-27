import {readPointer, readVersion, ReleaseError} from "./release.mjs";
import {en, zh, columnLabels, panelLabels} from "./labels.mjs";
import {evidenceDetails, node, options, renderDecision, renderMap, tableRows} from "./views.mjs";

const byId = id => document.getElementById(id);
const keys = {
  project: "專案ID", name: "專案名稱", developer: "開發商", state: "州省地區",
  stage: "開發階段", deposit: "礦床型態", ore: "礦石蘊藏量_Mt", grade: "TREO品位_pct",
  contained: "總TREO蘊藏量_kt", source: "有來源文件",
};
const defaultColumns = [keys.name, keys.developer, keys.state, keys.stage, keys.ore, keys.grade, keys.contained, keys.source];
const pageSize = 12;
const base = new URL("./", location.href);
const preview = new URLSearchParams(location.search).get("preview") === "1";
const tabs = new Set(["census", "map", "resources", "mcda"]);
const filters = {"state-filter":keys.state, "stage-filter":keys.stage, "deposit-filter":keys.deposit};
const mapFilters = {"map-stage":keys.stage, "map-deposit":keys.deposit};
const magnetSpecies = /(?:Pr6O11|Nd2O3|Tb4O7|Dy2O3|NdPr|MREO|磁)/i;
const current = {
  language: localStorage.getItem("au-ree-language") === "en" ? "en" : "zh",
  pointer: null, data: null, request: null, checked: null, next: null, message: null,
  tab: "census", project: null, selectedColumns: new Set(defaultColumns),
  page: 0, sort: null, ascending: true, outline: null, csvUrl: null, evidenceTarget: null,
};
const t = key => (current.language === "en" ? en : zh)[key] ?? key;
const number = value => new Intl.NumberFormat(current.language === "en" ? "en-AU" : "zh-TW").format(value);
const label = col => current.language === "en"
  ? (col.label_en ?? columnLabels[col.id] ?? (col.id.includes("_") ? col.id.split("_")[0] + (col.unit ? ` (${col.unit})` : "") : col.label ?? col.id))
  : col.label ?? col.id;

const projects = () => current.data.census.projects;
const column = id => current.data.census.columns[current.data.columnIds.indexOf(id)];
const cell = (project, id) => project?.cells[current.data.columnIds.indexOf(id)];
const name = id => cell(projects().find(p => p.id === id), keys.name)?.value || id;
const displayState = state => t(state === "UNKNOWN" ? "missing" : "na");

function translate() {
  document.documentElement.lang = current.language === "en" ? "en" : "zh-Hant";
  document.title = `AU REE · ${t("mast")}`;
  byId("language").textContent = current.language === "en" ? "中文" : "English";
  byId("language").lang = current.language === "en" ? "zh-Hant" : "en";
  for (const tag of document.querySelectorAll("[data-i]")) tag.textContent = t(tag.dataset.i);
  for (const input of document.querySelectorAll("[data-placeholder]")) input.placeholder = t(input.dataset.placeholder);
  byId("table-region").setAttribute("aria-label", t("explorer"));
  byId("compare-region").setAttribute("aria-label", t("resourcesTitle"));
  if (current.data) renderVersion();
  else if (byId("version").disabled) byId("version").options[0].textContent = t(current.message === "loading" ? "loading" : "noVersion");
  renderStatus();
}

function deepLink(push = false) {
  const url = new URL(location.href);
  if (current.data) url.searchParams.set("release", current.data.entry.release_id);
  url.searchParams.set("tab", current.tab);
  if (current.project) url.searchParams.set("project", current.project);
  else url.searchParams.delete("project");
  history[push ? "pushState" : "replaceState"]({}, "", url);
}

function setTab(tab, push = false) {
  current.tab = tabs.has(tab) ? tab : "census";
  for (const button of byId("tabs").querySelectorAll("button")) {
    const active = button.dataset.tab === current.tab;
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
    byId(button.dataset.tab).hidden = !active;
  }
  if (current.tab === "map" && current.data) updateMap();
  if (push) deepLink(true);
}

function renderStatus() {
  const target = byId("status");
  target.replaceChildren();
  target.className = "banner";
  if (current.message) {
    if (!["loading", "noFormal", "checkDone"].includes(current.message)) target.classList.add("error");
    const detail = t(current.message);
    target.textContent = current.data && target.classList.contains("error") ? `${detail} ${t("stale")}` : detail;
  } else if (preview && current.data) {
    target.classList.add("candidate");
    target.textContent = t("candidateNotice");
  }
  if (preview && current.data && current.message && !target.classList.contains("error")) target.classList.add("candidate");
  if (current.next) {
    const link = node("button", t("updateReady"), "button secondary update-button");
    link.type = "button";
    link.addEventListener("click", () => changeVersion(current.next.release_id, current.checked, true));
    target.append(link);
  }
}

function report(error) {
  if (error.name === "AbortError") return;
  current.message = error instanceof ReleaseError ? error.code : "badRelease";
  if (!current.data) byId("version").options[0].textContent = t("noVersion");
  renderStatus();
}

function setupVersion(entry, pointer) {
  const choices = [...pointer.entries.values()].map(item => [
    item.release_id,
    `${item.cutoff}  ·  ${item.release_id.slice(0, 10)}`,
  ]);
  options(byId("version"), choices, entry.release_id);
  byId("version").disabled = false;
  const releaseStatus = byId("release-status");
  releaseStatus.textContent = t(preview ? "candidate" : entry.release_id === pointer.active.release_id ? "formal" : "formalArchive");
  releaseStatus.classList.toggle("preview", preview);
  byId("cutoff").textContent = `${t("cutoff")} ${entry.cutoff}`;
  byId("cutoff").title = current.data.manifest.timezone ?? "";
  byId("footer-release").textContent = entry.release_id;
}

function renderMetrics() {
  const {coverage, mcda} = current.data;
  const captured = coverage.project_source_search_statuses?.SOURCE_ACQUIRED ?? coverage.projects_with_source ?? 0;
  const complete = mcda.panels.filter(item => item.status === "PASS").length;
  const metrics = [
    [number(coverage.projects), "totalProjects"],
    [`${number(coverage.cell_states.PRESENT)} / ${number(coverage.cells)}`, "present", `${t("unknown")} ${number(coverage.cell_states.UNKNOWN)} · ${t("notApplicable")} ${number(coverage.cell_states.NOT_APPLICABLE)}`],
    [`${number(captured)} / ${number(coverage.projects)}`, "sources"],
    [number((coverage.formal_direct_cells ?? 0) + (coverage.formal_derived_cells ?? 0)), "formalValues"],
    [`${number(complete)} / ${number(mcda.panels.length)}`, "completePanels", `${t("comparable")} ${number(coverage.comparable_project_criteria ?? 0)}`],
  ];
  byId("metrics").replaceChildren(...metrics.map(([total, metric, note]) => {
    const card = node("div", null, "metric");
    card.append(node("b", total), node("span", t(metric), "mini"));
    if (note) card.append(node("span", note, "mini"));
    return card;
  }));
}

function filterOptions() {
  for (const [filterId, key] of Object.entries({...filters, ...mapFilters})) {
    const input = byId(filterId);
    const values = [...new Set(projects().map(p => cell(p, key).value).filter(value => value !== null))];
    options(input, [["", t("all")], ["__unknown", t("missing")], ...values.sort().map(value => [value, value])], input.value);
  }
  const dataFilter = byId("data-filter");
  options(dataFilter, [["", t("all")], ["UNKNOWN", t("missing")], ["NOT_APPLICABLE", t("na")], ["PRESENT", t("present")]], dataFilter.value);
}

function renderColumns() {
  const box = byId("column-options");
  const create = (key, update) => {
    const button = node("button", t(key), "button secondary");
    button.type = "button";
    button.addEventListener("click", () => {
      current.selectedColumns = new Set(update);
      renderColumns(); renderTable();
    });
    return button;
  };
  const identified = projects().some(project => cell(project, keys.name).value !== null);
  const defaults = identified ? defaultColumns : [keys.project, ...defaultColumns];
  box.replaceChildren(create("displayAll", current.data.columnIds), create("defaultColumns", defaults));
  const groups = new Map();
  for (const col of current.data.census.columns) {
    const group = col.display_group || "metadata";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(col);
  }
  for (const [group, columns] of groups) {
    const item = node("details");
    item.open = true;
    item.append(node("summary", t("group_" + group.toLowerCase())));
    for (const col of columns) {
      const check = node("input");
      check.type = "checkbox";
      check.checked = current.selectedColumns.has(col.id);
      check.addEventListener("change", () => {
        if (!check.checked && current.selectedColumns.size === 1) {
          check.checked = true;
          return;
        }
        if (check.checked) current.selectedColumns.add(col.id);
        else current.selectedColumns.delete(col.id);
        renderTable();
      });
      const itemLabel = node("label");
      itemLabel.append(check, node("span", label(col)));
      item.append(itemLabel);
    }
    box.append(item);
  }
}

function selectRows(visible) {
  const q = byId("search").value.trim().toLocaleLowerCase();
  const state = byId("data-filter").value;
  const filtered = projects().filter(project => {
    if (q && ![keys.project, keys.name, keys.developer, "ASX代碼"].some(id =>
      cell(project, id).value?.toLocaleLowerCase().includes(q))) return false;
    for (const [input, id] of Object.entries(filters)) {
      const filter = byId(input).value;
      const item = cell(project, id);
      const actual = item.state === "UNKNOWN" ? "__unknown" : item.value;
      if (filter && filter !== actual) return false;
    }
    return !state || visible.some(id => cell(project, id).state === state);
  });
  if (!current.sort) return filtered;
  const collator = new Intl.Collator(current.language === "en" ? "en" : "zh-Hant", {numeric: true, sensitivity: "base"});
  const dtype = column(current.sort)?.data_type;
  const numericType = ["number", "numeric", "decimal", "integer"].includes(dtype?.toLowerCase()) || [keys.ore, keys.grade, keys.contained].includes(current.sort);
  return filtered.sort((left, right) => {
    const a = cell(left, current.sort).value, b = cell(right, current.sort).value;
    if (a === null || b === null) return a !== b ? (a === null ? 1 : -1) : collator.compare(left.id, right.id);
    const numeric = numericType && Number.isFinite(Number(a)) && Number.isFinite(Number(b));
    const order = numeric ? Number(a) - Number(b) : collator.compare(a, b);
    return (current.ascending ? order : -order) || collator.compare(left.id, right.id);
  });
}

function cellButton(projectId, col, item) {
  const trigger = node("button", item.value ?? displayState(item.state), item.state === "PRESENT" ? "cell-button" : "cell-button missing");
  trigger.type = "button";
  trigger.setAttribute("aria-label", `${label(col)} · ${item.value ?? displayState(item.state)}`);
  trigger.addEventListener("click", () => openEvidence(projectId, col, item));
  return trigger;
}

function renderTable() {
  const visible = current.data.columnIds.filter(id => current.selectedColumns.has(id));
  const rows = selectRows(visible);
  const lastPage = Math.max(0, Math.ceil(rows.length / pageSize) - 1);
  current.page = Math.min(current.page, lastPage);
  const start = current.page * pageSize;
  const table = byId("census-table");
  tableRows(table, visible.map(id => label(column(id))), rows.slice(start, start + pageSize).map(project =>
    visible.map(id => cellButton(project.id, column(id), cell(project, id))),
  ));
  if (!rows.length) {
    const empty = node("tr");
    const detail = node("td", t("noMatches"));
    detail.colSpan = visible.length;
    empty.append(detail);
    table.tBodies[0].append(empty);
  }
  for (const [index, header] of Array.from(table.tHead.rows[0].cells).entries()) {
    const id = visible[index];
    if (current.sort === id) header.setAttribute("aria-sort", current.ascending ? "ascending" : "descending");
    const button = node("button", header.textContent, "sort");
    button.type = "button";
    button.addEventListener("click", () => {
      current.ascending = current.sort !== id || !current.ascending;
      current.sort = id; current.page = 0; renderTable();
      table.tHead.rows[0].cells[index].querySelector("button").focus();
    });
    header.replaceChildren(button);
  }
  byId("table-count").textContent = `${number(rows.length)} / ${number(projects().length)} · ${number(visible.length)} / 51`;
  byId("page-info").textContent = rows.length ? `${number(start + 1)}–${number(Math.min(start + pageSize, rows.length))} / ${number(rows.length)}` : "0";
  byId("prev").disabled = current.page === 0;
  byId("next").disabled = current.page >= lastPage;
}

function openEvidence(id, col, item) {
  current.project = id;
  current.evidenceTarget = {id, col, item};
  const evidence = current.data.evidence.entries[item.lineage_id];
  byId("evidence-title").textContent = `${name(id)} · ${label(col)}`;
  byId("evidence-detail").replaceChildren(evidenceDetails(evidence, item, t, displayState(item.state)));
  if (!byId("evidence").open) byId("evidence").showModal();
  deepLink();
}

function projectChoices() {
  const choices = projects().map(project => [project.id, name(project.id)]);
  const populated = projects().filter(project => cell(project, keys.ore).value !== null);
  const left = byId("compare-left"), right = byId("compare-right");
  options(left, choices, current.project || left.value || populated[0]?.id);
  options(right, choices, right.value || populated[1]?.id || choices[1]?.[0]);
  const metric = byId("compare-metric");
  options(metric, [["all", t("all")], ["resource", t("resourceMeasures")], ["magnet", t("magnetMeasures")],
    ["oxide", t("oxideMeasures")]], metric.value);
}

function renderComparison() {
  const [left, right] = ["compare-left", "compare-right"].map(key => projects().find(row => row.id === byId(key).value));
  if (!left || !right) return;
  const group = byId("compare-metric").value;
  const measured = current.data.columnIds.filter((id, i) => {
    if (group === "resource") return [keys.ore,keys.grade,keys.contained].includes(id);
    if (group === "magnet") return magnetSpecies.test(id);
    if (group === "oxide") return i >= 35 && i < 50;
    return i >= 15 && i <= 24 || i >= 29 && i < 50 && id !== "總說明";
  });
  const rows = measured.map(id => [label(column(id)), column(id).unit,
    cellButton(left.id, column(id), cell(left, id)), cellButton(right.id, column(id), cell(right, id))]);
  const composition = current.data.census.selected_composition ?? {};
  const leftStock = new Map((composition[left.id] ?? []).map(item => [item.variable, item]));
  const rightStock = new Map((composition[right.id] ?? []).map(item => [item.variable, item]));
  const componentButton = (project, term, item) => item ? cellButton(project.id, {id: term, label: term}, {
    value: item.value, state: "PRESENT", origin: "FORMAL_SELECTION", lineage_id: item.lineage_id,
  }) : t("missing");
  const terms = [...new Set([...leftStock.keys(), ...rightStock.keys()])].sort().filter(term =>
    group === "all" || group === "oxide" || group === "magnet" && magnetSpecies.test(term));
  rows.push(...terms.map(term => [
    `${t("compositionObserved")} · ${term}`, leftStock.get(term)?.unit ?? rightStock.get(term)?.unit,
    componentButton(left, term, leftStock.get(term)), componentButton(right, term, rightStock.get(term)),
  ]));
  tableRows(byId("compare-table"), [t("compareField"), t("unit"), name(left.id), name(right.id)], rows);
}

function updateMap() {
  const filtered = current.data.map.features.filter(point => {
    const project = projects().find(item => item.id === point.properties.project_id);
    if (byId("map-only-resource").checked && cell(project, keys.ore).state !== "PRESENT") return false;
    return Object.entries(mapFilters).every(([select, field]) => {
      const filter = byId(select).value, item = cell(project,field);
      return !filter || filter === (item.state === "UNKNOWN" ? "__unknown" : item.value);
    });
  });
  renderMap(byId("map-canvas"), current.outline, {...current.data.map, features:filtered},
    current.data.coverage, byId("map-details"), name, t, point => {
    const project = projects().find(p => p.id === point);
    openEvidence(point, column("緯度"), cell(project, "緯度"));
  }, current.data.map.features.length);
}

async function loadOutline() {
  try {
    const response = await fetch(new URL("assets/australia.geojson", base));
    if (response.ok) current.outline = await response.json();
  } catch {
    return;
  }
  if (current.data && current.tab === "map") updateMap();
}

function updateDecision() {
  const panel = current.data.mcda.panels.find(item => item.id === byId("panel").value);
  renderDecision(byId("panel-detail"), panel, name, t);
}

function renderVersion() {
  setupVersion(current.data.entry, current.pointer);
  renderMetrics();
  filterOptions();
  renderColumns();
  renderTable();
  projectChoices();
  renderComparison();
  const panel = byId("panel");
  const available = current.data.mcda.panels;
  options(panel, available.map(item => [item.id, panelLabels[item.id]?.[current.language === "en" ? 1 : 0] ?? item.id]),
    panel.value || available.find(item => item.status === "PASS")?.id);
  updateDecision();
  setTab(current.tab);
  if (current.evidenceTarget && byId("evidence").open) {
    const {id, col, item} = current.evidenceTarget;
    openEvidence(id, col, item);
  }
}

async function changeVersion(id, pointer, push = false) {
  if (!pointer) return;
  current.request?.abort();
  const request = new AbortController();
  current.request = request;
  current.message = "loading";
  renderStatus();
  try {
    const result = await readVersion(pointer, id, request.signal);
    if (current.request !== request) return;
    if (!result.census.projects.some(project => project.cells[result.columnIds.indexOf(keys.name)].value !== null)) {
      current.selectedColumns.add(keys.project);
    }
    current.data = result;
    current.pointer = pointer;
    current.page = 0;
    current.message = null;
    current.next = null;
    current.evidenceTarget = null;
    if (byId("evidence").open) byId("evidence").close();
    if (current.csvUrl) URL.revokeObjectURL(current.csvUrl);
    current.csvUrl = URL.createObjectURL(new Blob([result.csv], {type: "text/csv;charset=utf-8"}));
    byId("download").href = current.csvUrl;
    byId("download").download = `census-${id}.csv`;
    byId("workbench").hidden = false;
    renderVersion();
    deepLink(push);
    renderStatus();
  } catch (error) {
    if (current.request !== request) return;
    if (current.data) byId("version").value = current.data.entry.release_id;
    report(error);
  }
}

async function checkVersion(mode = "initial") {
  try {
    const pointer = await readPointer(base, preview);
    current.checked = pointer;
    if (mode !== "initial" && current.data) {
      current.next = pointer.active.manifest_sha256 !== current.data.entry.manifest_sha256 ? pointer.active : null;
      if (mode === "manual") current.message = "checkDone";
      renderStatus();
      return;
    }
    const query = new URLSearchParams(location.search);
    current.tab = tabs.has(query.get("tab")) ? query.get("tab") : "census";
    current.project = query.get("project");
    if (current.project) byId("search").value = current.project;
    await changeVersion(query.get("release") || pointer.active.release_id, pointer);
  } catch (error) {
    report(error);
  }
}

byId("language").addEventListener("click", () => {
  current.language = current.language === "zh" ? "en" : "zh";
  localStorage.setItem("au-ree-language", current.language);
  translate();
});
byId("refresh").addEventListener("click", () => checkVersion("manual"));
byId("version").addEventListener("change", event => changeVersion(event.target.value, current.pointer, true));
byId("tabs").addEventListener("click", event => {
  if (event.target.dataset.tab) setTab(event.target.dataset.tab, true);
});
byId("search").addEventListener("input", () => {
  current.project = null;
  current.page = 0;
  renderTable();
  deepLink();
});
for (const id of [...Object.keys(filters), "data-filter"]) byId(id).addEventListener("change", () => {
  current.page = 0;
  renderTable();
});
byId("prev").addEventListener("click", () => { current.page -= 1; renderTable(); });
byId("next").addEventListener("click", () => { current.page += 1; renderTable(); });
for (const select of ["compare-left", "compare-right", "compare-metric"]) byId(select).addEventListener("change", renderComparison);
for (const id of [...Object.keys(mapFilters), "map-only-resource"]) byId(id).addEventListener("change", updateMap);
byId("map-compare").addEventListener("click", () => setTab("resources", true));
byId("panel").addEventListener("change", updateDecision);
byId("evidence-close").addEventListener("click", () => byId("evidence").close());
window.addEventListener("popstate", () => checkVersion());
const refreshMinutes = Number(new URLSearchParams(location.search).get("refresh") ?? 10);
if (Number.isFinite(refreshMinutes) && refreshMinutes >= 5 && refreshMinutes <= 60) {
  setInterval(() => { if (current.data && !document.hidden) checkVersion("automatic"); }, refreshMinutes * 60000);
}
current.message = "loading";
translate();
void loadOutline();
void checkVersion();
