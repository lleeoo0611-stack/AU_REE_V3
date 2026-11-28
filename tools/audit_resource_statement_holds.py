"""Recheck manually annotated resource statement holds on exact published bytes."""

import argparse
from collections import defaultdict
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from au_ree_v3.census_resource_display import _original_phrase
from au_ree_v3.comparability import TOTAL_REO_15
from au_ree_v3.documents import DocumentCache
from au_ree_v3.fresh_research import _verified_edition
from au_ree_v3.normalization import reported_interval
from au_ree_v3.registry import load_profile
from au_ree_v3.resource_extraction import _chemical_species
from au_ree_v3.sources import ContentStore

ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS = ROOT / "tests/fixtures/held_resource_statements_v1.json"
COHORT_REVIEW_SCHEMA = "historical-resource-cohort-statement-review-v1"


def audit(profile, manifest, assets, build):
    raw_manifest = manifest.read_bytes()
    sources = json.loads(raw_manifest)
    expected = json.loads(EXPECTATIONS.read_bytes())
    observed = (json.loads(line) for line in (build / "observations/observations.jsonl").read_bytes().splitlines())
    selected = {(row["project_id"], row["source_sha256"]) for row in observed if row["selected_for_census"]}
    events = {row["receipt_sha256"]: row for row in sources["events"] if row["status"] == "PUBLIC_CAPTURED"}
    settings = load_profile(profile)
    content = ContentStore(assets)
    cache = DocumentCache()
    checked = []
    for case in expected["cases"]:
        source, project = case["source_sha256"], case["project_id"]
        edition, = [row for row in sources["editions"] if row["sha256"] == source and project in row["project_ids"]]
        capture = edition["receipt_sha256"]
        temporal = _verified_edition(settings, edition, events[capture], content)
        if temporal.status != "ELIGIBLE" or (project, source) in selected:
            raise ValueError(f"Exact held statement was selected or lost its capture proof: {case['case']}")
        pdf = cache.pdf(content.read(source), source, max(page for page, _ in case["witnesses"]))
        evidence = []
        for page, quote in case["witnesses"]:
            anchor = _original_phrase(pdf, page, quote)
            if anchor is None:
                raise ValueError(f"Independent native evidence cannot be located: {case['case']}, page {page}")
            evidence.append({"physical_page": anchor.page, "locator": anchor.locator, "quote": anchor.text})
        checked.append({**{key: value for key, value in case.items() if key != "witnesses"},
                        "capture_sha256": capture, "temporal_decision_id": temporal.decision_id, "evidence": evidence})
    return {"schema": expected["schema"], "status": "PASS", "source_manifest_sha256": sha256(raw_manifest).hexdigest(),
            "known_scope_negative_count": len(checked), "formal_admission_false_positives": 0,
            "scope": "NAMED_ORIGINAL_NEGATIVES_ONLY", "generalized_admission_precision_recall": "UNMEASURED",
            "cases": checked}


def _checked_pdf(case, files, project, profile, cache):
    manifest, events, content = files[case["input"]]
    digest, capture = case["source_sha256"], case["capture_sha256"]
    edition, = (item for item in manifest["editions"]
                if item["sha256"] == digest and item["receipt_sha256"] == capture
                and project in item["project_ids"] and item["kind"] == "pdf")
    temporal = _verified_edition(profile, edition, events[capture], content)
    if temporal.status != "ELIGIBLE":
        raise ValueError("Scoped resource witness lacks exact public evidence by cutoff")
    pdf = cache.pdf(content.read(digest), digest, max(row["page"] for row in case["witnesses"]))
    anchors = []
    for row in case["witnesses"]:
        anchor = _original_phrase(pdf, row["page"], row["quote"])
        if anchor is None:
            raise ValueError(f"Scoped resource witness absent: {row['id']}, physical page {row['page']}")
        anchors.append({"id": row["id"], "physical_page": anchor.page, "locator": anchor.locator, "quote": anchor.text})
    return {"source_sha256": digest, "capture_sha256": capture, "source_url": edition["url"],
            "source_kind": edition["kind"], "publication_date": case["publication_date"],
            "statement_role": case["statement_role"], "temporal_decision": temporal.to_dict(),
            "witnesses": anchors, "pages_decoded": len(pdf.pages)}


def _decimal_bounds(annotation, quotes):
    token = annotation["token"]
    unit_token = annotation["native_unit_token"]
    if not re.search(rf"(?<![0-9.,]){re.escape(token)}(?![0-9.,])", quotes[annotation["witness"]]):
        raise ValueError("Decimal comparison token is absent from its exact native witness")
    if not re.search(rf"(?<![A-Za-z]){re.escape(unit_token)}(?![A-Za-z])", quotes[annotation["unit_witness"]]):
        raise ValueError("Decimal comparison unit is absent from its exact native header")
    scale = {"t": Decimal("0.001"), "kt": Decimal(1), "Mt": Decimal(1),
             "%": Decimal(1), "ppm": Decimal("0.0001")}[annotation["unit"]]
    center = Decimal(token.replace(",", ""))
    step = Decimal(annotation["printed_step"])
    if not (center.is_finite() and center > 0 and step.is_finite() and step > 0):
        raise ValueError("Decimal comparison requires positive finite source precision")
    return (center - step / 2) * scale, (center + step / 2) * scale


def _arithmetic(case, quotes):
    results = []
    for check in case["decimal_checks"]:
        units = tuple(item["unit"] for item in check["inputs"])
        values = [_decimal_bounds(item, quotes) for item in check["inputs"]]
        if check["kind"] == "REPORTED_MASS_INTERSECTION" and len(units) == 2 and set(units) <= {"t", "kt"}:
            left, right = values
        elif (check["kind"] == "ORE_GRADE_MASS_INTERSECTION" and len(units) == 3
              and units[0] == "Mt" and units[1] in {"%", "ppm"} and units[2] in {"t", "kt"}):
            ore, grade, right = values
            left = ore[0] * grade[0] * 10, ore[1] * grade[1] * 10
        else:
            raise ValueError("Unknown independent resource arithmetic check")
        intersects = max(left[0], right[0]) <= min(left[1], right[1])
        if intersects != check["expected_intersection"]:
            raise ValueError("Independent resource rounding result differs from the reviewed oracle")
        results.append({**check, "left_bounds_kt": [format(value, "f") for value in left],
                        "right_bounds_kt": [format(value, "f") for value in right], "intersects": intersects})
    return results


def audit_comparability(profile_path, manifest, assets, build, challenge, annexes):
    """Replay fixed and independently captured scoped negatives against the locked R1 policy."""
    profile = load_profile(profile_path)
    spec = json.loads(challenge.read_bytes())
    if spec["schema"] != "resource-comparability-challenge-v1":
        raise ValueError("Unknown resource comparability challenge schema")
    files = {}
    for label, path, store in [("fixed", manifest, assets), *annexes]:
        raw = Path(path).read_bytes()
        payload = json.loads(raw)
        if (label in files or sha256(raw).hexdigest() != spec["manifest_sha256"][label]
                or label != "fixed" and payload.get("base_manifest_sha256") != spec["manifest_sha256"]["fixed"]):
            raise ValueError("Resource challenge requires its exact source manifest scope")
        events = {row["receipt_sha256"]: row for row in payload["events"] if row["status"] == "PUBLIC_CAPTURED"}
        files[label] = payload, events, ContentStore(Path(store))
    if files.keys() != spec["manifest_sha256"].keys():
        raise ValueError("Missing required resource challenge source manifests")
    inputs = (build / "decision/mcda_inputs.json").read_bytes()
    table = json.loads(inputs)
    report = json.loads((build / "build_receipt.json").read_bytes())
    inputs_sha = sha256(inputs).hexdigest()
    if (report["mcda_inputs_hash"] != inputs_sha or report["input_artifacts"]["sources"] != spec["manifest_sha256"]["fixed"]
            or table["policy_sha256"] != spec["policy_sha256"] or table["research_cutoff"] != profile.research_cutoff.isoformat()
            or table["research_timezone"] != profile.timezone.key):
        raise ValueError("Resource challenge requires the locked R1 method and date")
    cache, results = DocumentCache(), []
    for case in spec["cases"]:
        project = case["project_id"]
        criterion = table["criteria_by_project"][project]["R1"]
        documents = [_checked_pdf(edition, files, project, profile, cache) for edition in case["editions"]]
        held_shas = {document["source_sha256"] for document in documents}
        if any(row["source_sha256"] in held_shas for row in criterion["lineage"]):
            raise ValueError("A held original edition has entered formal R1 lineage")
        anchors = [row for document in documents for row in document["witnesses"]]
        quotes = {row["id"]: row["quote"] for row in anchors}
        if len(quotes) != len(anchors):
            raise ValueError("Scoped resource witness identities must be unique within a case")
        results.append({**{key: value for key, value in case.items() if key not in {"editions", "decimal_checks"}},
                        "editions": documents, "decimal_checks": _arithmetic(case, quotes),
                        "current_r1_state": criterion["state"], "current_r1_reason": criterion["reason"],
                        "formal_admission": False})
    return {"schema": spec["schema"], "status": "PASS", "scope": "NAMED_ORIGINAL_NEGATIVES_ONLY",
            "challenges_sha256": sha256(challenge.read_bytes()).hexdigest(), "manifest_sha256": spec["manifest_sha256"],
            "policy_sha256": table["policy_sha256"], "mcda_inputs_sha256": inputs_sha,
            "known_scope_negative_count": len(results), "formal_admission_false_positives": 0,
            "pdf_decodes": sum(len(row["editions"]) for row in results),
            "pdf_pages_decoded": sum(e["pages_decoded"] for row in results for e in row["editions"]),
            "generalized_admission_precision_recall": "UNMEASURED", "cases": results}


def _exact_recovered_rows(database, recovery):
    database_sha = sha256(database.read_bytes()).hexdigest()
    if recovery.get("database_sha256") != database_sha or recovery.get("status") != "PASS":
        raise ValueError("Statement review requires its exact successful recovery database")
    rows = defaultdict(list)
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro&immutable=1", uri=True)) as store:
        for (payload,) in store.execute("SELECT payload FROM raw_observations ORDER BY observation_id"):
            raw = json.loads(payload)
            native = json.loads(raw["original_payload"])
            key = raw["project_id"], raw["source_sha"], native["physical_page"], native["row_anchor"]["locator"]
            rows[key].append((raw, native))
    if sum(map(len, rows.values())) != recovery["counts"]["candidate_positions"]:
        raise ValueError("Reviewed native records differ from the recovery receipt")
    return rows


def _exact_recovered_cell(pdf, raw, native):
    for role in ("value_anchor", "header_anchor", "unit_anchor"):
        span = native[role]
        actual = pdf.locate(span["physical_page"], line=span["line"], column=span["start_column"],
                            end_column=span["end_column"])
        if actual.locator != span["locator"] or actual.text.strip() != span["text"].strip():
            raise ValueError(f"Token/header/unit cannot be reproduced at {span['locator']}")
    value = native["value_anchor"]
    if raw["raw_value"] != value["text"] or raw["raw_unit"] != native["native_unit"]:
        raise ValueError("Original raw value or unit differs from the exact native binding")
    _, low, high = reported_interval(value["text"])
    if [Decimal(v) for v in native["raw_rounding_interval"]] != [low, high]:
        raise ValueError(f"Printed token precision differs at {value['locator']}")
    return {
        "observation_id": raw["observation_id"], "variable": raw["variable"],
        "raw_value": raw["raw_value"], "raw_unit": raw["raw_unit"],
        "checks": {
            "COLUMN_ROLE": _check("PASS", "EXACT_NATIVE_VALUE_HEADER_AND_UNIT", [
                native["value_anchor"]["locator"], native["header_anchor"]["locator"],
                native["unit_anchor"]["locator"],
            ]),
            "PRECISION": _check("PASS", "LEXICAL_ROUNDING_INTERVAL_REPRODUCED",
                                [value["locator"]]),
        },
    }


def _check(status, reason, proof):
    if status not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"} or (status in {"PASS", "FAIL"} and not proof):
        raise ValueError("A tested statement decision requires exact original evidence")
    return {"status": status, "reason": reason, "proof": proof}


def _same_printed_row(case, native):
    row_label = " ".join(case["row"].split()).casefold()
    row_text = " ".join(native["row_anchor"]["text"].split()).casefold()
    table_label = " ".join(case["table"].split()).casefold()
    caption = " ".join(" ".join(span["text"].split()) for span in native["caption_anchors"]).casefold()
    return bool(row_label and row_text.startswith(row_label + " ") and table_label
                and re.search(rf"(?<!\w){re.escape(table_label)}(?!\w)", caption))


def _governed_joins(observation, native, cases):
    result = []
    for case in cases:
        if (case["variable"], case["raw_unit"]) != (observation["variable"], observation["raw_unit"]):
            continue
        if (not _same_printed_row(case, native)
                or Decimal(case["raw_value"].replace(",", "")) != Decimal(observation["raw_value"].replace(",", ""))):
            continue
        result.append({name: case[name] for name in (
            "observation_id", "outcome", "reason", "selected_for_census", "scope_id", "statement_id"
        )})
    return result


def _cohort_assertions(case, pdf):
    anchors = {}
    for role, (page, quote) in case["witnesses"].items():
        original = _original_phrase(pdf, page, quote)
        if not original:
            raise ValueError(f"{case['case_id']} {role}: witness must exist uniquely on PDF page {page}")
        anchors[role] = {"physical_page": original.page, "locator": original.locator, "quote": original.text}
    results = {gate: _check(status, reason, [anchors[name] for name in proof]) for gate, (
        status, reason, proof
    ) in case["findings"].items()}
    chemistry = results.get("CHEMICAL_BASIS")
    if chemistry and chemistry["reason"] == "EXACT_FIFTEEN_TREO_MEMBER_DEFINITION":
        members = _chemical_species((anchors["definition"]["quote"],))
        if set(members) != set(TOTAL_REO_15):
            raise ValueError(f"{case['case_id']}: original TREO definition differs from the strict member set")
        chemistry["members"] = list(members)
    return results


def audit_recovered_statements(profile, manifest, assets, build, recovery, database, review):
    """Replay statement-level proof for every originally recovered numeric row."""
    review_bytes = review.read_bytes()
    spec = json.loads(review_bytes)
    manifest_bytes = manifest.read_bytes()
    manifest_sha = sha256(manifest_bytes).hexdigest()
    recovery_bytes = recovery.read_bytes()
    recovered = json.loads(recovery_bytes)
    recovery_sha = sha256(recovery_bytes).hexdigest()
    if (spec["schema"] != COHORT_REVIEW_SCHEMA or recovered["source_manifest_sha256"] != manifest_sha
            or recovered["database_sha256"] != spec["database_sha256"]
            or (recovered["cutoff"], recovered["timezone"]) != (spec["cutoff"], spec["timezone"])):
        raise ValueError("Statement review input identity differs from the frozen cohort")
    rows = _exact_recovered_rows(database, recovered)
    settings = load_profile(profile)
    sources = json.loads(manifest_bytes)
    events = {row["receipt_sha256"]: row for row in sources["events"] if row["status"] == "PUBLIC_CAPTURED"}
    content, cache = ContentStore(assets), DocumentCache()
    ledger = defaultdict(list)
    build_receipt = json.loads((build / "build_receipt.json").read_bytes())
    observation_path = "observations/observations.jsonl"
    governed_bytes = (build / observation_path).read_bytes()
    if (build_receipt["input_artifacts"]["sources"] != manifest_sha
            or build_receipt["scientific_artifacts"][observation_path] != sha256(governed_bytes).hexdigest()):
        raise ValueError("Governed observations belong to a different source manifest")
    for record in map(json.loads, governed_bytes.splitlines()):
        ledger[record["project_id"], record["source_sha256"], record["page"]].append(record)
    out = []
    for case in spec["records"]:
        project, source, page, row = (case[key] for key in (
            "project_id", "source_sha256", "physical_page", "row_locator"
        ))
        entries = rows.pop((project, source, page, row))
        captures = {native["source_capture_sha256"] for _, native in entries}
        edition, = [row for row in sources["editions"] if row["sha256"] == source and project in row["project_ids"]
                    and row["receipt_sha256"] in captures]
        capture = edition["receipt_sha256"]
        temporal = _verified_edition(settings, edition, events[capture], content)
        if temporal.status != "ELIGIBLE":
            raise ValueError(f"Source is no longer cutoff-eligible: {case['case_id']}")
        pdf = cache.pdf(content.read(source), source, max(page, *(p for p, _ in case["witnesses"].values())))
        exact_row = pdf.locate(page, line=int(row.rsplit(" ", 1)[-1]))
        if any(row != exact_row.locator or native["row_anchor"]["text"] != exact_row.text for _, native in entries):
            raise ValueError(f"Exact native resource row differs: {case['case_id']}")
        observations = []
        for raw, native in entries:
            observation = _exact_recovered_cell(pdf, raw, native)
            observation["governed_matches"] = _governed_joins(observation, native, ledger[project, source, page])
            observations.append(observation)
        out.append({**{key: value for key, value in case.items() if key not in {"findings", "witnesses"}},
                    "capture_sha256": capture, "temporal_decision_id": temporal.decision_id,
                    "checks": _cohort_assertions(case, pdf), "observations": observations})
    if rows:
        raise ValueError("Statement decisions omit originally recovered numeric rows")
    return {"schema": COHORT_REVIEW_SCHEMA, "status": "PASS", "source_manifest_sha256": manifest_sha,
            "recovery_sha256": recovery_sha, "database_sha256": recovered["database_sha256"],
            "review_sha256": sha256(review_bytes).hexdigest(), "governed_build_code_commit": build_receipt["code_commit"],
            "governed_observations_sha256": sha256(governed_bytes).hexdigest(), "reviewed_statement_rows": len(out),
            "observation_count": sum(len(record["observations"]) for record in out), "records": out}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "manifest", "assets", "build", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--challenge", type=Path, help="Replay a scoped resource comparability challenge")
    parser.add_argument("--annex", nargs=3, action="append", default=[], metavar=("LABEL", "MANIFEST", "CAS"))
    parser.add_argument("--recovered-statements", nargs=3, type=Path, metavar=("RECOVERY", "SQLITE", "REVIEW"),
                        help="Replay the original numeric cohort statement evidence")
    args = parser.parse_args()
    if args.recovered_statements:
        result = audit_recovered_statements(args.profile, args.manifest, args.assets, args.build,
                                            *args.recovered_statements)
    elif args.challenge:
        result = audit_comparability(args.profile, args.manifest, args.assets, args.build, args.challenge, args.annex)
    else:
        result = audit(args.profile, args.manifest, args.assets, args.build)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    count = result.get("reviewed_statement_rows", result.get("known_scope_negative_count"))
    print(json.dumps({"status": result["status"], "cases": count}))


if __name__ == "__main__":
    main()
