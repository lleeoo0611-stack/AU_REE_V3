"""Audit the uses of recovered evidence against pinned V3 research outputs."""

from collections import Counter, defaultdict
from hashlib import sha256
import json

from .census import COLUMNS
from .contracts.temporal import TEMPORAL_POLICY_VERSION
from .fresh_census import FORMAL_CENSUS_ORIGINS
from .native_table_work import COHORT_RECOVERY_SCHEMA, recovered_cohort_records
from .research_delta import ResearchBuild

SCHEMA = "historical-cohort-admission-v1"
# Different first breaks on alternative routes remain visible. The cell headline
# uses the route that advanced the furthest in this fixed diagnostic order.
_ORDER = (
    "ORIGINAL", "CUTOFF", "COLUMN_ROLE", "SCOPE", "JORC_CATEGORY", "ESTIMATE_DATE",
    "RESOURCE_CUTOFF", "MATERIAL_BASIS", "CHEMICAL_BASIS", "PRECISION", "CONFLICT",
    "SELECTION", "MCDA", "DISPLAY",
)
_PENDING = {"BLOCKED", "FAIL", "NOT_RUN"}
_DERIVED_S1 = {"磁鐵稀土氧化物蘊藏量_kt", "磁鐵稀土佔TREO_pct", "磁性元素重算值_NdPrTbDy_kt"}
_RESOURCE_COLUMNS = {label: variable for variable, label in COLUMNS.items()}


def _load(path):
    data = path.read_bytes()
    return json.loads(data), sha256(data).hexdigest()


def _check(status, reason, **proof):
    return {"status": status, "reason": reason, **proof}


def _first(checks):
    return next((gate for gate in _ORDER if checks.get(gate, {}).get("status") in _PENDING), None)


def _purpose(checks, result):
    gate = _first(checks)
    return {"status": checks[gate]["status"] if gate else result, "first_open_gate": gate,
            "reason": checks[gate]["reason"] if gate else result, "checks": checks}


def _indexed_cells(receipt):
    rows = receipt["cells"]
    cells = {(row["project_id"], row["column"]): row for row in rows}
    if len(cells) != len(rows) or receipt.get("schema") != COHORT_RECOVERY_SCHEMA:
        raise ValueError("Invalid historical recovery cohort axes")
    if receipt.get("status") != "PASS":
        raise ValueError("Cohort admission audit needs a successful original recovery receipt")
    return cells


def _raw_records(database, expected):
    payload = database.read_bytes()
    if sha256(payload).hexdigest() != expected:
        raise ValueError("Historical candidate SQLite differs from the exact recovery receipt")
    return {row["candidate_id"]: row for row in recovered_cohort_records(payload)}


def _keys(cells):
    return {key for key, row in cells.items() if any(row[field] for field in (
        "original_anchor_ids", "candidate_text_evidence", "candidate_government_point",
    ))}


def _display(directory, build):
    if directory is None:
        return {}, set(), None
    path = directory / "census_display.json"
    payload, digest = _load(path)
    receipt, pin = _load(directory / "display_package_receipt.json")
    if (receipt.get("status") != "PASS" or receipt.get("build_receipt_sha256") != build.receipt_sha
            or receipt["files"].get(path.name) != digest or payload["release_id"] != receipt["release_id"]):
        raise ValueError("Cohort display was not verified for the requested candidate build")
    matches = {}
    visible_keys = set()
    for entry in payload["review_only"]:
        context = entry["context"]
        if context.get("display_value") and context.get("census_column_id") and context.get("candidate_id"):
            matches[(entry["project_id"], context["census_column_id"], context["candidate_id"])] = entry
            visible_keys.add((entry["project_id"], context["census_column_id"]))
    for entry in payload["facts"].values():
        if entry["field_id"] == "forecast_product_and_basis":
            visible_keys.add((entry["project_id"], "product_output"))
    return matches, visible_keys, pin


def _study_reviews(path, records, recovery):
    if path is None:
        return {}, None
    review, digest = _load(path)
    if (review.get("status") != "PASS" or review.get("database_sha256") != recovery["database_sha256"]
            or review.get("source_manifest_sha256") != recovery["source_manifest_sha256"]):
        raise ValueError("Statement review does not match the recovered SQLite and source manifest")
    expected = {(r["project_id"], r["source_sha256"], r["physical_page"], r["row_anchor"]["locator"])
                for r in records.values()}
    by_row = {}
    for row in review["records"]:
        key = row["project_id"], row["source_sha256"], row["physical_page"], row["row_locator"]
        if key not in expected or key in by_row:
            raise ValueError("Statement evidence is detached from the recovered source row")
        by_row[key] = row
    return by_row, digest


def _criteria(column):
    result = set()
    if column in COLUMNS.values():
        result.add("R1")
    if column.split("_", 1)[0] in {"Pr6O11", "Nd2O3", "Tb4O7", "Dy2O3"}:
        result.add("S1")
    if column in _DERIVED_S1:
        result.add("S1")
    return sorted(result)


def _criterion_receipt(current, cell, shas, formal):
    criteria = {}
    for criterion in _criteria(cell["column"]):
        item = current.decision_inputs["criteria_by_project"][cell["project_id"]][criterion]
        joined = shas & {line["source_sha256"] for line in item["lineage"]}
        matched = item["state"] == "COMPARABLE" and bool(joined) and formal["status"] == "PASS"
        criteria[criterion] = {"status": "PASS" if matched else "BLOCKED",
                               "state": item["state"], "reason": item["reason"], "joined_sha256": sorted(joined)}
    if not criteria:
        return _purpose({}, "NOT_APPLICABLE")
    if all(item["status"] == "PASS" for item in criteria.values()):
        return {**_purpose({}, "PASS"), "criteria": criteria}
    checks = dict(formal["checks"])
    checks["MCDA"] = _check("BLOCKED", "NO_MATCHING_COMPARABLE_CRITERION_LINEAGE",
                           policy_version=current.decision_inputs["policy_version"])
    return {**_purpose(checks, "PASS"), "criteria": criteria}


def _numeric_role(column, row):
    if expected := _RESOURCE_COLUMNS.get(column):
        return expected == row["variable"]
    oxide = column.split("_", 1)[0]
    return "_氧化" in column and (row.get("chemical_basis") or "").casefold() == oxide.casefold()


def _native_checks(cell, row, review):
    direct = _numeric_role(cell["column"], row)
    checks = {
        "ORIGINAL": _check("PASS", "EXACT_ORIGINAL_TOKEN_ANCHOR"),
        "CUTOFF": _check("PASS", "APPROVED_EDITION_DECISION_LINK",
                         decision_id=row["temporal_decision_id"], policy_version=TEMPORAL_POLICY_VERSION)
        if row["source_capture_sha256"] else _check("BLOCKED", "EXACT_SAME_EDITION_PUBLIC_PROOF_UNBOUND"),
        "COLUMN_ROLE": _check("PASS", "ORIGINAL_HEADER_AND_UNIT_FOR_CENSUS_FIELD")
        if direct else _check("BLOCKED", "NO_DIRECT_VALUE_ROLE_FOR_TARGET_FIELD"),
        "PRECISION": _check("PASS", "ORIGINAL_TOKEN_ROUNDING_INTERVAL", bounds=row["raw_rounding_interval"])
        if row.get("raw_rounding_interval") else _check("BLOCKED", "PRINTED_PRECISION_UNBOUND"),
        "SCOPE": _check("BLOCKED", "TABLE_ROW_PROJECT_RESOURCE_SCOPE_UNBOUND"),
    }
    fields = {"SCOPE": "SCOPE", "JORC_CATEGORY": "JORC_CATEGORY", "ESTIMATE_DATE": "ESTIMATE_DATE",
              "CUTOFF": "RESOURCE_CUTOFF", "MATERIAL_BASIS": "MATERIAL_BASIS",
              "CHEMICAL_BASIS": "CHEMICAL_BASIS", "CONFLICT": "CONFLICT"}
    observed = next((item for item in review.get("observations", ())
                     if item["observation_id"] == row["candidate_id"]), {})
    provided = {**review.get("checks", {}), **observed.get("checks", {})}
    for source, target in fields.items():
        result = provided.get(source, {})
        if result.get("status") in _PENDING | {"PASS", "NOT_APPLICABLE"}:
            locators = [proof["locator"] if isinstance(proof, dict) else proof
                        for proof in result.get("proof", ())]
            checks[target] = _check(result["status"], result["reason"], proof_locators=locators)
        elif target not in checks:
            checks[target] = _check("NOT_RUN", "GOVERNED_STATEMENT_CHECK_NOT_EXECUTED")
    matches = observed.get("governed_matches", ())
    if direct:
        checks["ORIGINAL"]["governed_observation_ids"] = sorted(match["observation_id"] for match in matches
            if match["outcome"] == "VALIDATED" and match["selected_for_census"])
    conflicts = [match["reason"] for match in matches if match["outcome"] == "SCIENTIFIC_CONFLICT"]
    if conflicts:
        checks["CONFLICT"] = _check("FAIL", "EXACT_CELL_HAS_GOVERNED_SCIENTIFIC_CONFLICT", reasons=conflicts)
    return checks


def _text_checks(text):
    decision = text["temporal_decision"]
    status = {"ELIGIBLE": "PASS", "CONFLICT": "FAIL", "INELIGIBLE": "FAIL"}.get(decision["status"], "BLOCKED")
    return {
        "ORIGINAL": _check("PASS", "EXACT_ORIGINAL_QUOTE_ANCHOR"),
        "CUTOFF": _check(status, decision["reason"],
                         decision_id=decision["decision_id"], policy_version=decision["policy_version"]),
        "COLUMN_ROLE": _check("BLOCKED", "ORIGINAL_QUOTE_REQUIRES_TYPED_ATTRIBUTE_ASSERTION"),
        "SCOPE": _check("BLOCKED", "ATTRIBUTE_SUBJECT_UNBOUND"),
        "CONFLICT": _check("NOT_RUN", "TYPED_ATTRIBUTE_CONFLICT_ASSESSMENT_NOT_EXECUTED"),
    }


def _geography_checks(point):
    exact = point["comparison"] == "MATCH_AT_HISTORICAL_PRECISION"
    return {
        "ORIGINAL": _check("PASS" if exact else "FAIL", point["comparison"]),
        "CUTOFF": _check("BLOCKED", "NATIVE_FEATURE_CUTOFF_ELIGIBILITY_NOT_ESTABLISHED"),
        "COLUMN_ROLE": _check("PASS", "NATIVE_POINT_COORDINATE_WITH_RETAINED_ROLE",
                              location_role=point["location_role"], crs=point["capture_crs"]),
        "SCOPE": _check("BLOCKED", "HISTORICAL_ID_REPLAY_REQUIRES_APPROVED_CURRENT_PROJECT_FEATURE_BINDING"),
        "PRECISION": _check("PASS" if exact else "FAIL", point["comparison"]),
    }


def _paths(cell, raw, review, direct):
    project, column = cell["project_id"], cell["column"]
    hinted = set(cell["original_anchor_ids"])
    for candidate in sorted(hinted | direct.get((project, column), set())):
        row = raw[candidate]
        sha, locator = row["source_sha256"], row["row_anchor"]["locator"]
        if row["project_id"] != project or (candidate in hinted and sha not in cell["source_sha256"]):
            raise ValueError("Census route references a candidate from an unrelated Project or source")
        checks = _native_checks(cell, row, review.get((project, sha, row["physical_page"], locator), {}))
        yield "NUMERIC", sha, [row["value_anchor"]["locator"]], candidate, checks
    if text := cell["candidate_text_evidence"]:
        yield "TEXT", text["source_sha256"], [anchor["locator"] for anchor in text["anchors"]], None, _text_checks(text)
    if point := cell["candidate_government_point"]:
        yield "GEOGRAPHY", point["source_sha256"], [point["historical_locator"]], None, _geography_checks(point)


def _route_state(path, archived, visible):
    kind, sha, locators, candidate, checks = path
    selection = archived["selection_id"]
    anchors = archived["locator"].split("; ")
    observed = archived.get("observation_id") in checks.get("ORIGINAL", {}).get("governed_observation_ids", ())
    authorized = (archived["value_origin"] in FORMAL_CENSUS_ORIGINS and bool(selection)
                  and archived["source_sha256"] == sha and (observed or any(locator in anchors for locator in locators)))
    formal = _purpose({}, "PASS") if authorized else _purpose({
        **checks, "SELECTION": _check("NOT_RUN", "NO_EXACT_RELEASE_SELECTION_FOR_THIS_ROUTE"),
    }, "PASS")
    # A display record binds the exact same evidence and column. Occupied Census
    # cells mask this overlay and their incumbent evidence remains untouched.
    published = visible and visible["source_sha256"] == sha and visible["locator"] in locators
    if published:
        display = _purpose({}, "PRESENT" if archived["value_state"] == "UNKNOWN" else "EVIDENCE_ONLY")
    elif authorized:
        display = _purpose({}, "PRESENT")
    else:
        relevant = ("ORIGINAL", "CUTOFF", "COLUMN_ROLE", "SCOPE", "CONFLICT")
        display = _purpose({**{key: checks[key] for key in relevant if key in checks},
                            "DISPLAY": _check("NOT_RUN", "NO_MATCHING_SOURCE_VERIFIED_DISPLAY_FACT")}, "PASS")
    return {"kind": kind, "source_sha256": sha, "locators": locators, "candidate_id": candidate,
            "display": display, "formal": formal, "selection_id": selection if authorized else None}


def _best(routes, name):
    def rank(route):
        state = route[name]
        gate = state["first_open_gate"]
        return (len(_ORDER) if gate is None else _ORDER.index(gate), state["status"] in {"PASS", "PRESENT"})

    return max(routes, key=rank)[name]


def _audit_rows(cells, raw, review, baseline, current, old_display, display):
    direct = defaultdict(set)
    for candidate, row in raw.items():
        if column := COLUMNS.get(row["variable"]):
            direct[row["project_id"], column].add(candidate)
    recovered_keys = _keys(cells)
    for key in sorted(recovered_keys | direct.keys()):
        cell, before, after = cells[key], baseline.census[key], current.census[key]
        paths = list(_paths(cell, raw, review, direct))
        routes = [_route_state(path, after, display.get((key[0], after["column_id"], path[3])))
                  for path in paths]
        previous = (key[0], before["column_id"]) in old_display
        display_result, formal = _best(routes, "display"), _best(routes, "formal")
        incumbent = before["value_state"] != "UNKNOWN" or previous
        yield {
            "project_id": key[0], "column": key[1], "column_id": after["column_id"],
            "historical_candidate_cell": key in recovered_keys,
            "historical_method": cell["historical_method"], "baseline_display_blank": not incumbent,
            "incumbent": {
                "before": before["data_role"], "after": after["data_role"],
                "current_value_state": after["value_state"], "current_reason": after["reason"],
            },
            "display": {k: v for k, v in display_result.items() if k != "checks"},
            "formal": {k: v for k, v in formal.items() if k != "checks"},
            "mcda": _criterion_receipt(current, after, {route["source_sha256"] for route in routes}, formal),
            "newly_displayed": not incumbent and display_result["status"] == "PRESENT",
            "routes": routes,
        }


def audit_cohort_admission(*, recovery, database, baseline_recovery, baseline_build, current_build,
                           baseline_display=None, current_display=None, statement_review=None):
    """Account for every recoverable cell using immutable inputs, without writing selections."""
    old, old_hash = _load(baseline_recovery)
    receipt, receipt_hash = _load(recovery)
    old_cells, cells = _indexed_cells(old), _indexed_cells(receipt)
    baseline, current = ResearchBuild.load(baseline_build), ResearchBuild.load(current_build)
    if (set(old_cells) != set(cells) or set(cells) != set(baseline.census) or set(cells) != set(current.census)
            or old["cutoff"] != receipt["cutoff"] or old["timezone"] != receipt["timezone"]
            or any(b.decision_inputs["research_cutoff"] != receipt["cutoff"] for b in (baseline, current))):
        raise ValueError("Recovery and research comparison must share exact axes and research cutoff")
    raw = _raw_records(database, receipt["database_sha256"])
    review, review_hash = _study_reviews(statement_review, raw, receipt)
    _, before, before_hash = _display(baseline_display, baseline)
    after, _, after_hash = _display(current_display, current)
    rows = list(_audit_rows(cells, raw, review, baseline, current, before, after))
    lost = sorted(_keys(old_cells) - _keys(cells))
    gained = sorted(_keys(cells) - _keys(old_cells))
    changed_routes = sorted(key for key in _keys(old_cells) & _keys(cells) if old_cells[key] != cells[key])
    counts = {purpose: dict(Counter(row[purpose]["first_open_gate"] or row[purpose]["status"] for row in rows))
              for purpose in ("display", "formal", "mcda")}
    historical_rows = [row for row in rows if row["historical_candidate_cell"]]
    historical_counts = {
        purpose: dict(Counter(row[purpose]["first_open_gate"] or row[purpose]["status"] for row in historical_rows))
        for purpose in counts
    }
    return {
        "schema": SCHEMA, "status": "PASS", "cutoff": receipt["cutoff"], "timezone": receipt["timezone"],
        "input_sha256": {"baseline_recovery": old_hash, "recovery": receipt_hash, "candidate_sqlite": receipt["database_sha256"],
                         "baseline_build": baseline.receipt_sha, "current_build": current.receipt_sha,
                         "baseline_display": before_hash, "current_display": after_hash, "statement_review": review_hash},
        "policy_versions": {"temporal": TEMPORAL_POLICY_VERSION, "decision": current.decision_inputs["policy_version"],
                            "census_projection_sha256": current.receipt["census_projection_policy_sha256"]},
        "coverage": {"axes": len(cells), "baseline_candidates": len(_keys(old_cells)), "candidates": len(historical_rows),
                     "candidate_projects": len({r["project_id"] for r in historical_rows}),
                     "native_additional_cells": len(rows) - len(historical_rows),
                     "baseline_display_blank": sum(r["baseline_display_blank"] for r in historical_rows),
                     "newly_displayed": sum(r["newly_displayed"] for r in rows),
                     "new_display_projects": len({r["project_id"] for r in rows if r["newly_displayed"]})},
        "baseline_candidate_difference": {"lost": lost, "gained": gained, "changed_routes": changed_routes},
        "first_open_gate_counts": historical_counts, "all_routes_first_open_gate_counts": counts, "cells": rows,
    }
