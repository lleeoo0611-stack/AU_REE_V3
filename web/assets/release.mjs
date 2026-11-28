import {DISPLAY_CSV, DISPLAY_JSON, DISPLAY_SCHEMA, readExtendedDisplay} from "./display.mjs";

const shaPattern = /^[a-f0-9]{64}$/;
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const artifacts = {
  "census.json": "web-census-v1",
  "census_evidence.json": "web-evidence-v1",
  "coverage.json": "web-coverage-v1",
  "mcda.json": "web-mcda-v1",
  "gis.geojson": "web-gis-v1",
  "census.csv": null,
};

export class ReleaseError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function check(condition, reason = "badRelease") {
  if (!condition) throw new ReleaseError(reason);
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function decode(raw) {
  try {
    return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(raw));
  } catch {
    throw new ReleaseError("badRelease");
  }
}

async function request(url, cache, signal) {
  let response;
  try {
    response = await fetch(url, {cache, signal, redirect: "error", credentials: "same-origin"});
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ReleaseError("networkError");
  }
  return response;
}

async function verified(url, expected, signal) {
  check(shaPattern.test(expected));
  const response = await request(url, "default", signal);
  check(response.ok, "missingArtifact");
  const bytes = await response.arrayBuffer();
  check(globalThis.crypto?.subtle, "secureOrigin");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  check(hex === expected, "digestMismatch");
  return bytes;
}

function versionEntry(entry, preview) {
  const status = preview ? "RESEARCH_CANDIDATE" : "FORMAL_CURRENT";
  check(isRecord(entry) && shaPattern.test(entry.release_id) && datePattern.test(entry.cutoff));
  check(entry.release_status === status || (!preview && entry.release_status === "FORMAL_PUBLISHED"));
  check(entry.manifest_path === `releases/${entry.release_id}/manifest.json`);
  check(shaPattern.test(entry.manifest_sha256));
  return entry;
}

export async function readPointer(base, preview, signal) {
  const pointerURL = new URL(preview ? "data/preview.json" : "data/latest.json", base);
  const response = await request(pointerURL, "no-cache", signal);
  if (response.status === 404) throw new ReleaseError(preview ? "missingPreview" : "noFormal");
  check(response.ok, "networkError");
  const pointer = decode(await response.arrayBuffer());
  check(isRecord(pointer) && pointer.schema_version === "web-release-pointer-v1");
  const active = versionEntry(pointer, preview);
  const entries = new Map([[active.release_id, active]]);
  if (Object.hasOwn(pointer, "releases")) {
    check(Array.isArray(pointer.releases));
    for (const item of pointer.releases) {
      const version = versionEntry(item, preview);
      const earlier = entries.get(version.release_id);
      check(!earlier || earlier.manifest_sha256 === version.manifest_sha256);
      if (!earlier) entries.set(version.release_id, version);
    }
  }
  return {active, entries, pointerURL};
}

function readCensus(census, evidence) {
  check(Array.isArray(census.columns) && census.columns.length === 51);
  check(Array.isArray(census.projects) && census.projects.length === 93 && isRecord(evidence.entries));
  const columnIds = census.columns.map(col => col.id);
  check(columnIds.every(id => typeof id === "string" && id.length > 0) && new Set(columnIds).size === 51);
  check(columnIds[0] === "專案ID");
  const projectIds = new Set(), states = {PRESENT:0, UNKNOWN:0, NOT_APPLICABLE:0};
  for (const project of census.projects) {
    check(typeof project.id === "string" && project.id && !projectIds.has(project.id));
    projectIds.add(project.id);
    check(Array.isArray(project.cells) && project.cells.length === 51 && project.cells[0].value === project.id);
    for (const [index, cell] of project.cells.entries()) {
      check(Object.hasOwn(states, cell.state));
      states[cell.state] += 1;
      check(cell.state === "PRESENT" ? typeof cell.value === "string" : cell.value === null);
      const link = evidence.entries[cell.lineage_id];
      check(isRecord(link) && link.project_id === project.id && link.column_id === columnIds[index]);
    }
  }
  const composition = census.selected_composition ?? {};
  check(isRecord(composition));
  for (const [projectId, selections] of Object.entries(composition)) {
    check(projectIds.has(projectId) && Array.isArray(selections));
    const variables = new Set();
    for (const item of selections) {
      const entry = evidence.entries[item?.lineage_id];
      check(typeof item?.variable === "string" && !variables.has(item.variable) && item.unit === "ppm"
        && typeof item.value === "string" && Number.isFinite(Number(item.value))
        && entry?.project_id === projectId && entry.column_id === item.variable
        && entry.evidence_state === "FORMAL_DIRECT" && shaPattern.test(entry.source_sha256)
        && shaPattern.test(entry.observation_id));
      variables.add(item.variable);
    }
  }
  check(evidence.entries && Object.keys(evidence.entries).length >= 4743);
  return {projectIds, columnIds, states};
}

function checkGeography(gis, census, columnIds, evidence) {
  check(gis.type === "FeatureCollection" && Array.isArray(gis.features));
  const longitudeIndex = columnIds.indexOf("經度"), latitudeIndex = columnIds.indexOf("緯度");
  const checked = new Set();
  const sites = new Map(census.projects.map(project => [project.id, project.cells]));
  const locationRoles = ["VERIFIED_MINE_POINT", "VERIFIED_DEPOSIT_POINT"];
  const shared = ["source_sha256", "geometry_role", "temporal_decision_id", "locator", "scope_id", "crs"];
  for (const feature of gis.features) {
    check(Array.isArray(feature.geometry?.coordinates) && feature.geometry.coordinates.length === 2);
    const [longitude, latitude] = feature.geometry.coordinates;
    const props = feature.properties;
    check(feature.type === "Feature" && feature.geometry.type === "Point"
      && typeof longitude === "number" && typeof latitude === "number"
      && longitude >= -180 && longitude <= 180 && latitude >= -90 && latitude <= 90
      && isRecord(props) && sites.has(props.project_id) && feature.id === props.project_id
      && locationRoles.includes(props.geometry_role) && !checked.has(props.project_id));
    const [lon, lat] = [longitudeIndex, latitudeIndex].map(index => sites.get(props.project_id)[index]);
    check(lon.state === "PRESENT" && lat.state === "PRESENT"
      && ["FORMAL_DIRECT","FORMAL_SELECTION"].includes(lon.origin) && lon.origin === lat.origin
      && lon.lineage_id === props.longitude_lineage_id && lat.lineage_id === props.latitude_lineage_id
      && Number(lon.value) === longitude && Number(lat.value) === latitude);
    const left = evidence.entries[lon.lineage_id], right = evidence.entries[lat.lineage_id];
    check(left.temporal_status === "ELIGIBLE" && right.temporal_status === "ELIGIBLE"
      && left.geometry_role === props.geometry_role && ["EPSG:4326","OGC:CRS84"].includes(left.crs)
      && shaPattern.test(left.source_sha256) && shaPattern.test(left.temporal_decision_id)
      && shared.every(key => left[key] && left[key] === right[key]));
    checked.add(props.project_id);
  }
  const paired = census.projects.filter(project => project.cells[longitudeIndex]?.state === "PRESENT"
    && project.cells[latitudeIndex]?.state === "PRESENT");
  check(paired.length === checked.size);
}

function checkDecision(mcda, projectIds) {
  check(Array.isArray(mcda.panels));
  const panelIds = new Set();
  for (const panel of mcda.panels) {
    check(isRecord(panel) && typeof panel.id === "string" && !panelIds.has(panel.id));
    panelIds.add(panel.id);
    const {eligible_projects: eligible, eligible_count: count, minimum_cohort: min} = panel;
    check(Number.isInteger(count) && Number.isInteger(min) && min >= 2 && Array.isArray(eligible)
      && count === eligible.length && count === new Set(eligible).size && eligible.every(id => projectIds.has(id)));
    const ranks = panel.point_rankings ?? {}, sensitivity = panel.leave_one_out ?? {};
    check(isRecord(ranks) && isRecord(sensitivity));
    if (count < min) {
      check(panel.status === "INSUFFICIENT_COHORT" && !Object.keys(ranks).length && !Object.keys(sensitivity).length);
      continue;
    }
    const scenarios = Object.values(ranks);
    check(panel.status === "PASS" && scenarios.length > 0);
    for (const methods of scenarios) {
      check(isRecord(methods) && Object.keys(methods).length > 0);
      for (const rows of Object.values(methods)) {
        check(isRecord(rows) && Object.keys(rows).length === count
          && eligible.every(id => Number.isFinite(rows[id]?.score) && Number.isFinite(rows[id]?.rank)));
      }
    }
  }
}

export async function readVersion(pointer, releaseId, signal) {
  const entry = pointer.entries.get(releaseId);
  check(entry, "unknownVersion");
  const manifestURL = new URL(entry.manifest_path, pointer.pointerURL);
  const manifest = decode(await verified(manifestURL, entry.manifest_sha256, signal));
  check(isRecord(manifest) && manifest.schema_version === "web-research-manifest-v1"
    && manifest.release_id === releaseId && manifest.cutoff === entry.cutoff
    && manifest.release_status === entry.release_status && isRecord(manifest.artifacts));
  const hasDisplay = Object.hasOwn(manifest.artifacts, DISPLAY_JSON);
  check(hasDisplay === Object.hasOwn(manifest.artifacts, DISPLAY_CSV));
  check(!hasDisplay || (entry.release_status === "RESEARCH_CANDIDATE" && manifest.authorization_sha256 === null));
  const expected = hasDisplay ? {...artifacts, [DISPLAY_JSON]: DISPLAY_SCHEMA, [DISPLAY_CSV]: null} : artifacts;
  check(Object.keys(manifest.artifacts).length === Object.keys(expected).length);
  const files = await Promise.all(Object.entries(expected).map(async ([name, schema]) => {
    const artifact = manifest.artifacts[name];
    check(isRecord(artifact) && artifact.path === name);
    const bytes = await verified(new URL(artifact.path, manifestURL), artifact.sha256, signal);
    if (schema === null) return [name, bytes];
    const data = decode(bytes);
    check(isRecord(data) && data.release_id === releaseId && data.schema_version === schema);
    return [name, data];
  }));
  const data = Object.fromEntries(files);
  const census = data["census.json"], evidence = data["census_evidence.json"];
  const {projectIds, columnIds, states} = readCensus(census, evidence);
  const map = data["gis.geojson"];
  checkGeography(map, census, columnIds, evidence);
  const coverage = data["coverage.json"], mcda = data["mcda.json"];
  const counts = coverage.cell_states;
  check(coverage.projects === 93 && coverage.columns === 51 && coverage.cells === 4743
    && isRecord(counts) && Object.keys(states).every(key => counts[key] === states[key])
    && counts.PRESENT + counts.UNKNOWN + counts.NOT_APPLICABLE === 4743
  );
  checkDecision(mcda, projectIds);
  const display = hasDisplay ? await readExtendedDisplay(data[DISPLAY_JSON], data[DISPLAY_CSV], census,
    {check, sourceURL: publicSourceURL, cutoff: manifest.cutoff}) : null;
  return {entry, manifest, census, evidence, coverage, mcda, map, columnIds, csv: data["census.csv"], display};
}

export function publicSourceURL(value, page) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    const host = url.hostname;
    const hostIsPublic = /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*$/i.test(host)
      && !/\.(?:local|localhost|internal|example|test|invalid|onion)$/i.test(host);
    if (url.protocol !== "https:" || url.username || url.password || url.port || !hostIsPublic) return null;
    if (Number.isInteger(page) && page > 0) url.hash = `page=${page}`;
    return url.href;
  } catch {
    return null;
  }
}
