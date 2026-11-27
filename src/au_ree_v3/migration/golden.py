"""Extract archived V2 expectations without invoking V3 research calculations."""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from .baseline import (
    ARCHIVED_CENSUS,
    BASELINES,
    PinnedRepository,
    _coordinates,
    _ensure_output_outside_reference,
    _status,
    _write_json,
    asset_inventory,
    checked_csv,
    pinned_inputs,
)


GENERATOR = "frozen-v2-golden-v1"
GOLDEN_PROJECTS = {
    "AU-9bd8e5ed8408cd84d876": "Wolverine scoped total and distinct DFS component",
    "AU-efff91a06cf344122d88": "Mt Weld published precision and unselected rounded table",
    "AU-8f3fe174a998d20ee4cf": "Mangaroon Yin separated from Gifford Creek and Exploration Targets",
    "AU-216d0983f6892f9b58f9": "North Stanmore oxide basis and different effective periods",
    "AU-574465211976ec57b1ef": "Splinter Ce2O3 conversion and missing source",
    "AU-ff758cad5beb276d327d": "Wimmera heavy minerals distinct from bulk ore denominator",
}
CENSUS_FIELDS = {
    "礦石蘊藏量_Mt": "ore_mt",
    "TREO品位_pct": "treo_grade_pct",
    "總TREO蘊藏量_kt": "contained_treo_kt",
}
OBSERVATION_FIELDS = {
    "project_id": "project_id",
    "observation_id": "observation_id",
    "statement_id": "statement_id",
    "scope": "scope",
    "category": "category",
    "reporting_standard": "reporting_standard",
    "cutoff_grade": "cutoff",
    "effective_date": "effective_date",
    "effective_date_precision": "effective_date_precision",
    "publication_date": "publication_date",
    "publication_date_precision": "publication_date_precision",
    "acquired_at": "acquired_at",
    "selection_role": "selection_role",
    "variable": "variable",
    "value": "value",
    "unit": "unit",
    "basis": "basis",
    "material_basis": "material_basis",
    "locator": "locator",
    "transform_version": "transform_version",
    "raw_value": "original_token",
    "raw_unit": "original_unit",
    "denominator_basis": "denominator_basis",
    "rounding_half_width": "rounding_half_width",
    "source_sha256": "source_sha256",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _jsonl(path: Path, rows: list[dict], *, immutable: bool = False) -> str:
    payload = b"".join(_canonical_json(row) + b"\n" for row in rows)
    if immutable and path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"Refusing to overwrite frozen expectations at {path}")
    else:
        path.write_bytes(payload)
    return sha256(payload).hexdigest()


def _observation_values(row: dict, cutoff: str, source: dict, decision: dict) -> dict:
    return {**{key: row[column] or None for key, column in OBSERVATION_FIELDS.items()},
            "research_cutoff": cutoff,
            "selected_for_census": bool(int(row["selected_for_census"])),
            "physical_page": int(row["physical_page"]) if row["physical_page"] else None,
            "source_url": source["original_url"],
            "available_date": decision["available_date"] or None}


def _census_values(project_id: str, field: str, cutoff: str, state: dict, provenance: dict) -> dict:
    return {
        "project_id": project_id,
        "research_cutoff": cutoff,
        "field": field,
        "value": provenance["value"] or None,
        "value_state": state["value_state"],
        "status": state["status"],
        "source_sha256": provenance["source_sha256"] or None,
        "locator": provenance["locator"] or None,
        "observation_ids": json.loads(provenance["observation_ids"]),
        "effective_date": provenance["effective_date"] or None,
        "scope": provenance["scope"] or None,
    }


def build_golden(v2_root: Path, out: Path, assets: Path | None = None) -> dict:
    """Generate reviewable reference rows and separately report current source availability."""
    _ensure_output_outside_reference(v2_root, out)
    repo = PinnedRepository(v2_root)
    inputs, checks = pinned_inputs(repo, ARCHIVED_CENSUS)
    status = _status(checks)
    cutoff = BASELINES["profiles"][ARCHIVED_CENSUS]["cutoff"]
    manifest = {"schema": GENERATOR, "cutoff": cutoff, "status": status, "checks": checks, "cases": []}
    if status != "PASS":
        manifest["reason"] = "PINNED_V2_INPUTS_UNVERIFIED"
        _write_json(out / "golden_manifest.json", manifest)
        return manifest

    archive = BASELINES["archive_path"]
    profile = ARCHIVED_CENSUS
    recovery_path = f"{BASELINES['audit_path']}/source_recovery_manifest.json"
    recovery = json.loads(inputs["engineering", recovery_path])
    records = {row["expected_sha256"]: row for row in recovery["assets"]}
    inventory = {row["expected_sha256"]: row for row in asset_inventory(repo, set(records), recovery, assets)}
    cases: list[dict] = []
    expected: list[dict] = []

    def append(case_id: str, values: dict, paths: list[str], source_shas: list[str], scenario: str,
               *, tier: str = "FROZEN_V2_OUTPUT") -> None:
        refs = [
            {"path": path, "commit": BASELINES["refs"]["engineering"],
             "sha256": sha256(inputs["engineering", path]).hexdigest()}
            for path in paths
        ]
        locked = []
        for sha in source_shas:
            receipt = inventory.get(sha)
            record = records.get(sha)
            locked.append({
                "sha256": sha,
                "availability": receipt["status"] if receipt else "MISSING",
                "locked_sources": record["locked_sources"] if record else [],
            })
        identity_fields = ("project_id", "observation_id", "statement_id", "scope", "locator", "physical_page")
        case = {
            "case_id": case_id, "scenario": scenario, "synthetic": False, "research_cutoff": cutoff,
            "source_assets": locked,
            "source_verification": "PASS" if locked and all(x["availability"] == "EXACT_SHA" for x in locked) else "BLOCKED",
            "expected_sources": refs, "comparison_rule": "EXACT",
            **{field: values[field] for field in identity_fields if field in values},
        }
        case["input_sha256"] = sha256(_canonical_json({k: v for k, v in case.items()
                                                       if k not in {"source_verification", "source_assets"}})
                                          + _canonical_json(source_shas)).hexdigest()
        cases.append(case)
        expected.append({"case_id": case_id, "expected_source": {"files": refs},
                         "reference_sha256": refs[0]["sha256"], "evidence_tier": tier, "values": values})

    observations_path = f"{archive}/resource_statement_observations.csv"
    selection_path = f"{archive}/source_selection.csv"
    observations = checked_csv(inputs["engineering", observations_path])
    decisions = defaultdict(list)
    for row in checked_csv(inputs["engineering", selection_path]):
        decisions[row["source_sha256"]].append(row)

    for row in observations:
        project_id = row["project_id"]
        if project_id not in GOLDEN_PROJECTS:
            continue
        observation_id = row["observation_id"]
        sha = row["source_sha256"]
        locked = records[sha]["locked_sources"]
        by_key = {source["source_key"]: source for source in locked}
        decision = next((item for item in decisions[sha] if item["key"] in by_key), None)
        if decision:
            source = by_key[decision["key"]]
        else:
            source = next((item for item in locked if item["historical_acquired_at"] == row["acquired_at"]), locked[0])
            decision = source
        append(f"{profile}:observation:{observation_id}", _observation_values(row, cutoff, source, decision),
               [observations_path, selection_path, recovery_path], [sha], GOLDEN_PROJECTS[project_id])

    status_path = f"{archive}/field_status.csv"
    provenance_path = f"{archive}/field_provenance.csv"
    census_path = f"{archive}/REE project census.csv"
    states = _coordinates(checked_csv(inputs["engineering", status_path]))
    census = {row["專案ID"]: row for row in checked_csv(inputs["engineering", census_path])}
    provenance = _coordinates(checked_csv(inputs["engineering", provenance_path]))
    for project_id, scenario in GOLDEN_PROJECTS.items():
        for column, field in CENSUS_FIELDS.items():
            coordinate = (project_id, column)
            ledger = provenance[coordinate]
            if census[project_id][column] != ledger["value"]:
                raise ValueError(f"Census and provenance differ for {coordinate!r}")
            values = _census_values(project_id, field, cutoff, states[coordinate], ledger)
            append(f"{profile}:census:{project_id}:{field}", values,
                   [provenance_path, status_path, census_path], ledger["source_sha256"].split(";") if ledger["source_sha256"] else [],
                   scenario)

    for sha, historical in sorted(recovery["missing_assets"].items()):
        append(f"{profile}:missing-source:{sha}",
               {"source_sha256": sha, "research_cutoff": cutoff,
                "historical_recovery_status": records[sha]["status"],
                "historical_recovery_reason": historical["reason"],
                "historical_distinct_sha256": historical.get("distinct_sha256", [])},
               [recovery_path], [sha], "V2 historical recovery recorded unavailable original",
               tier="FROZEN_V2_SOURCE_RECOVERY_RECEIPT")

    cases.sort(key=lambda row: row["case_id"])
    expected.sort(key=lambda row: row["case_id"])
    ids = {row["case_id"] for row in expected}
    if len(ids) != len(expected):
        raise ValueError("Duplicate Golden case IDs in frozen source artifacts")
    expected_path = out / "golden_expected.jsonl"
    expected_sha = _jsonl(expected_path, expected, immutable=True)
    cases_path = out / "golden_cases.jsonl"
    cases_sha = _jsonl(cases_path, cases)
    _write_json(out / "oracle_manifest.json", {
        "schema": GENERATOR,
        "reference_scope": "FROZEN_V2_MIGRATION_EQUIVALENCE",
        "cutoff": cutoff, "ref": BASELINES["refs"]["engineering"], "science_ref": BASELINES["refs"]["science"],
        "expected_file": {"path": expected_path.name, "sha256": expected_sha},
        "cases_file": {"path": cases_path.name, "sha256": cases_sha},
        "case_count": len(cases), "evidence_tiers": dict(Counter(row["evidence_tier"] for row in expected)),
        "comparisons": "Exact typed values and lexical decimal strings. Blank exported fields map to null.",
    })
    manifest.update({
        "cases": cases,
        "archived_reference_cases": len(cases),
        "source_verification": dict(Counter(case["source_verification"] for case in cases)),
        "source_assets": {
            key: sorted({entry["sha256"] for row in cases for entry in row["source_assets"] if entry["availability"] == key})
            for key in ("EXACT_SHA", "MISSING", "WRONG_SHA")
        },
        "external_scientific_accuracy": "UNMEASURED",
    })
    # Temporal governance is a parallel result; historical reference bytes stay unchanged.
    from .temporal_audit import _audit_pinned_temporal
    temporal_out = out / "temporal"
    _ensure_output_outside_reference(v2_root, temporal_out)
    temporal = _audit_pinned_temporal(repo, expected_path, out / "oracle_manifest.json", temporal_out, inputs, checks)
    manifest["temporal_audit"] = {"status": temporal["status"], "directory": "temporal"}
    _write_json(out / "golden_manifest.json", manifest)
    return manifest
