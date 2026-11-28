"""Independently verify new Census values against exact originals and frozen annotations."""

import argparse
import csv
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess


SHA256 = re.compile(r"[0-9a-f]{64}")
MASS = {"t": 1, "kt": 1000, "Mt": 10**6, "Bt": 10**9, "kt TREO": 1000}
FRACTION = {"ppm": Decimal("0.000001"), "%": Decimal("0.01"), "kg/t": Decimal("0.001")}
NATIVE_FIELDS = (
    "source_sha256", "scope_id", "statement_id", "variable", "raw_value",
    "raw_unit", "value", "unit", "page", "table", "row", "column",
)


def _check(condition, reason):
    if not condition:
        raise ValueError(reason)


def _json(path):
    return json.loads(path.read_bytes())


def _jsonl(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def _number(token):
    return Decimal(token.replace(",", ""))


def _selected(build):
    return {
        row["observation_id"]: row
        for row in _jsonl(build / "observations/observations.jsonl") if row["selected_for_census"]
    }


def _check_coordinates(pdf, coordinates, cache):
    spans = [coordinates["value_anchor"], coordinates["header_anchor"],
             coordinates["unit_anchor"], *coordinates["caption_anchors"]]
    parent = coordinates.get("parent_header_anchor")
    if parent:
        spans.append(parent)
    for span in spans:
        page = span["physical_page"]
        line_number, start, end = (span[key] for key in ("line", "start_column", "end_column"))
        _check(all(type(value) is int and value > 0 for value in (page, line_number, start, end)) and end >= start,
               "Native coordinates must be positive and ordered")
        key = pdf, page
        if key not in cache:
            raw = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
                capture_output=True, check=True, timeout=60,
            ).stdout.decode("utf-8")
            cache[key] = raw.splitlines()
        _check(line_number <= len(cache[key]), "Native line is outside the physical page")
        line = cache[key][line_number - 1]
        _check(end <= len(line), "Native coordinates are outside the physical line")
        actual = line[start - 1:end]
        _check(actual == span["text"], f"Native coordinates differ at {span['locator']}")


def _selected_evidence(current, assets, eligible, new):
    observations = current / "observations"
    report = _json(observations / "extraction.json")
    for name, expected in report["admission_artifact_sha256"].items():
        _check(re.fullmatch(r"[a-z][a-z0-9_]*\.jsonl?", name) and SHA256.fullmatch(expected),
               "Invalid admission artifact name or digest")
        _check(_sha(observations / name) == expected, f"Modified admission artifact {name}")
    cache, results = {}, []
    for case in _jsonl(observations / "admission_decisions.jsonl"):
        if case["verdict"] != "SELECTED":
            continue
        sha = case["edition_sha256"]
        _check(SHA256.fullmatch(sha) and (sha, case["project_id"]) in eligible,
               "Selection uses an unverified Project edition")
        pdf = assets / "objects" / sha
        _check(_sha(pdf) == sha, f"Original bytes disagree with {sha}")
        _check(case["resource_class"] == "MINERAL_RESOURCE" and case["reporting_standard"] == "JORC 2012"
               and case["temporal_decision"]["status"] == "ELIGIBLE"
               and case["effective_date"] and case["publication_date"], "Statement timing or reporting class differs")
        for row in case["observations"]:
            observation_id = row["observation_id"]
            if observation_id not in new:
                continue
            _check(all(new[observation_id][key] == row[key] for key in NATIVE_FIELDS),
                   f"Observation lineage changed {observation_id}")
            locator = row["evidence"]
            _check(row["source_sha256"] == sha and row["outcome"] == "VALIDATED"
                   and row["material_basis"] == "WHOLE_ORE" and row["chemical_basis"] == "TREO"
                   and row["raw_value"] == locator["value_anchor"]["text"]
                   and row["page"] == locator["value_anchor"]["physical_page"],
                   f"Observation lacks exact evidence {observation_id}")
            _check_coordinates(pdf, locator, cache)
            units = MASS if row["variable"] in {"ore_mt", "contained_treo_kt"} else FRACTION
            factor = Decimal(units[row["raw_unit"]]) / Decimal(units[row["unit"]])
            _check(_number(row["raw_value"]) * factor == _number(row["value"]),
                   f"Exact unit conversion differs {observation_id}")
            results.append({
                "project_id": case["project_id"], "observation_id": observation_id,
                "source_url": case["source_url"], "source_sha256": sha, "physical_page": row["page"],
                "table": row["table"], "row": row["row"], "column": row["column"],
                "scope": case["scope"], "scope_id": case["scope_id"], "statement_id": case["statement_id"],
                "temporal_decision_id": case["temporal_decision"]["decision_id"],
                "quantity_kind": row["variable"], "raw_token": row["raw_value"], "raw_unit": row["raw_unit"],
                "normalized_value": row["value"], "normalized_unit": row["unit"],
                "raw_rounding_interval": locator["raw_rounding_interval"],
                "formula": "raw_value * normalization_factor", "normalization_factor": str(factor),
                "category": case["category"], "reporting_standard": case["reporting_standard"],
                "effective_date": case["effective_date"], "publication_date": case["publication_date"],
                "cutoff": case["cutoff"], "treo_members": case["treo_members"],
                "material_basis": row["material_basis"], "chemical_basis": row["chemical_basis"],
                "versioned_use_policy": case["policy_version"], "coordinates": locator["value_anchor"],
            })
    _check(len(results) == len({row["observation_id"] for row in results}) == len(new),
           "New observations do not map one-to-one to decision evidence")
    positions = {(row["source_sha256"], row["physical_page"], row["coordinates"]["line"],
                  row["coordinates"]["start_column"]) for row in results}
    _check(len(positions) == len(results), "A native position was admitted more than once")
    return results, cache


def _check_oracle(current, oracle, rows):
    actual = {(row["project_id"], row["quantity_kind"]): row for row in rows}
    with (current / "census/cell_status.csv").open(newline="", encoding="utf-8") as stream:
        ledger = {(row["project_id"], row["column"]): row for row in csv.DictReader(stream)}
    criteria = _json(current / "decision/mcda_inputs.json")["criteria_by_project"]
    cases = _json(oracle)["cases"]
    _check(len(actual) == sum(len(case["values"]) for case in cases), "Unexpected new scientific values")
    for case in cases:
        project = case["project_id"]
        for expected in case["values"]:
            field = expected["quantity_kind"]
            match = actual[project, field]
            fields = ("source_sha256", "physical_page", "category", "effective_date", "cutoff")
            _check(all(match[key] == case[key] for key in fields), f"Statement differs from the Oracle for {project}")
            fields = ("raw_token", "raw_unit", "normalized_unit")
            _check(all(match[key] == expected[key] for key in fields), f"Native quantity differs for {project} {field}")
            _check(tuple(map(Decimal, match["raw_rounding_interval"]))
                   == tuple(map(Decimal, expected["raw_rounding_interval"])), f"Rounding interval differs for {project}")
            census = ledger[project, expected["column"]]
            _check(census["value_origin"] == "FORMAL_SELECTION"
                   and census["observation_id"] == match["observation_id"]
                   and _number(census["value"]) == _number(expected["census_value"]),
                   f"Census projection differs for {project} {field}")
        _check(ledger[project, "總TREO蘊藏量_kt"]["value_state"] == "UNKNOWN", f"Contained mass leaked for {project}")
        _check(all(criteria[project][code]["state"] == "UNKNOWN" for code in ("R1", "S1")),
               f"Scoped reports leaked into MCDA for {project}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "current", "manifest", "assets", "oracle", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    baseline, current = _selected(args.baseline), _selected(args.current)
    _check(set(baseline) <= set(current), "Previously selected observations were withdrawn")
    new = {key: value for key, value in current.items() if key not in baseline}
    eligible = {(edition["sha256"], project) for edition in _json(args.manifest)["editions"]
                if edition["temporal_decision"]["status"] == "ELIGIBLE" for project in edition["project_ids"]}
    rows, cache = _selected_evidence(args.current, args.assets, eligible, new)
    _check_oracle(args.current, args.oracle, rows)
    receipt = {
        "schema": "independent-native-resource-value-verification-v1", "status": "PASS",
        "baseline_selected": len(baseline), "new_selections_verified": len(rows),
        "decoded_documents": len({key[0] for key in cache}), "decoded_physical_pages": len(cache),
        "source_manifest_sha256": _sha(args.manifest), "oracle_sha256": _sha(args.oracle),
        "observations": rows,
    }
    with args.out.open("x", encoding="utf-8") as output:
        output.write(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(f"PASS — {len(rows)} new values checked against exact originals and frozen annotations")


if __name__ == "__main__":
    main()
