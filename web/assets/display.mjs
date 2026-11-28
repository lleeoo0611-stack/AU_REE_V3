/** Validate the versioned display-only Census extension before rendering or export. */

export const DISPLAY_JSON = "census_display.json";
export const DISPLAY_CSV = "census_display_extended.csv";
export const DISPLAY_SCHEMA = "web-census-display-v2";
const roles = new Set(["REPORTED_RESOURCE", "COMPLETED_STUDY", "FORECAST", "ENGINEERING_PLAN"]);
const coreResourceRule = "as-reported-resource-review-v1";
const reviewStates = new Set(["CANDIDATE", "SHADOW", "SCIENTIFIC_CONFLICT", "EVIDENCE_INSUFFICIENT"]);
const groups = new Set(["resource", "study", "forecast", "geology", "location"]);
const types = new Set(["TEXT", "PARTIAL_DATE", "REPORTED_QUANTITY"]);
const hex = /^[a-f0-9]{64}$/;
const fieldName = /^[a-z][a-z0-9_]+$/;
const signedNumber = /^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$/;
const record = value => typeof value === "object" && value !== null && !Array.isArray(value);
const text = value => typeof value === "string" && value.trim() !== "" && !/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(value);
const strings = (value, keys) => keys.every(key => text(value[key]));
const keysAre = (value, keys) => record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const spreadsheetSafe = value => !/^[=+\-@]/.test(value.trimStart()) || signedNumber.test(value);
const contextOkay = value => record(value) && Object.keys(value).length > 0
  && Object.entries(value).every(([key, item]) => fieldName.test(key) && text(item));
function calendar(value) {
  if (typeof value !== "string" || !/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(value) || value.startsWith("0000")) return false;
  const day = value + ({4: "-01-01", 7: "-01", 10: ""}[value.length]);
  return Number.isFinite(Date.parse(day)) && new Date(day).toISOString().slice(0, 10) === day;
}

function canonical(value) {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (record(value)) return "{" + Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonical(value[key])).join(",") + "}";
  return JSON.stringify(value);
}

async function digest(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(value)));
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
}

const envelopeFields = ["schema_version", "release_id", "columns", "projects", "facts", "coverage", "review_only", "contract_sha256"];
const definitionFields = ["field_id", "label_zh", "label_en", "group", "data_type", "unit", "basis", "purpose", "source_priority",
  "applicability", "missing_semantics", "validation_rule", "precision", "display_label", "evidence_roles", "version"];
const definitionText = definitionFields.filter(key => key !== "evidence_roles");
const provenanceFields = ["project_id", "data_role", "source_sha256", "source_capture_sha256", "source_url", "publication_date",
  "locator", "quote", "scope", "context"];
const factFields = [...provenanceFields, "field_id", "value", "raw_token", "raw_unit", "subject_id"];
const reviewFields = [...provenanceFields, "state", "label", "reason"];

function sourceOkay(entry, ids, sourceURL, review = false, cutoff) {
  const context = entry.context;
  const undatedGeography = context?.capture_date?.length === 10 && calendar(context.capture_date)
    && context.feature_effective_date === "UNKNOWN_CONTEXT";
  const undatedResource = entry.data_role === "REPORTED_RESOURCE" && context?.display_rule === coreResourceRule
    && context.publication_date_status === "UNKNOWN_CONTEXT" && context.first_proven_public_date?.length === 10
    && calendar(context.first_proven_public_date) && calendar(cutoff) && context.first_proven_public_date <= cutoff;
  const undated = review && entry.publication_date === "" && (undatedGeography || undatedResource);
  return ids.has(entry.project_id) && hex.test(entry.source_sha256) && hex.test(entry.source_capture_sha256)
    && (calendar(entry.publication_date) || undated)
    && strings(entry, ["locator", "quote", "scope", "data_role"]) && !!sourceURL(entry.source_url)
    && contextOkay(entry.context);
}

function columnDefinitions(columns, census, check) {
  check(Array.isArray(columns) && columns.length > 0);
  const byField = new Map();
  const core = new Set(census.columns.map(item => item.id));
  for (const definition of columns) {
    check(keysAre(definition, definitionFields) && fieldName.test(definition.field_id) && !byField.has(definition.field_id)
      && !core.has(definition.field_id) && !core.has(definition.label_zh)
      && strings(definition, definitionText) && groups.has(definition.group) && types.has(definition.data_type)
      && definition.purpose === "CENSUS_DISPLAY_ONLY" && definition.display_label === "SOURCE_REPORTED_DISPLAY"
      && definition.version === "v2"
      && Array.isArray(definition.evidence_roles) && definition.evidence_roles.length > 0
      && definition.evidence_roles.every(role => roles.has(role))
      && new Set(definition.evidence_roles).size === definition.evidence_roles.length);
    byField.set(definition.field_id, definition);
  }
  return byField;
}

function checkFacts(facts, byField, ids, sourceURL, check) {
  check(record(facts));
  for (const [id, fact] of Object.entries(facts)) {
    const field = record(fact) ? byField.get(fact.field_id) : null;
    check(hex.test(id) && keysAre(fact, factFields) && !!field
      && strings(fact, ["value", "raw_token", "subject_id"]) && spreadsheetSafe(fact.value)
      && (fact.raw_unit === "" || text(fact.raw_unit)) && field.evidence_roles.includes(fact.data_role)
      && sourceOkay(fact, ids, sourceURL) && fact.context.as_reported_at === fact.publication_date && text(fact.context.source_kind)
      && (hex.test(fact.context.temporal_decision_id) || fact.context.temporal_status === "ELIGIBLE")
      && (fact.context.temporal_status ?? "ELIGIBLE") === "ELIGIBLE"
      && (field.data_type !== "PARTIAL_DATE" || calendar(fact.value))
      && (field.data_type !== "REPORTED_QUANTITY" || (text(fact.raw_unit) && signedNumber.test(fact.raw_token))));
  }
}

function csvMatches(bytes, expected) {
  // Parse quoted fields without normalizing exact display text. A newline in a
  // field, doubled quotes and both LF/CRLF row endings are supported.
  const input = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
  const token = /(?:"((?:[^"]|"")*)"|([^",\r\n]*))(,|\r\n|\n|$)/gy;
  let row = 0, column = 0, start = 0;
  while (start < input.length || column > 0) {
    const match = token.exec(input);
    if (!match || (match[3] === "" && token.lastIndex !== input.length)) return false;
    const value = match[1] === undefined ? match[2] : match[1].replaceAll('""', '"');
    if (expected[row]?.[column] !== value) return false;
    start = token.lastIndex;
    if (match[3] === ",") {
      column += 1;
    } else {
      if (column + 1 !== expected[row].length) return false;
      row += 1;
      column = 0;
    }
  }
  return row === expected.length && column === 0;
}

function checkCells(data, census, byField, check) {
  check(Array.isArray(data.projects) && data.projects.length === census.projects.length);
  const used = new Set();
  const measured = new Map([...byField.keys()].map(field => [field, {present: 0, unknown: 0, not_applicable: 0}]));
  const projects = new Map();
  const csv = [["專案ID", ...byField.keys()]];
  const fields = [...byField.keys()];
  data.projects.forEach((project, index) => {
    check(keysAre(project, ["id", "cells"]) && project.id === census.projects[index].id
      && Array.isArray(project.cells) && project.cells.length === byField.size);
    const values = project.cells.map((cell, position) => {
      const field = fields[position];
      check(keysAre(cell, ["state", "value", "fact_ids"]) && Array.isArray(cell.fact_ids));
      if (cell.state === "UNKNOWN") {
        check(cell.value === null && cell.fact_ids.length === 0);
        measured.get(field).unknown += 1;
        return "";
      }
      check(cell.state === "PRESENT" && text(cell.value) && spreadsheetSafe(cell.value) && cell.fact_ids.length > 0);
      const exact = cell.fact_ids.map(id => {
        const fact = data.facts[id];
        check(typeof id === "string" && hex.test(id) && !!fact && !used.has(id)
          && fact.project_id === project.id && fact.field_id === field);
        used.add(id);
        return fact.value;
      });
      check(cell.value === exact.join(" | "));
      measured.get(field).present += 1;
      return cell.value;
    });
    projects.set(project.id, project.cells);
    csv.push([project.id, ...values]);
  });
  check(used.size === Object.keys(data.facts).length && record(data.coverage) && Object.keys(data.coverage).length === byField.size);
  for (const [field, counts] of measured) {
    check(keysAre(data.coverage[field], Object.keys(counts)) && Object.entries(counts).every(([key, count]) => data.coverage[field][key] === count));
  }
  return {projects, csv};
}

export async function readExtendedDisplay(data, csv, census, {check, sourceURL, cutoff}) {
  check(keysAre(data, envelopeFields) && hex.test(data.contract_sha256));
  const byField = columnDefinitions(data.columns, census, check);
  check(await digest({schema_version: "census-display-columns-v2", columns: data.columns}) === data.contract_sha256);
  const ids = new Set(census.projects.map(project => project.id));
  checkFacts(data.facts, byField, ids, sourceURL, check);
  await Promise.all(Object.entries(data.facts).map(async ([id, fact]) => check(await digest(fact) === id)));
  const rows = checkCells(data, census, byField, check);
  check(csvMatches(csv, rows.csv));
  check(Array.isArray(data.review_only));
  for (const entry of data.review_only) {
    check(keysAre(entry, reviewFields) && reviewStates.has(entry.state) && strings(entry, ["label", "reason"])
      && sourceOkay(entry, ids, sourceURL, true, cutoff));
  }
  const columns = data.columns.map(definition => ({
    id: `display:${definition.field_id}`, label: definition.label_zh, label_en: definition.label_en,
    display_group: definition.group.toLowerCase(), data_type: definition.data_type, unit: definition.unit, definition,
  }));
  return {columns, byColumn: new Map(columns.map((column, index) => [column.id, index])), projects: rows.projects,
    facts: data.facts, review: data.review_only, coverage: data.coverage, contractSha: data.contract_sha256, csv};
}

export function reportedQuantity(cell, facts) {
  if (cell.fact_ids.length !== 1) return null;
  const fact = facts[cell.fact_ids[0]];
  const amount = Number(fact.raw_token.replaceAll(",", ""));
  return Number.isFinite(amount) ? {unit: fact.raw_unit, amount} : null;
}

const coreResourceColumns = new Set(["ore_mt", "treo_grade_pct", "contained_treo_kt"]);
const annualRateUnits = new Set(["tonnes per annum", "tpa"]);
const subjectKey = fact => [fact.project_id, fact.source_sha256, fact.subject_id].join(":");
const cellKey = (project, column) => `${project}:${column}`;

function annualRateColumn(fact) {
  if (fact.data_role !== "FORECAST" || !annualRateUnits.has(fact.raw_unit)) return null;
  const {material_basis: material, chemical_basis: chemistry} = fact.context;
  if (material === "MREC" && chemistry === "TREO") return "forecast_treo_t_per_year";
  if (material === "NDPR_OXIDE" && chemistry === "NDPR_OXIDE") return "forecast_ndpr_t_per_year";
  return null;
}

/** Compose Census-only previews from display facts already checked by readExtendedDisplay. */
export function reportedCoreCells(display) {
  const cells = new Map();
  if (!display) return cells;
  const facts = Object.values(display.facts);
  const periods = new Map(facts.filter(fact => fact.field_id === "forecast_scenario_or_period")
    .map(fact => [subjectKey(fact), fact.value]));
  const add = (column, record, amount, period = null) => {
    const key = cellKey(record.project_id, column);
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push({record, amount, period});
  };
  for (const fact of facts) {
    if (fact.field_id === "forecast_product_and_basis") {
      add("product_output", fact, null);
    } else if (fact.field_id === "forecast_annual_rate_as_reported") {
      const column = annualRateColumn(fact);
      if (column) add(column, fact, fact.raw_token, periods.get(subjectKey(fact)));
    }
  }
  for (const fact of display.review) {
    const {census_column_id: column, display_value: value, raw_unit: rawUnit,
      raw_token: rawToken, raw_value: rawValue} = fact.context;
    const token = rawToken ?? rawValue;
    if (fact.data_role === "REPORTED_RESOURCE" && coreResourceColumns.has(column)
        && fact.context.display_rule === coreResourceRule && rawUnit && signedNumber.test(token)
        && value === `${token} ${rawUnit}`) {
      add(column, {...fact, value, raw_unit: rawUnit, raw_token: token}, null);
    }
  }
  for (const [key, claims] of cells) {
    const values = new Set(claims.map(({record, period}) => claims.length > 1 && period
      ? `${record.value} (${period})` : record.value));
    const amount = claims.length === 1 && claims[0].amount;
    cells.set(key, {
      state: "PRESENT", value: [...values].join(" | "),
      reported: claims.map(({record}) => record),
      sortAmount: amount && signedNumber.test(amount) ? Number(amount.replaceAll(",", "")) : null,
    });
  }
  return cells;
}

export function reportedCoreCell(cells, project, column, archived) {
  if (archived?.state !== "UNKNOWN") return archived;
  return cells.get(cellKey(project, column)) ?? archived;
}
