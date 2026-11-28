import {readPointer, readVersion, ReleaseError} from "./release.mjs";
import {reportedCoreCell, reportedCoreCells, reportedQuantity} from "./display.mjs";
import {en, zh, columnLabels, panelLabels} from "./labels.mjs";
import {displayEvidence, evidenceDetails, node, options, renderDecision, renderMap, tableRows} from "./views.mjs";

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
  showDisplay: false, displayCsvUrl: null, reportedCensus: new Map(),
};
const t = key => (current.language === "en" ? en : zh)[key] ?? key;
const number = value => new Intl.NumberFormat(current.language === "en" ? "en-AU" : "zh-TW").format(value);
const label = col => current.language === "en"
  ? (col.label_en ?? columnLabels[col.id] ?? (col.id.includes("_") ? col.id.split("_")[0] + (col.unit ? ` (${col.unit})` : "") : col.label ?? col.id))
  : col.label ?? col.id;

const projects = () => current.data.census.projects;
const displayIndex = id => current.data.display?.byColumn.get(id);
const column = id => current.data.display?.columns[displayIndex(id)] ?? current.data.census.columns[current.data.columnIds.indexOf(id)];
const cell = (project, id) => current.data.display?.projects.get(project?.id)?.[displayIndex(id)] ?? project?.cells[current.data.columnIds.indexOf(id)];
const explorerCell = (project, id) => reportedCoreCell(current.reportedCensus, project.id, column(id).column_id, cell(project, id));
const explorerColumns = () => current.showDisplay && current.data.display
  ? [...current.data.columnIds, ...current.data.display.columns.map(item => item.id)] : current.data.columnIds;
const name = id => cell(projects().find(p => p.id === id), keys.name)?.value || id;
const displayState = state => t(state === "UNKNOWN" ? "missing" : "na");

// The extension controls belong to the existing Census pane. They stay absent
// from historical versions that only carry the frozen 51 columns.
const displayToggle = node("input");
displayToggle.type = "checkbox";
displayToggle.id = "show-display";
const displayToggleLabel = node("label", null, "display-toggle");
const newLabel = key => { const text = node("span"); text.dataset.i = key; return text; };
displayToggleLabel.append(displayToggle, newLabel("showDisplay"));
const displayDownload = node("a", null, "button secondary");
displayDownload.id = "download-display";
displayDownload.dataset.i = "downloadDisplay";
const displayToolbar = node("div", null, "display-toolbar");
const displayCoverage = node("span", null, "mini");
displayCoverage.id = "display-coverage";
displayToolbar.append(displayToggleLabel, displayDownload, displayCoverage);
displayToolbar.hidden = true;
byId("census-filters").before(displayToolbar);

const displayFilters = node("div", null, "filters display-filter-row");
function addDisplayFilter(key, control, id) {
  const field = node("div", null, "field");
  const name = node("label");
  name.htmlFor = id;
  name.dataset.i = key;
  control.id = id;
  field.append(name, control);
  displayFilters.append(field);
  return control;
}
const displayField = addDisplayFilter("displayField", node("select"), "display-field");
const displayQuery = addDisplayFilter("displayQuery", node("input"), "display-query");
displayQuery.type = "search";
const displayStateFilter = addDisplayFilter("valueState", node("select"), "display-state");
displayFilters.hidden = true;
byId("census-filters").after(displayFilters);

const reviewPanel = node("details", null, "display-review");
reviewPanel.id = "display-review";
const reviewTitle = node("summary");
reviewTitle.dataset.i = "reviewOnly";
const reviewItems = node("ul", null, "display-review-items");
reviewPanel.append(reviewTitle, reviewItems);
reviewPanel.hidden = true;
byId("census").append(reviewPanel);

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
  const reported = current.reportedCensus.size ? [["REPORTED_PREVIEW", t("reportedPreviewBadge")]] : [];
  options(dataFilter, [["", t("all")], ["UNKNOWN", t("missing")], ["NOT_APPLICABLE", t("na")],
    ["PRESENT", t(reported.length ? "displayedValues" : "present")], ...reported], dataFilter.value);
}

function renderDisplaySettings() {
  const display = current.data.display;
  displayToolbar.hidden = !display;
  displayToggle.checked = current.showDisplay;
  displayFilters.hidden = displayDownload.hidden = displayCoverage.hidden = !display || !current.showDisplay;
  if (!display) return;
  const populated = Object.values(display.coverage).reduce((count, column) => count + column.present, 0);
  displayCoverage.textContent = `${t("displayCoverage")} ${number(populated)} / ${number(projects().length * display.columns.length)}`;
  options(displayField, [["", t("allDisplayFields")], ...display.columns.map(item => [item.id, label(item)])], displayField.value);
  options(displayStateFilter, [["", t("all")], ["PRESENT", t("present")], ["UNKNOWN", t("missing")]], displayStateFilter.value);
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
  const available = explorerColumns();
  box.replaceChildren(create(current.showDisplay && current.data.display ? "displayAllExtended" : "displayAll", available),
    create("defaultColumns", defaults));
  const groups = new Map();
  for (const col of available.map(column)) {
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
        if (!check.checked && available.filter(id => current.selectedColumns.has(id)).length === 1) {
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

function matchesDisplay(project) {
  if (!current.showDisplay || !current.data.display) return true;
  const q = displayQuery.value.trim().toLocaleLowerCase();
  const expected = displayStateFilter.value;
  if (!q && !expected) return true;
  const columns = displayField.value ? [displayField.value] : current.data.display.columns.map(item => item.id);
  return columns.some(id => {
    const item = cell(project, id);
    return (!expected || item.state === expected) && (!q || item.value?.toLocaleLowerCase().includes(q));
  });
}

function selectRows(visible) {
  const q = byId("search").value.trim().toLocaleLowerCase();
  const state = byId("data-filter").value;
  const core = visible.filter(id => displayIndex(id) === undefined);
  const filtered = projects().filter(project => {
    if (q && ![keys.project, keys.name, keys.developer, "ASX代碼"].some(id =>
      explorerCell(project, id).value?.toLocaleLowerCase().includes(q))) return false;
    for (const [input, id] of Object.entries(filters)) {
      const filter = byId(input).value;
      const item = explorerCell(project, id);
      const actual = item.state === "UNKNOWN" ? "__unknown" : item.value;
      if (filter && filter !== actual) return false;
    }
    return matchesDisplay(project) && (!state || (core.length ? core : current.data.columnIds).some(id => {
      const item = explorerCell(project, id);
      return state === "REPORTED_PREVIEW" ? !!item.reported : item.state === state;
    }));
  });
  if (!current.sort) return filtered;
  const collator = new Intl.Collator(current.language === "en" ? "en" : "zh-Hant", {numeric: true, sensitivity: "base"});
  const sortColumn = column(current.sort);
  const display = current.data.display;
  const quantityType = sortColumn?.definition?.data_type === "REPORTED_QUANTITY";
  const numericType = !sortColumn?.definition && (["number", "numeric", "decimal", "integer"].includes(sortColumn?.data_type?.toLowerCase())
    || [keys.ore, keys.grade, keys.contained].includes(current.sort));
  return filtered.sort((left, right) => {
    const l = explorerCell(left, current.sort), r = explorerCell(right, current.sort);
    const a = l.value, b = r.value;
    if (a === null || b === null) return a !== b ? (a === null ? 1 : -1) : collator.compare(left.id, right.id);
    let order = collator.compare(a, b);
    if (quantityType) {
      const x = reportedQuantity(l, display.facts), y = reportedQuantity(r, display.facts);
      if (x && y) order = collator.compare(x.unit, y.unit) || x.amount - y.amount;
      else if (Boolean(x) !== Boolean(y)) order = x ? -1 : 1;
    } else if (numericType) {
      const x = l.reported ? l.sortAmount : Number(a);
      const y = r.reported ? r.sortAmount : Number(b);
      if (Number.isFinite(x) && Number.isFinite(y)) order = x - y;
      else if (Number.isFinite(x) !== Number.isFinite(y)) return Number.isFinite(x) ? -1 : 1;
      else if (l.reported || r.reported) return collator.compare(left.id, right.id);
    }
    return (current.ascending ? order : -order) || collator.compare(left.id, right.id);
  });
}

function cellButton(projectId, col, item) {
  const trigger = node("button", item.value ?? displayState(item.state), item.state === "PRESENT" ? "cell-button" : "cell-button missing");
  trigger.type = "button";
  const display = col.definition && item.state === "PRESENT";
  const hint = item.reported ? t("reportedPreviewBadge") : (display ? t("SOURCE_REPORTED_DISPLAY") : "");
  trigger.setAttribute("aria-label", [label(col), item.value ?? displayState(item.state), hint].filter(Boolean).join(" · "));
  if (hint) trigger.title = hint;
  if (item.reported) trigger.append(node("span", hint, "reported-census-badge"));
  trigger.addEventListener("click", () => openEvidence(projectId, col, item));
  return trigger;
}

function renderTable() {
  const visible = explorerColumns().filter(id => current.selectedColumns.has(id));
  const rows = selectRows(visible);
  const lastPage = Math.max(0, Math.ceil(rows.length / pageSize) - 1);
  current.page = Math.min(current.page, lastPage);
  const start = current.page * pageSize;
  const table = byId("census-table");
  tableRows(table, visible.map(id => label(column(id))), rows.slice(start, start + pageSize).map(project =>
    visible.map(id => cellButton(project.id, column(id), explorerCell(project, id))),
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
    if (column(id).definition?.data_type === "REPORTED_QUANTITY") button.title = t("sortReportedQuantity");
    button.addEventListener("click", () => {
      current.ascending = current.sort !== id || !current.ascending;
      current.sort = id; current.page = 0; renderTable();
      table.tHead.rows[0].cells[index].querySelector("button").focus();
    });
    header.replaceChildren(button);
  }
  const extended = visible.filter(id => displayIndex(id) !== undefined).length;
  const core = visible.length - extended;
  const total = `${number(rows.length)} / ${number(projects().length)} · ${number(core)} / 51`;
  byId("table-count").textContent = current.showDisplay && current.data.display
    ? `${total} · ${t("displayFields")} ${number(extended)} / ${number(current.data.display.columns.length)}` : total;
  byId("page-info").textContent = rows.length ? `${number(start + 1)}–${number(Math.min(start + pageSize, rows.length))} / ${number(rows.length)}` : "0";
  byId("prev").disabled = current.page === 0;
  byId("next").disabled = current.page >= lastPage;
  renderReview(rows);
}

function renderReview(rows) {
  const display = current.data.display;
  const ids = new Set(rows.map(project => project.id));
  const matching = current.showDisplay && display ? display.review.filter(entry => ids.has(entry.project_id)) : [];
  reviewPanel.hidden = !matching.length;
  reviewItems.replaceChildren(...matching.map(entry => {
    const listItem = node("li");
    const action = node("button", `${name(entry.project_id)} · ${entry.label} · ${t(entry.state)}`, "cell-button");
    action.type = "button";
    action.addEventListener("click", () => openEvidence(entry.project_id, {id: entry.label}, {state: entry.state, value: null}, entry));
    listItem.append(action);
    return listItem;
  }));
}

function openEvidence(id, col, item, review = null) {
  current.project = id;
  current.evidenceTarget = {id, col, item, review};
  byId("evidence-title").textContent = `${name(id)} · ${label(col)}`;
  const display = current.data.display;
  const status = review ? t(item.state) : displayState(item.state);
  const target = byId("evidence-detail");
  if (item.reported) {
    const archived = cell(projects().find(project => project.id === id), col.id);
    target.replaceChildren(
      displayEvidence(item.reported, {...item, origin: t("reportedPreviewBadge")}, col, t, t("reportedPreviewBadge"), display.contractSha),
      node("h3", t("archivedCensusCell")),
      evidenceDetails(current.data.evidence.entries[archived.lineage_id], archived, t, displayState(archived.state)),
    );
  } else {
    const details = col.definition || review
      ? displayEvidence(review ? [review] : item.fact_ids.map(key => display.facts[key]), item, col, t, status, display.contractSha)
      : evidenceDetails(current.data.evidence.entries[item.lineage_id], item, t, status);
    target.replaceChildren(details);
  }
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
  renderDisplaySettings();
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
    const {id, col, item, review} = current.evidenceTarget;
    openEvidence(id, col, item, review);
  }
}

function setDisplayVersion(result, id) {
  const previouslyOffered = current.data?.display?.byColumn;
  const next = result.display;
  current.selectedColumns = new Set([...current.selectedColumns].filter(key => result.columnIds.includes(key) || next?.byColumn.has(key)));
  for (const col of next?.columns ?? []) {
    if (!previouslyOffered?.has(col.id)) current.selectedColumns.add(col.id);
  }
  if (current.sort && !result.columnIds.includes(current.sort) && !next?.byColumn.has(current.sort)) current.sort = null;
  if (!next) {
    current.showDisplay = false;
    displayField.replaceChildren();
    displayQuery.value = displayStateFilter.value = "";
    reviewPanel.open = false;
  }
  if (!(current.showDisplay && next) && !result.columnIds.some(key => current.selectedColumns.has(key))) {
    current.selectedColumns.add(keys.project);
  }
  if (current.displayCsvUrl) URL.revokeObjectURL(current.displayCsvUrl);
  current.displayCsvUrl = next ? URL.createObjectURL(new Blob([next.csv], {type: "text/csv;charset=utf-8"})) : null;
  if (next) {
    displayDownload.href = current.displayCsvUrl;
    displayDownload.download = `census-display-extended-${id}.csv`;
  } else {
    displayDownload.removeAttribute("href");
    displayDownload.removeAttribute("download");
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
    setDisplayVersion(result, id);
    if (!result.census.projects.some(project => project.cells[result.columnIds.indexOf(keys.name)].value !== null)) {
      current.selectedColumns.add(keys.project);
    }
    current.data = result;
    current.reportedCensus = reportedCoreCells(preview ? result.display : null);
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
displayToggle.addEventListener("change", () => {
  current.showDisplay = displayToggle.checked;
  if (current.showDisplay && !current.data.display.columns.some(col => current.selectedColumns.has(col.id))) {
    for (const col of current.data.display.columns) current.selectedColumns.add(col.id);
  }
  if (!current.showDisplay && displayIndex(current.sort) !== undefined) current.sort = null;
  if (!explorerColumns().some(key => current.selectedColumns.has(key))) current.selectedColumns.add(keys.project);
  current.page = 0;
  renderDisplaySettings(); renderColumns(); renderTable();
});
for (const [control, event] of [[displayField, "change"], [displayQuery, "input"], [displayStateFilter, "change"]]) {
  control.addEventListener(event, () => { current.page = 0; renderTable(); });
}
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
