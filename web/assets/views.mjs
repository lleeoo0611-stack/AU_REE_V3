import {publicSourceURL} from "./release.mjs";

export function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

export function options(select, items, selected) {
  select.replaceChildren(...items.map(([value, label]) => {
    const item = node("option", label);
    item.value = value;
    return item;
  }));
  if (items.some(([value]) => value === selected)) select.value = selected;
}

export function tableRows(table, labels, rows) {
  const heading = node("tr");
  for (const label of labels) {
    const cell = node("th", label);
    cell.scope = "col";
    heading.append(cell);
  }
  const body = rows.map(row => {
    const tr = node("tr");
    for (const value of row) {
      const cell = node("td");
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value === null || value === undefined ? "—" : String(value);
      tr.append(cell);
    }
    return tr;
  });
  table.tHead.replaceChildren(heading);
  table.tBodies[0].replaceChildren(...body);
}

function makeTable(labels, rows) {
  const wrapper = node("div", null, "table-scroll");
  wrapper.tabIndex = 0;
  const table = node("table");
  table.createTHead();
  table.createTBody();
  tableRows(table, labels, rows);
  wrapper.append(table);
  return wrapper;
}

function nativeValue(entry) {
  return [entry.raw_value, entry.raw_unit].filter(value => value !== null && value !== undefined).join(" ");
}

function asText(value) {
  if (Array.isArray(value)) return value.map(asText).join(" · ");
  if (value && typeof value === "object") return Object.entries(value).map(([key, entry]) => `${key} = ${asText(entry)}`).join(" · ");
  return String(value);
}

export function evidenceDetails(entry, cell, t, status) {
  const details = node("dl", null, "evidence-list");
  const append = (term, value, code = false) => {
    if (value === undefined || value === null || value === "") return;
    const item = node("dd", null, code ? "code" : "");
    if (value instanceof Node) item.append(value);
    else item.textContent = asText(value);
    details.append(node("dt", t(term)), item);
  };
  append("value", cell.value ?? status);
  append("provenance", cell.origin);
  append("evidenceState", entry.evidence_state);
  append("reason", entry.reason);
  const url = publicSourceURL(entry.source_url, entry.physical_page);
  if (url) {
    const anchor = node("a", t("showSource"));
    anchor.href = url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    append("sourceUrl", anchor);
  }
  append("sourceSha", entry.source_sha256, true);
  append("page", entry.physical_page);
  append("locator", entry.locator);
  append("raw", nativeValue(entry));
  append("sourceQuote", entry.quote);
  append("scope", entry.scope ?? entry.scope_id);
  append("effective", entry.effective_date);
  append("chemical", entry.chemical_basis);
  append("material", entry.material_basis);
  append("denominator", entry.denominator_basis);
  append("formula", entry.formula ?? entry.derivation);
  append("precision", cell.precision);
  append("sourcePrecisionReview", entry.metadata?.precision_review);
  append("inputs", entry.input_observation_ids ?? entry.observation_ids);
  append("observationId", entry.observation_id, true);
  append("temporal", entry.temporal_decision_id, true);
  append("registry", entry.registry_sha256, true);
  append("projection", entry.projection_policy_sha256 ?? entry.policy_sha256, true);
  append("reportingRole", entry.reporting_role && t(entry.reporting_role));
  append("publicationDate", entry.publication_date);
  append("captureSha", entry.source_capture_sha256, true);
  append("displayContract", entry.display_contract, true);
  append("fieldVersion", entry.field_version);
  append("displayContext", entry.context && Object.keys(entry.context).length ? entry.context : null);
  if (entry.evidence_state === "VALIDATED_DESCRIPTIVE") {
    append("attributeRole", entry.metadata?.attribute_role);
    append("sourceStatement", entry.metadata?.native_value);
    if (!entry.publication_date) append("publicationDate", entry.metadata?.publication_date);
    append("eventDate", entry.metadata?.event_date_token);
    append("attributePolicy", entry.metadata?.policy_sha256, true);
  }
  return details;
}

export function displayEvidence(records, cell, column, t, status, contractSha) {
  const container = node("div", null, "display-evidence");
  for (const fact of records) {
    const pages = [...fact.locator.matchAll(/\b(?:physical (?:pdf )?|pdf )?page\s+([1-9]\d*)\b/gi)];
    const page = pages.length === 1 ? Number(pages[0][1]) : null;
    const entry = {
      ...fact, raw_value: fact.raw_token, physical_page: page, evidence_state: fact.state ? t(fact.state) : t("SOURCE_REPORTED_DISPLAY"),
      reporting_role: fact.data_role, display_contract: contractSha, field_version: column.definition?.version,
      publication_date: fact.publication_date || t("missing"),
    };
    container.append(evidenceDetails(entry, {...cell, value: fact.value ?? cell.value}, t, status));
  }
  if (!records.length) container.append(node("p", status));
  return container;
}

function coordinates([x, y], canvas) {
  return [41 + (x - 108) * (canvas.width - 82) / 49, 36 + (-9 - y) * (canvas.height - 72) / 37];
}

export function renderMap(canvas, outline, geographic, coverage, side, names, t, openPoint, totalPoints) {
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = "#e8f2ef";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#ccded7";
    ctx.lineWidth = 1;
    for (let X = 60; X < canvas.width; X += 90) {
      ctx.beginPath(); ctx.moveTo(X, 22); ctx.lineTo(X, canvas.height - 22); ctx.stroke();
    }
    for (let Y = 40; Y < canvas.height; Y += 64) {
      ctx.beginPath(); ctx.moveTo(26, Y); ctx.lineTo(canvas.width - 26, Y); ctx.stroke();
    }
    ctx.fillStyle = "#b6d1c1";
    ctx.strokeStyle = "#6f9a86";
    ctx.lineWidth = 1.25;
    for (const polygon of outline?.geometry?.coordinates ?? []) {
      ctx.beginPath();
      for (const ring of polygon) {
        for (const [i, point] of ring.entries()) {
          const [x, y] = coordinates(point, canvas);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
      }
      ctx.fill("evenodd"); ctx.stroke();
    }
    for (const point of geographic.features) {
      const [x, y] = coordinates(point.geometry.coordinates, canvas);
      ctx.beginPath(); ctx.arc(x, y, 6, 0, 2 * Math.PI);
      ctx.fillStyle = "#134c40"; ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke();
    }
  }

  const projects = new Set(geographic.features.map(feature => feature.properties.project_id)).size;
  canvas.setAttribute("aria-label", `${t("mapPoints")} ${projects} / ${coverage.projects}`);
  side.replaceChildren(
    node("h3", projects ? t("mapPoints") : t(totalPoints ? "noFilteredPoints" : "noPoints")),
    node("p", `${projects} / ${coverage.projects} · ${t("mapPoints")}`),
    node("p", t("mapLimits")),
    node("p", t("baseMap")),
  );
  if (projects) {
    const list = node("ul");
    for (const point of geographic.features) {
      const id = point.properties.project_id;
      const button = node("button", names(id), "button secondary");
      button.type = "button";
      button.addEventListener("click", () => openPoint(id));
      const item = node("li");
      item.append(button);
      list.append(item);
    }
    side.insertBefore(list, side.children[2]);
  }
}

function pointRanks(panel, names) {
  const rows = [];
  for (const [scenario, methods] of Object.entries(panel.point_rankings ?? {})) {
    for (const [method, projects] of Object.entries(methods)) {
      for (const [id, result] of Object.entries(projects).sort((a, b) => a[1].rank - b[1].rank)) {
        rows.push([scenario, method, result.rank, names(id), result.score]);
      }
    }
  }
  return rows;
}

function sensitivityRows(panel, names) {
  const rows = [];
  for (const [scenario, variants] of Object.entries(panel.leave_one_out ?? {})) {
    for (const variant of variants) {
      for (const [id, result] of Object.entries(variant.projects ?? {})) {
        rows.push([scenario, variant.removed, names(id), result.rank, result.rank_change]);
      }
    }
  }
  return rows;
}

export function renderDecision(detail, panel, names, t) {
  detail.replaceChildren();
  if (!panel) return;
  detail.append(node("h3", `${t("cohort")} ${panel.eligible_count} · ${t("minimum")} ${panel.minimum_cohort}`));
  if (panel.status !== "PASS") {
    detail.append(node("p", `${t("insufficient")} · ${panel.status}`));
  } else {
    const rankings = pointRanks(panel, names);
    if (rankings.length) detail.append(makeTable(
      ["scenario", "method", "rank", "project", "score"].map(t), rankings,
    ));
    const weights = Object.entries(panel.scenario_weights ?? {}).map(([scenario, codes]) => [
      scenario, Object.entries(codes).map(([criterion, weight]) => `${criterion} = ${weight}`).join(" · "),
    ]);
    if (weights.length) detail.append(node("h3", t("weights")), makeTable(["scenario", "weights"].map(t), weights));
    const sensitive = sensitivityRows(panel, names);
    detail.append(node("h3", t("sensitivity")));
    detail.append(sensitive.length ? makeTable(
      ["scenario", "removed", "project", "rank", "rankChange"].map(t), sensitive,
    ) : node("p", t("noSensitivity")));
  }
  detail.append(node("p", t("legacyT1")), node("p", t("shadowT1")));
}
