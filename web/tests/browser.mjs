import { strict as assert } from "node:assert";
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir } from "node:fs/promises";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const site = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = dirname(site);
const origin = "https://research.test";
const root = "/research-repo/";
const url = origin + root;
const flags = process.argv.slice(2);
const synthetic = flags.includes("--synthetic");
const data = flags.find(arg => !arg.startsWith("--"));
const capture = process.env.AU_REE_BROWSER_SCREENSHOTS;
const executablePath = process.env.AU_REE_CHROMIUM;
const dependency = process.env.AU_REE_PLAYWRIGHT ? await import(process.env.AU_REE_PLAYWRIGHT) : await import("playwright");
const mimetypes = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".geojson": "application/geo+json", ".csv": "text/csv",
};
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const buffer = value => Buffer.from(JSON.stringify(value));
const served = new Map();
const coverageExpected = new Map();
const inputs = new Map();

function envelope(schema_version, release_id, rest) {
  return {schema_version, release_id, ...rest};
}

const displayName = "census_display.json", displayCsvName = "census_display_extended.csv";
const sortedFields = (_, value) => value && typeof value === "object" && !Array.isArray(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value;
const canonicalDigest = value => digest(JSON.stringify(value, sortedFields));

function addMockDisplay(files, releaseId, core) {
  const definition = (field_id, label_zh, label_en, group, data_type, evidence_roles, unit = "as reported") => ({
    field_id, label_zh, label_en, group, data_type, evidence_roles, unit,
    basis: "As reported", purpose: "CENSUS_DISPLAY_ONLY", source_priority: "ISSUER",
    applicability: "Source statement", missing_semantics: "UNKNOWN", validation_rule: "ORIGINAL",
    precision: "AS_REPORTED", display_label: "SOURCE_REPORTED_DISPLAY", version: "v2",
  });
  const columns = [
    definition("resource_estimate_effective_date", "原件估算日期", "Reported estimate date", "resource", "PARTIAL_DATE", ["REPORTED_RESOURCE"]),
    definition("forecast_annual_rate_as_reported", "原報告工程年率", "Reported annual rate", "forecast", "REPORTED_QUANTITY", ["FORECAST"], "tpa"),
  ];
  const source = (project_id, data_role) => ({
    project_id, data_role, source_sha256: "a".repeat(64), source_capture_sha256: "b".repeat(64),
    source_url: "https://example.net/issuer.pdf", publication_date: "2026-11-01", locator: "PDF page 6, Table 1",
    quote: "Reported case from the original\nengineering study.", scope: "Synthetic provenance",
    context: {product: "MREC", as_reported_at: "2026-11-01", source_kind: "ISSUER", temporal_status: "ELIGIBLE"},
  });
  const facts = {}, coverage = {};
  const input = [
    [core[0].id, columns[0].field_id, "2026-09", "REPORTED_RESOURCE", ""],
    [core[0].id, columns[1].field_id, "8,300", "FORECAST", "tpa"],
    [core[1].id, columns[1].field_id, "29,000", "FORECAST", "tpa"],
  ];
  for (const [project, field, value, role, unit] of input) {
    const fact = {...source(project, role), field_id: field, value: unit ? `${value} ${unit}` : value,
      raw_token: value, raw_unit: unit, subject_id: project};
    facts[canonicalDigest(fact)] = fact;
  }
  const projects = core.map(({id}) => ({id, cells: columns.map(column => {
    const claim = Object.entries(facts).find(([, fact]) => fact.project_id === id && fact.field_id === column.field_id);
    return claim ? {state: "PRESENT", value: claim[1].value, fact_ids: [claim[0]]} : {state: "UNKNOWN", value: null, fact_ids: []};
  })}));
  columns.forEach((column, index) => {
    const present = projects.filter(project => project.cells[index].state === "PRESENT").length;
    coverage[column.field_id] = {present, unknown: 93 - present, not_applicable: 0};
  });
  const undated = {...source(core[0].id, "OFFICIAL_REFERENCE"), publication_date: "",
    label: "Unverified geographic reference", state: "EVIDENCE_INSUFFICIENT", reason: "Representative point unproved",
    context: {capture_date: "2026-11-28", feature_effective_date: "UNKNOWN_CONTEXT"}};
  files.set(displayName, buffer(envelope("web-census-display-v2", releaseId, {
    columns, projects, facts, coverage, review_only: [undated],
    contract_sha256: canonicalDigest({schema_version: "census-display-columns-v2", columns}),
  })));
  const csvRows = [["專案ID", ...columns.map(column => column.field_id)], ...projects.map(project => [project.id, ...project.cells.map(cell => cell.value ?? "")])];
  files.set(displayCsvName, Buffer.from(csvRows.map(row => row.map(value => value.includes(",") ? `"${value}"` : value).join(",")).join("\r\n") + "\r\n"));
}

async function mockData() {
  const contract = JSON.parse(await readFile(resolve(repo, "config/research_profiles/census_columns_v1.json")));
  const headings = contract.columns;
  for (const id of ["1".repeat(64), "2".repeat(64)]) {
    const current = id[0] === "2";
    const records = {}, projects = [];
    let present = 0;
    for (let i = 0; i < 93; i++) {
      const project = `AU-TEST-${i}`;
      const facts = new Map([[0, project], [3, `Test project ${i}`], [7, i < 30 ? "Western Australia" : "Victoria"]]);
      if (i === 0) facts.set(15, "150");
      if (current) facts.set(1, "Test developer");
      const cells = headings.map((column_id, index) => {
        const value = facts.get(index) ?? null, lineage_id = `${project}:${index}`;
        const origin = value === null ? "NONE" : index === 15 ? "FORMAL_SELECTION" : "REGISTRY_SNAPSHOT";
        if (value !== null) present++;
        records[lineage_id] = {project_id: project, column_id, reason: "TEST_DATA",
          ...(i === 0 && index === 15 ? { source_url: "https://example.net/issuer.pdf", physical_page: 6,
            source_sha256: "a".repeat(64), raw_value: "150", raw_unit: "Mt", evidence_state:"FORMAL_DIRECT" } : {})};
        return {value, state: value === null ? "UNKNOWN" : "PRESENT", origin, precision: null, lineage_id};
      });
      projects.push({id: project, cells});
    }
    const panel = {
      id: "issuer_resource_composition", status: "PASS", minimum_cohort: 2, eligible_count: 2,
      eligible_projects:["AU-TEST-0","AU-TEST-1"],
      point_rankings: {base:{saw:{"AU-TEST-0":{rank:1,score:.8},"AU-TEST-1":{rank:2,score:.4}}}},
      scenario_weights:{base:{R1:.5,S1:.5}},leave_one_out:{base:[{removed:"R1",projects:{"AU-TEST-0":{rank:2,rank_change:1}}}]},
    };
    const object = {
      "census.json": envelope("web-census-v1", id, {columns:headings.map(x=>({id:x,label:x,display_group:"census"})),projects}),
      "coverage.json": envelope("web-coverage-v1", id, {projects:93,columns:51,cells:4743,cell_states:{PRESENT:present,UNKNOWN:4743-present,NOT_APPLICABLE:0},formal_direct_cells:1,comparable_project_criteria:4}),
      "census_evidence.json": envelope("web-evidence-v1", id, {entries:records}),
      "mcda.json": envelope("web-mcda-v1", id, {panels:[panel,{id:"six_domain",status:"INSUFFICIENT_COHORT",eligible_count:0,eligible_projects:[],minimum_cohort:2}]}),
      "gis.geojson": envelope("web-gis-v1", id, {type:"FeatureCollection",features:[],status:"NO_VERIFIED_POINT"}),
    };
    const files = new Map(Object.entries(object).map(([name,payload])=>[name,buffer(payload)]));
    files.set("census.csv", Buffer.from(headings.join(",")+"\n"+projects.map(project=>project.cells.map(cell=>cell.value??"").join(",")).join("\n")+"\n"));
    if (current) addMockDisplay(files, id, projects);
    inputs.set(id, files);
    coverageExpected.set(id, present);
  }
}

async function actualData() {
  assert(data, "Provide the controlled preview data directory or --synthetic");
  const target = resolve(data, "releases");
  for (const id of await readdir(target)) {
    if (!/^[a-f0-9]{64}$/.test(id)) continue;
    const files = new Map();
    for (const name of await readdir(resolve(target,id))) files.set(name,await readFile(resolve(target,id,name)));
    const manifest = JSON.parse(files.get("manifest.json"));
    if (manifest.release_status !== "RESEARCH_CANDIDATE") continue;
    assert.equal(manifest.release_id,id);
    inputs.set(id, files);
    coverageExpected.set(id, JSON.parse(files.get("coverage.json")).cell_states.PRESENT);
  }
}

function stage() {
  const entries = [];
  for (const [id, files] of inputs) {
    const previous = files.get("manifest.json");
    const manifest = previous ?? buffer(envelope("web-research-manifest-v1",id,{
      cutoff:"2026-11-28", release_status:"RESEARCH_CANDIDATE", authorization_sha256:null,
      artifacts:Object.fromEntries([...files].map(([name,bytes])=>[name,{path:name,sha256:digest(bytes)}])),
    }));
    const record = JSON.parse(manifest);
    const prefix = `data/releases/${id}/`;
    for (const [name, bytes] of files) served.set(prefix + name, bytes);
    served.set(prefix + "manifest.json", manifest);
    entries.push({release_id:id,release_status:record.release_status,cutoff:record.cutoff,
      manifest_path:`releases/${id}/manifest.json`,manifest_sha256:digest(manifest)});
  }
  const hasDisplay = entry => Number(inputs.get(entry.release_id).has(displayName));
  const requested = process.env.AU_REE_CANDIDATE_RELEASE_ID;
  if (requested) assert(entries.some(entry => entry.release_id === requested), "Requested candidate is absent");
  const priority = entry => Number(entry.release_id === requested);
  entries.sort((left,right)=>priority(right) - priority(left)
    || (flags.includes("--stage4") ? hasDisplay(right) - hasDisplay(left) : 0)
    || coverageExpected.get(right.release_id)-coverageExpected.get(left.release_id));
  return entries;
}

if (synthetic) await mockData();
else await actualData();
let entries=stage();
assert(entries.length>=2,"Two explicitly identified data versions are required");
const [lead, prior] = entries;
const leadId=lead.release_id, priorId=prior.release_id;
const extended = inputs.get(leadId).has(displayName);
if (flags.includes("--stage4")) assert(extended, "The newer candidate must include the exact Stage 4 display artifacts");
const priorHasDisplay = inputs.get(priorId).has(displayName);
if (flags.includes("--stage4")) assert(!priorHasDisplay, "The Stage 3 version must retain its core-only layout");
const pointerKey = "data/preview.json";
const writePointer = (active = lead, history = entries) => served.set(pointerKey, buffer({
  schema_version:"web-release-pointer-v1",...active,previous_release_id:priorId,releases:history,
}));
writePointer();
const art = (id,file) => `data/releases/${id}/${file}`;
const oldCsv = served.get(art(priorId,"census.csv"));
if (flags.includes("--stage5")) {
  assert(extended && priorHasDisplay && process.env.AU_REE_CANDIDATE_RELEASE_ID === leadId);
  assert.equal(digest(served.get(art(leadId,"census.csv"))), digest(oldCsv));
  const [current, previous] = [leadId, priorId].map(id => JSON.parse(served.get(art(id, displayName))));
  const dateColumn = doc => doc.columns.findIndex(col => col.field_id === "resource_estimate_effective_date");
  const weldDate = doc => doc.projects.find(row => row.id === "AU-efff91a06cf344122d88").cells[dateColumn(doc)];
  assert.equal(weldDate(previous).state, "UNKNOWN");
  assert.equal(weldDate(current).value, "2024-06-30");
  const link = current.facts[weldDate(current).fact_ids[0]];
  assert.equal(link.context.effective_date_role, "SAME_EDITION_LINKED_ESTIMATE_DATE");
  assert.match(link.context.date_link_statement_locator, /^PDF page 2/);
}

const newCensus = JSON.parse(served.get(art(leadId,"census.json")));
const nolansId = "AU-1345edd8165c10930e94";
const nolansSource = "5f72a08840640987e8d619c6d55ca047a2fe26c0194ffd1a46521f76eda7b06f";
const nolansInventoryField = "總TREO蘊藏量_kt";
if (flags.includes("--stage6")) {
  assert(extended && priorHasDisplay && process.env.AU_REE_CANDIDATE_RELEASE_ID === leadId);
  const archive = JSON.parse(served.get(art(priorId,"census.json")));
  const current = newCensus.projects.find(project => project.id === nolansId);
  const old = archive.projects.find(project => project.id === nolansId);
  const evidence = JSON.parse(served.get(art(leadId,"census_evidence.json"))).entries;
  for (const [name, value] of [["礦石蘊藏量_Mt",56],["TREO品位_pct",2.6],[nolansInventoryField,1456]]) {
    const i = newCensus.columns.findIndex(column => column.id === name);
    assert.equal(old.cells[i].value, null);
    assert.equal(Number(current.cells[i].value), value);
    const lineage = evidence[current.cells[i].lineage_id];
    assert.equal(lineage.source_sha256, nolansSource);
    if (name === nolansInventoryField) {
      assert.equal(current.cells[i].origin, "FORMAL_DERIVED");
      assert.equal(lineage.input_observation_ids.length, 2);
    }
  }
}
const populated = newCensus.projects.find(p=>p.cells[15].value !== null);
assert(populated);
const observationValue = populated.cells[15].value;
const exactEvidencePath = art(leadId,"census_evidence.json");
const errors = [];
const metrics = {};
const browser = await dependency.chromium.launch({executablePath, headless:true});
let fault = null;

async function instrument(page) {
  page.on("pageerror", e=>errors.push(String(e)));
  await page.route(origin+"/**", async route=>{
    const request = new URL(route.request().url());
    if (!request.pathname.startsWith(root)) return route.fulfill({status:404,body:"missing"});
    const path = request.pathname.slice(root.length) || "index.html";
    if (fault?.matches(path)) return route.fulfill(fault.response);
    const cached = served.get(path);
    const file = resolve(site,path);
    if (!cached && !(file.startsWith(resolve(site,"assets")+"/") || file===resolve(site,"index.html"))) {
      return route.fulfill({status:404,body:"missing"});
    }
    try {
      return route.fulfill({body:cached??await readFile(file),contentType:mimetypes[extname(path)]??"text/plain"});
    } catch { return route.fulfill({status:404,body:"missing"}); }
  });
}

const expectText = (page,id,expr)=>page.waitForFunction(([target,re])=>new RegExp(re).test(document.querySelector(target).textContent),[id,expr.source]);
async function version(page,id) {
  await page.selectOption("#version",id);
  await page.waitForFunction(value=>document.querySelector("#footer-release").textContent===value,id);
}

async function checkDisplay(page) {
  const json = JSON.parse(served.get(art(leadId, displayName)));
  const facts = Object.values(json.facts);
  const fact = facts.find(item => item.data_role === "FORECAST" && item.field_id === "forecast_annual_rate_as_reported") ?? facts[0];
  const index = json.columns.findIndex(column => column.field_id === fact.field_id);
  const definition = json.columns[index];
  const selected = json.projects.find(row => row.id === fact.project_id).cells[index];
  const present = Object.values(json.coverage).reduce((count, value) => count + value.present, 0);
  const toggle = page.locator("#show-display");
  await toggle.waitFor();
  assert.equal(await toggle.isChecked(), false);
  assert(await page.locator("#download-display").isHidden());
  await toggle.check();
  await expectText(page, "#display-coverage", new RegExp(`${present.toLocaleString("en-US")} / ${(93 * json.columns.length).toLocaleString("en-US")}`));
  await expectText(page, "#table-count", new RegExp(`延伸欄 ${json.columns.length} / ${json.columns.length}`));
  await page.click("#columns summary");
  const group = page.locator("#column-options details").filter({has:page.getByText(definition.label_zh, {exact:true})});
  const groupLabels = { resource:"資源估算", study:"開發研究", forecast:"產量預測", geology:"礦床類型", location:"位置" };
  assert.equal(await group.locator("summary").textContent(), groupLabels[definition.group]);
  await page.click("#columns summary");
  const field = page.locator("#census-table th").filter({hasText:definition.label_zh});
  await field.getByRole("button").click();
  assert.equal(await field.getAttribute("aria-sort"), "ascending");
  if (synthetic) {
    const first = page.locator("#census-table tbody tr").first();
    assert(await first.getByRole("button", {name:/8,300/}).isVisible());
    await field.getByRole("button").click();
    assert.equal(await field.getAttribute("aria-sort"), "descending");
    assert(await first.getByRole("button", {name:/29,000/}).isVisible());
  }
  await page.selectOption("#display-field", `display:${definition.field_id}`);
  await page.selectOption("#display-state", "PRESENT");
  await page.fill("#display-query", "no exact source value");
  await expectText(page, "#table-count", /^0 \/ 93/);
  await page.fill("#display-query", fact.value);
  await page.fill("#search", fact.project_id);
  await expectText(page, "#table-count", /^1 \/ 93/);
  await page.locator("#census-table tbody button").filter({hasText:selected.value}).and(
    page.getByRole("button", {name: `${definition.label_zh} · ${selected.value} · 已核對原件展示`}),
  ).click();
  await page.locator("dialog[open]").waitFor();
  const details = page.locator("#evidence-detail");
  const text = await details.textContent();
  assert([fact.quote, fact.raw_token, fact.scope, fact.source_sha256, fact.source_capture_sha256, fact.locator]
    .every(value => text.includes(value)));
  const source = details.locator("dl").filter({hasText:fact.source_sha256}).filter({hasText:fact.locator}).locator("a").first();
  const link = new URL(await source.getAttribute("href"));
  const physical = fact.locator.match(/\bPDF page (\d+)\b/i);
  if (physical) assert.equal(link.hash, `#page=${physical[1]}`);
  if (process.env.AU_REE_STAGE4_CAS) {
    link.hash = "";
    const bytes = await readFile(resolve(process.env.AU_REE_STAGE4_CAS, "objects", fact.source_sha256));
    assert.equal(digest(bytes), fact.source_sha256);
    const receipts = process.env.AU_REE_STAGE4_CAPTURES ?? resolve(process.env.AU_REE_STAGE4_CAS, "captures");
    const receipt = await readFile(resolve(receipts, `${fact.source_capture_sha256}.json`));
    assert.equal(digest(receipt), fact.source_capture_sha256);
    const context = page.context();
    await context.route(url => url.toString() === link.href, route => route.fulfill({
      body:bytes, contentType:"application/pdf", status:200,
    }));
    const requested = context.waitForEvent("request", request => request.url() === link.href);
    await source.click();
    assert.equal((await requested).url(), link.href);
    metrics.extended_original_source_readback = "PASS";
  }
  await page.keyboard.press("Escape");
  await page.fill("#search", "");
  await page.fill("#display-query", "");
  await page.selectOption("#display-state", "");
  if (json.review_only.length) {
    const [review] = json.review_only;
    await page.fill("#search", review.project_id);
    await page.click("#display-review summary");
    const table = await page.locator("#census-table tbody").textContent();
    assert(!table.includes(review.reason));
    const grid = json.projects.find(row => row.id === review.project_id);
    const accepted = grid.cells.filter(cell => cell.state === "PRESENT").length;
    assert.equal(await page.locator('#census-table tbody button[title="已核對原件展示"]').count(), accepted);
    await page.locator("#display-review button").filter({hasText:review.label}).first().click();
    const text = await details.textContent();
    assert(text.includes(review.quote) && text.includes(review.reason) && text.includes(review.source_capture_sha256));
    if (!review.publication_date) assert(text.includes(review.context.capture_date));
    await page.keyboard.press("Escape");
    await page.fill("#search", "");
  }
  const downloading = page.waitForEvent("download");
  await page.click("#download-display");
  const download = await downloading;
  assert.equal(download.suggestedFilename(), `census-display-extended-${leadId}.csv`);
  assert.equal(digest(await readFile(await download.path())), digest(served.get(art(leadId, displayCsvName))));
  await toggle.uncheck();
  assert(await page.locator("#download-display").isHidden());
  metrics.extended_fields_filters_sort_csv_evidence = "PASS";
  metrics.extended_fields = json.columns.length;
  metrics.extended_present_cells = present;
  metrics.extended_review_only_records = json.review_only.length;
}

try {
  const page = await browser.newPage({acceptDownloads:true,viewport:{width:1440,height:985}});
  await instrument(page);
  await page.goto(url);
  await expectText(page,"#status",/尚未正式發布/);
  assert(await page.locator("#workbench").isHidden());
  metrics.no_formal_pointer="PASS";
  served.set("data/latest.json",served.get(pointerKey));
  await page.reload();
  await expectText(page,"#status",/版次內容有誤/);
  assert(await page.locator("#workbench").isHidden());
  served.delete("data/latest.json");
  metrics.formal_pointer_rejects_candidate="PASS";


  const start=performance.now();
  await page.goto(url+"?preview=1");
  await page.locator("#census-table tbody tr").first().waitFor();
  metrics.browser_load_census_wall_ms=Math.round(performance.now()-start);
  const session = await page.context().newCDPSession(page);
  await session.send("Performance.enable");
  const browserMetrics = await session.send("Performance.getMetrics");
  const heap = browserMetrics.metrics.find(item => item.name === "JSHeapUsedSize")?.value;
  if (Number.isFinite(heap)) metrics.browser_js_heap_used_bytes=heap;
  await session.detach();
  assert.equal(await page.locator("#footer-release").textContent(),leadId);
  await expectText(page,"#metrics",new RegExp(coverageExpected.get(leadId).toLocaleString("en-US")));
  await page.click("#columns summary");
  await page.getByRole("button",{name:"顯示全部 51 欄"}).click();
  assert.equal(await page.locator("#census-table th").count(),51);
  await page.getByRole("button",{name:"恢復預設欄位"}).click();
  await page.click("#columns summary");
  await page.click("#next");
  await expectText(page,"#page-info",/13–24/);
  await page.locator("#census-table th").filter({hasText:"礦石蘊藏量_Mt"}).getByRole("button").click();
  await page.waitForSelector('th[aria-sort="ascending"]');
  await page.fill("#search",populated.id);
  await expectText(page,"#table-count",/^1 \/ 93/);
  const numericRow=page.locator("#census-table tbody tr").first();
  await numericRow.getByRole("button",{name:new RegExp(`礦石蘊藏量_Mt.*${observationValue}`)}).click();
  await page.locator("dialog[open]").waitFor();
  await expectText(page,"#evidence-detail",/SHA-256/);
  assert.match(await page.locator("#evidence-detail a").getAttribute("href"),/^https:\/\//);
  await page.keyboard.press("Escape");
  await page.fill("#search","");
  const states=await page.locator("#state-filter option").count();
  if (states>2) {
    await page.locator("#state-filter").selectOption({index:2});
    assert.doesNotMatch(await page.locator("#table-count").textContent(),/^93 \/ 93/);
    await page.selectOption("#state-filter","");
  }
  metrics.search_filter_sort_columns_evidence="PASS";
  if (extended) await checkDisplay(page);
  if (flags.includes("--census-fill")) {
    assert(!synthetic, "Census Fill regressions require the exact original candidate and baseline");
    const project = "AU-9bd8e5ed8408cd84d876", heading = "四項磁性元素皆公布";
    const column = newCensus.columns.findIndex(entry => entry.id === heading);
    const current = newCensus.projects.find(entry => entry.id === project).cells[column];
    const oldCensus = JSON.parse(served.get(art(priorId, "census.json")));
    assert.equal(oldCensus.projects.find(entry => entry.id === project).cells[column].state, "UNKNOWN");
    assert.equal(current.value, "是");
    assert.equal(current.origin, "EVIDENCE_REFERENCE");
    const entries = JSON.parse(served.get(exactEvidencePath)).entries;
    const evidence = entries[current.lineage_id];
    assert.equal(evidence.evidence_state, "DESCRIPTIVE_ONLY");
    assert.equal(evidence.input_observation_ids.length, 4);
    assert.deepEqual(evidence.input_observation_ids.map(id => entries[`observation:${id}`].source_column).sort(),
      ["Dy2O3", "Nd2O3", "Pr6O11", "Tb4O7"]);
    await page.click("#columns summary");
    await page.getByRole("button", {name: "顯示全部 51 欄"}).click();
    await page.fill("#search", project);
    await page.locator("#census-table tbody").getByRole("button", {name: new RegExp(`^${heading}.*是`)}).click();
    const dialog = page.locator("#evidence-detail");
    assert.match(await dialog.textContent(), /840 ppm.*p\. 6.*0\.85 kg\/t.*p\. 5/);
    assert.match(await dialog.locator("a").getAttribute("href"), /#page=6$/);
    await page.keyboard.press("Escape");
    await page.fill("#search", "");
    await page.getByRole("button", {name: "恢復預設欄位"}).click();
    await page.click("#columns summary");
    metrics.scoped_magnet_oxide_disclosure = "PASS";
  }
  if (flags.includes("--dated-study-coverage")) {
    assert(!synthetic, "Dated study regression requires the exact research candidate");
    const cases = [
      ["AU-216d0983f6892f9b58f9", "已公布預可行性研究結果", "STUDY_RESULTS_RELEASED", "August 2026", 8],
      ["AU-1345edd8165c10930e94", "已公布最終可行性研究結果", "STUDY_RESULTS_RELEASED", "7 February 2019", 1],
      ["AU-9b667066e6eb66753005", "已完成範疇研究", "COMPLETED", "August 2023", 12],
    ];
    const column = newCensus.columns.findIndex(entry => entry.id === "開發階段");
    const evidence = JSON.parse(served.get(exactEvidencePath)).entries;
    await page.click("#columns summary");
    await page.getByRole("button", {name: "顯示全部 51 欄"}).click();
    for (const [id, label, role, date, physicalPage] of cases) {
      const cell = newCensus.projects.find(entry => entry.id === id).cells[column];
      const proof = evidence[cell.lineage_id];
      assert.equal(cell.origin, "SOURCE_EVIDENCE");
      assert.equal(proof.metadata.attribute_role, role);
      assert.equal(proof.metadata.event_date_token, date);
      assert.equal(proof.physical_page, physicalPage);
      await page.fill("#search", id);
      await page.locator("#census-table tbody").getByRole("button", {name: new RegExp(`^開發階段 · ${label}`)}).click();
      const dialog = page.locator("#evidence-detail");
      assert((await dialog.textContent()).includes(proof.source_sha256));
      assert((await dialog.textContent()).includes(role));
      assert.match(await dialog.locator("a").getAttribute("href"), new RegExp(`#page=${physicalPage}$`));
      await page.keyboard.press("Escape");
    }
    await page.fill("#search", "");
    await page.getByRole("button", {name: "恢復預設欄位"}).click();
    await page.click("#columns summary");
    metrics.dated_study_evidence = "PASS";
  }
  if (flags.includes("--stage3")) {
    assert(!synthetic, "Stage 3 regressions require exact archived candidate data");
    const expectedProducts = [
      ["AU-dac3c8e40326b8244cdb", "2890", 4, "總TREO蘊藏量_kt", 2],
      ["AU-216d0983f6892f9b58f9", "134.560032", 97, "總TREO蘊藏量_kt", 2],
    ];
    expectedProducts.push(["AU-0098e17a63e7c05c213f", "16.8", 3, "磁鐵稀土氧化物蘊藏量_kt", 3]);
    const newEvidence = JSON.parse(served.get(exactEvidencePath)).entries;
    const oldGrid = JSON.parse(served.get(art(priorId,"census.json")));
    await page.click("#columns summary");
    await page.getByRole("button",{name:"顯示全部 51 欄"}).click();
    for (const [id, value, physicalPage, field, inputCount] of expectedProducts) {
      const row = newCensus.projects.find(project => project.id === id);
      const column = newCensus.columns.findIndex(item => item.id === field);
      const current = row.cells[column];
      assert.equal(Number(current.value), Number(value));
      assert.equal(current.origin, "FORMAL_DERIVED");
      const evidence = newEvidence[current.lineage_id];
      assert.equal(evidence.physical_page, physicalPage);
      assert.equal(evidence.input_observation_ids.length, inputCount);
      if (inputCount === 3) {
        assert.equal(evidence.chemical_basis, "PrNdDyTb_REO");
        assert.deepEqual(current.precision.rounding_interval.map(Number), [16.66925, 16.93125]);
      }
      assert(evidence.formula.includes("0.001"));
      const historical = oldGrid.projects.find(project => project.id === id).cells[column].value;
      assert.equal(historical, inputCount === 2 ? current.value : null);
      await page.fill("#search", id);
      await expectText(page,"#table-count",/^1 \/ 93/);
      await page.locator("#census-table tbody").getByRole("button",{name:new RegExp(`^${field}`)}).click();
      const dialog = page.locator("#evidence-detail");
      await page.locator("dialog[open]").waitFor();
      const detail = await dialog.textContent();
      assert(detail.includes(evidence.formula));
      assert(detail.includes(evidence.source_sha256));
      assert(evidence.input_observation_ids.every(id => detail.includes(id)));
      await page.keyboard.press("Escape");
    }

    if (process.env.AU_REE_STAGE3_CAS) {
      const [id, , , field] = expectedProducts.at(-1);
      const project = newCensus.projects.find(row => row.id === id);
      const column = newCensus.columns.findIndex(item => item.id === field);
      const evidence = newEvidence[project.cells[column].lineage_id];
      const bytes = await readFile(resolve(process.env.AU_REE_STAGE3_CAS, "objects", evidence.source_sha256));
      assert.equal(digest(bytes), evidence.source_sha256);
      await page.fill("#search", id);
      await expectText(page, "#table-count", /^1 \/ 93/);
      await page.locator("#census-table tbody").getByRole("button", {name:new RegExp(`^${field}`)}).click();
      const context = page.context();
      const source = evidence.source_url;
      await context.route(url => url.toString() === source, route => route.fulfill({
        body:bytes, contentType:"application/pdf", status:200,
      }));
      const requested = context.waitForEvent("request", request => request.url() === source);
      await page.locator("#evidence-detail a").click();
      assert.equal((await requested).url(), source);
      await page.keyboard.press("Escape");
      metrics.original_source_link_readback = "PASS";
    }
    metrics.exact_derived_evidence_dialogs = expectedProducts.length;
    {
      let checked = 0, preserved = 0;
      const oldEvidence = JSON.parse(served.get(art(priorId,"census_evidence.json"))).entries;
      for (const project of newCensus.projects) {
        for (const [index, cell] of project.cells.entries()) {
          const evidence = newEvidence[cell.lineage_id];
          if (evidence.evidence_state !== "VALIDATED_DESCRIPTIVE") continue;
          const priorCell = oldGrid.projects.find(row => row.id === project.id).cells[index];
          if (priorCell.value !== null) {
            assert.equal(priorCell.value, cell.value);
            assert.equal(oldEvidence[priorCell.lineage_id].source_sha256, evidence.source_sha256);
            preserved++;
          }
          await page.fill("#search", project.id);
          await expectText(page,"#table-count",/^1 \/ 93/);
          const heading = newCensus.columns[index].id;
          await page.locator("#census-table tbody").getByRole("button",{name:new RegExp(`^${heading} · `)}).click();
          await page.locator("dialog[open]").waitFor();
          const detail = await page.locator("#evidence-detail").textContent();
          assert(detail.includes(evidence.source_sha256));
          assert(detail.includes(evidence.locator));
          assert(await page.locator("#evidence-detail a").count());
          await page.keyboard.press("Escape");
          checked++;
        }
      }
      assert.equal(checked, 11);
      assert.equal(preserved, 5);
      metrics.preserved_historical_attributes = preserved;
      metrics.typed_attribute_evidence_dialogs = checked;
      await page.getByRole("button",{name:"恢復預設欄位"}).click();
      await page.click("#columns summary");
    }
  }
  await page.fill("#search","");

  await page.click('[data-tab="map"]');
  await expectText(page,"#map-details",/無可驗證座標/);
  assert.equal(await page.locator("#map-details button").count(),0);
  await page.check("#map-only-resource");
  await expectText(page,"#map-details",/無可驗證座標/);
  await page.click("#map-compare");
  assert((await page.locator("#compare-table tbody tr").count())>=15);
  await page.selectOption("#compare-metric","resource");
  assert.equal(await page.locator("#compare-table tbody tr").count(),3);
  await page.selectOption("#compare-metric","all");
  const optionIds = await page.locator("#compare-left option").evaluateAll(nodes=>nodes.map(node=>node.value));
  await page.selectOption("#compare-left",optionIds.at(-1));
  await page.click('[data-tab="mcda"]');
  await expectText(page,"#panel-detail",/完整樣本 2/);
  assert((await page.locator("#panel-detail table").count())>=2);
  await page.selectOption("#panel","six_domain");
  await expectText(page,"#panel-detail",/INSUFFICIENT_COHORT/);
  assert.equal(await page.locator("#panel-detail table").count(),0);
  await page.click("#language");
  await expectText(page,"h1",/Rare-earth research overview/);
  await page.click("#language");
  await page.click('[data-tab="census"]');
  metrics.map_ree_mcda_language="PASS";

  if (extended) await page.locator("#show-display").check();
  const before = performance.now();
  await version(page,priorId);
  metrics.browser_history_load_wall_ms=Math.round(performance.now()-before);
  await expectText(page,"#metrics",new RegExp(coverageExpected.get(priorId).toLocaleString("en-US")));
  const downloading=page.waitForEvent("download");
  await page.click("#download");
  const download=await downloading;
  assert.equal(download.suggestedFilename(),`census-${priorId}.csv`);
  assert.equal(digest(await readFile(await download.path())),digest(oldCsv));
  assert(new URL(page.url()).searchParams.get("release")===priorId);
  if (priorHasDisplay) {
    await page.locator("#show-display").check();
    const downloading = page.waitForEvent("download");
    await page.locator("#download-display").click();
    const extension = await downloading;
    assert.equal(extension.suggestedFilename(), `census-display-extended-${priorId}.csv`);
    assert.equal(digest(await readFile(await extension.path())), digest(served.get(art(priorId, displayCsvName))));
  } else {
    assert(await page.locator("#show-display").isHidden());
    assert.equal(await page.locator("#download-display").getAttribute("href"), null);
  }
  const priorDisplayHref = await page.locator("#download-display").getAttribute("href");
  await page.click("#refresh");
  await page.locator(".update-button").waitFor();
  assert.equal(await page.locator("#footer-release").textContent(),priorId);
  metrics.history_csv_update_link="PASS";

  fault={matches:path=>path===art(leadId,"coverage.json"),response:{body:served.get(art(priorId,"coverage.json")),contentType:"application/json"}};
  await page.click(".update-button");
  await expectText(page,"#status",/指紋/);
  assert.equal(await page.locator("#footer-release").textContent(),priorId);
  assert.equal(await page.locator("#version").inputValue(),priorId);
  fault={matches:path=>path===art(leadId,"mcda.json"),response:{status:404,body:"missing"}};
  await page.selectOption("#version",leadId);
  await expectText(page,"#status",/缺少必要/);
  assert.equal(await page.locator("#footer-release").textContent(),priorId);
  fault=null;
  metrics.mixed_cache_missing_atomic="PASS";
  if (extended) {
    for (const [name, response, message] of [
      [displayName, {body:buffer({}), contentType:"application/json"}, /指紋/],
      [displayCsvName, {status:404, body:"missing"}, /缺少必要/],
    ]) {
      const download = await page.locator("#download").getAttribute("href");
      fault = {matches:path=>path===art(leadId,name), response};
      await page.selectOption("#version", leadId);
      await expectText(page, "#status", message);
      assert.equal(await page.locator("#footer-release").textContent(), priorId);
      assert.equal(await page.locator("#version").inputValue(), priorId);
      assert.equal(await page.locator("#download").getAttribute("href"), download);
      assert.equal(await page.locator("#show-display").isHidden(), !priorHasDisplay);
      assert.equal(await page.locator("#download-display").getAttribute("href"), priorDisplayHref);
    }
    fault = null;
    metrics.extended_missing_corrupt_keeps_prior_version = "PASS";
  }
  async function rejectedData(file, mutate) {
    const filePath=art(leadId,file), manifestPath=art(leadId,"manifest.json");
    const original=served.get(filePath), originalManifest=served.get(manifestPath);
    const info=JSON.parse(originalManifest);
    const prototype=file.endsWith(".csv") ? original.toString("utf-8") : JSON.parse(original);
    const mutated=mutate(prototype) ?? prototype;
    const body=file.endsWith(".csv") ? Buffer.from(mutated) : buffer(mutated);
    info.artifacts[file].sha256=digest(body);
    const manifest=buffer(info);
    const rehashed={...lead,manifest_sha256:digest(manifest)};
    served.set(filePath,body);
    served.set(manifestPath,manifest);
    writePointer(rehashed,[rehashed,...entries.slice(1)]);
    try {
      await page.goto(url+"?preview=1");
      await expectText(page,"#status",/版次內容有誤/);
      assert(await page.locator("#workbench").isHidden());
    } finally {
      served.set(filePath,original);
      served.set(manifestPath,originalManifest);
      writePointer();
    }
  }
  await rejectedData("gis.geojson", gis=>gis.features.push({
    type:"Feature",id:populated.id,geometry:{type:"Point",coordinates:[120,-27]},
    properties:{project_id:populated.id,geometry_role:"VERIFIED_MINE_POINT",
      longitude_lineage_id:populated.cells[9].lineage_id,latitude_lineage_id:populated.cells[8].lineage_id},
  }));
  await rejectedData("mcda.json", mcda=>{
    mcda.panels.find(item=>item.id==="six_domain").status="PASS";
  });
  await rejectedData("census.json", census=>{
    census.selected_composition ||= {};
    census.selected_composition[populated.id]=[{variable:"mreo_ppm",lineage_id:"absent",unit:"ppm",value:"1"}];
  });
  metrics.rehashed_unqualified_pin_rank_lineage="PASS";
  if (extended) {
    await rejectedData(displayName, payload=>{ payload.release_id=priorId; });
    await rejectedData(displayName, payload=>{ payload.projects.reverse(); });
    await rejectedData(displayName, payload=>{ Object.values(payload.facts)[0].project_id=payload.projects.at(-1).id; });
    await rejectedData(displayName, payload=>{ Object.values(payload.facts)[0].data_role="CANDIDATE"; });
    await rejectedData(displayName, payload=>{ payload.columns[0].basis="Unpinned basis"; });
    await rejectedData(displayName, payload=>{ Object.values(payload.facts)[0].quote="Unpinned original excerpt"; });
    await rejectedData(displayCsvName, payload=>payload.replace(/^專案ID/, "unknown_field_name"));
    metrics.extended_rehashed_mixed_role_lineage_csv_refused = "PASS";
  }


  const originalEvidence = served.get(exactEvidencePath);
  const evidence = JSON.parse(originalEvidence);
  const inserted = "<script>window.auReeInjected = 1</script><img src=x onerror=alert(1)>";
  Object.assign(evidence.entries[populated.cells[15].lineage_id],{reason:inserted,source_url:"javascript:alert(1)"});
  const badBytes=buffer(evidence);
  const manifestPath=art(leadId,"manifest.json");
  const originalManifest=served.get(manifestPath);
  const manifest=JSON.parse(originalManifest);
  manifest.artifacts["census_evidence.json"].sha256=digest(badBytes);
  const pollutedManifest=buffer(manifest);
  const injected={...lead,manifest_sha256:digest(pollutedManifest)};
  entries=[injected,...entries.slice(1)];
  served.set(exactEvidencePath,badBytes);
  served.set(manifestPath,pollutedManifest);
  writePointer(injected);
  await page.goto(url+`?preview=1&release=${leadId}&project=${populated.id}`);
  await page.locator("#census-table tbody tr").first().waitFor();
  await page.locator("#census-table tbody").getByRole("button",{name:new RegExp(`礦石蘊藏量_Mt.*${observationValue}`)}).click();
  const detail=page.locator("#evidence-detail");
  assert((await detail.textContent()).includes(inserted));
  assert.equal(await detail.locator("script,img,a").count(),0);
  assert.equal(await page.evaluate(()=>window.auReeInjected),undefined);
  metrics.xss_and_source_link="PASS";
  served.set(exactEvidencePath,originalEvidence);
  served.set(manifestPath,originalManifest);
  entries=[lead,...entries.slice(1)];
  writePointer();
  await page.goto(url+"?preview=1");
  await page.locator("#census-table tbody tr").first().waitFor();

  if (capture) {
    await mkdir(capture,{recursive:true});
    await page.screenshot({path:resolve(capture,"census-desktop.png")});
  }
  const mobile = await browser.newPage({viewport:{width:390,height:844},isMobile:true,deviceScaleFactor:1});
  await instrument(mobile);
  await mobile.goto(url+"?preview=1");
  await mobile.locator("#census-table tbody tr").first().waitFor();
  assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  if (extended) {
    await mobile.locator("#show-display").check();
    assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  }
  await mobile.click('[data-tab="map"]');
  await expectText(mobile,"#map-details",/無可驗證座標/);
  if (capture) await mobile.screenshot({path:resolve(capture,"map-mobile.png"),fullPage:true});
  metrics.mobile="PASS";
  if (flags.includes("--stage6")) {
    const lineagePage = await browser.newPage({viewport:{width:1440,height:985}});
    await instrument(lineagePage);
    await lineagePage.goto(url+`?preview=1&project=${nolansId}`);
    await expectText(lineagePage,"#table-count",/^1 \/ 93/);
    await lineagePage.click("#columns summary");
    await lineagePage.getByRole("button",{name:"顯示全部 51 欄"}).click();
    await lineagePage.locator("#census-table tbody").getByRole("button",{name:new RegExp(`^${nolansInventoryField}`)}).click();
    await expectText(lineagePage,"#evidence-detail",new RegExp(nolansSource));
    await lineagePage.close();
    metrics.nolans_source_and_derived_lineage = "PASS";
  }
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:"PASS",runtime:process.version,chromium:browser.version(),
    corpus:synthetic?"SYNTHETIC_BROWSER_FIXTURE":"EXACT_RESEARCH_PREVIEWS",
    versions:[leadId,priorId],metrics},null,2));
} finally {
  await browser.close();
}
