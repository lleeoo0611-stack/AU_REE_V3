"""Verify immutable V2 references and enumerate source bytes available to this run."""

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from hashlib import file_digest, sha256
from importlib.resources import files
import io
import json
from pathlib import Path
import platform
import re
import subprocess
from time import perf_counter

from .source_temporal import source_temporal_inventory


ARCHIVED_CENSUS = "archived-census-2026-11-13"
PHASE22_SUBSET = "phase22-2026-11-05"
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")
BASELINES = json.loads(files(__package__).joinpath("baselines_v1.json").read_bytes())


@dataclass(frozen=True)
class PinnedRepository:
    root: Path

    def git(self, *args: str) -> bytes | None:
        process = subprocess.run(
            ["git", "--no-lazy-fetch", "-C", str(self.root), *args],
            capture_output=True,
            check=False,
        )
        return process.stdout if process.returncode == 0 else None

    def resolve(self, object_name: str) -> str | None:
        value = self.git("rev-parse", "--verify", object_name)
        return value.decode("ascii").strip() if value is not None else None

    def read(self, role: str, path: str) -> bytes | None:
        return self.git("cat-file", "blob", f"{BASELINES['refs'][role]}:{path}")


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def checked_csv(data: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("Invalid or duplicate CSV headers")
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("CSV row width disagrees with header width")
    return rows


def _coordinates(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    indexed = {(row["project_id"], row["column"]): row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate Census coordinates")
    return indexed


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ensure_output_outside_reference(reference: Path, out: Path) -> None:
    if out.resolve().is_relative_to(reference.resolve()):
        raise ValueError("Write migration reports outside the V2 reference checkout")
    out.mkdir(parents=True, exist_ok=True)


def _check(name: str, expected: object, actual: object, *, path: str | None = None) -> dict:
    return {
        "name": name,
        "status": "BLOCKED" if actual is None else "PASS" if expected == actual else "FAIL",
        "expected": expected,
        "actual": actual,
        "evidence_path": path,
    }


def _status(checks: list[dict]) -> str:
    statuses = {check["status"] for check in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    return "PASS"


def pinned_inputs(repo: PinnedRepository, profile: str) -> tuple[dict[tuple[str, str], bytes], list[dict]]:
    """Read from exact commit objects. Paths in the working tree cannot override artifacts."""
    checks = []
    inputs = {}
    for role, sha in BASELINES["refs"].items():
        checks.append(_check(f"ref:{role}", sha, repo.resolve(f"{sha}^{{commit}}")))
    expected_inputs = BASELINES["pins"]["common"] + BASELINES["pins"][profile]
    for pin in expected_inputs:
        role, path = pin["ref"], pin["path"]
        data = repo.read(role, path)
        actual = None if data is None else {
            "sha256": _sha(data),
            "git_blob_sha": repo.resolve(f"{BASELINES['refs'][role]}:{path}"),
        }
        expected = {"sha256": pin["sha256"], "git_blob_sha": pin["git_blob_sha"]}
        check = _check(f"pinned:{role}:{path}", expected, actual, path=path)
        checks.append(check)
        if check["status"] == "PASS":
            inputs[role, path] = data
    return inputs, checks


def _candidate_assets(root: Path, assets: Path | None) -> dict[str, list[tuple[str, Path]]]:
    locations = [("reference_checkout", root / "data/snapshots")]
    if assets is not None:
        locations.extend([
            ("asset_library", assets),
            ("asset_library", assets / "assets"),
            ("attempted_download", assets / "different-editions"),
        ])
    index = defaultdict(list)
    for location, directory in locations:
        for path in sorted(directory.glob("*")):
            expected = path.stem.split("-")[0] if location == "attempted_download" else path.stem
            if SHA256_PATTERN.fullmatch(expected) and path.is_file():
                index[expected].append((location, path))
    return index


def asset_inventory(repo: PinnedRepository, required: set[str], recovery: dict, assets: Path | None) -> list[dict]:
    candidates = _candidate_assets(repo.root, assets)
    inventory = []
    for record in recovery["assets"]:
        expected = record["expected_sha256"]
        if not SHA256_PATTERN.fullmatch(expected):
            raise ValueError("Invalid SHA-256 in frozen recovery manifest")
        receipt = {
            "expected_sha256": expected,
            "status": "NOT_REQUIRED_FOR_SCOPE",
            "locked_sources": record["locked_sources"],
            "affected_projects_in_v2": record["project_ids_in_historical_lineage"],
            "historical_v2_status": record["status"],
            "examined": [],
        }
        if expected in required:
            receipt["status"] = "MISSING"
            for kind, path in candidates.get(expected, ()):
                with path.open("rb") as stream:
                    observed = file_digest(stream, "sha256").hexdigest()
                receipt["examined"].append({"location": kind, "file": str(path), "actual_sha256": observed})
                if observed == expected:
                    receipt["status"] = "EXACT_SHA"
                    break
                receipt["status"] = "WRONG_SHA"
        inventory.append(receipt)
    return sorted(inventory, key=lambda row: row["expected_sha256"])


def census_counts(
    census_data: bytes,
    status_data: bytes,
    provenance_data: bytes,
    previous_status_data: bytes,
    gaps_data: bytes,
    selected_ids: list[str],
) -> dict:
    census = checked_csv(census_data)
    if not census:
        raise ValueError("Census is empty")
    ids = [row["專案ID"] for row in census]
    columns = list(census[0])
    if len(set(ids)) != len(ids) or sorted(ids) != sorted(selected_ids):
        raise ValueError("Census cohort disagrees with release or contains duplicate IDs")
    values = {(row["專案ID"], column): row[column] for row in census for column in columns}
    status = _coordinates(checked_csv(status_data))
    provenance = _coordinates(checked_csv(provenance_data))
    previous = _coordinates(checked_csv(previous_status_data))
    gaps = _coordinates(checked_csv(gaps_data))
    if values.keys() != status.keys() or values.keys() != provenance.keys() or values.keys() != previous.keys():
        raise ValueError("Census, status, provenance and prior denominator coordinates disagree")
    states = Counter()
    status_counts = Counter()
    current_applicable = set()
    frozen_applicable = set()
    for key, value in values.items():
        state = status[key]["value_state"]
        status_counts[status[key]["status"]] += 1
        if value != provenance[key]["value"] or bool(value) != (state == "PRESENT"):
            raise ValueError(f"Inconsistent Census value, status or provenance for {key!r}")
        not_applicable = status[key]["status"].startswith("NOT_APPLICABLE_")
        if not_applicable and value:
            raise ValueError(f"Value present in an inapplicable Census cell {key!r}")
        states["NOT_APPLICABLE" if not_applicable else "PRESENT" if value else "MISSING_APPLICABLE"] += 1
        if not not_applicable:
            current_applicable.add(key)
        if not previous[key]["status"].startswith("NOT_APPLICABLE_"):
            frozen_applicable.add(key)

    historical_gaps = {key for key in frozen_applicable if previous[key]["status"] != "VALUE_PRESENT"}
    if gaps.keys() != historical_gaps:
        raise ValueError("Gap review does not cover the frozen baseline's applicable gaps")
    for key, gap in gaps.items():
        if gap["current_status"] != status[key]["status"] or gap["current_value"] != values[key]:
            raise ValueError(f"Gap review differs from the released cell {key!r}")

    historically_present = sum(bool(values[key]) for key in frozen_applicable)
    current_coverage = Decimal(states["PRESENT"]) / len(current_applicable)
    historical_coverage = Decimal(historically_present) / len(frozen_applicable)
    return {
        "projects": len(ids),
        "columns": len(columns),
        "column_names": columns,
        "total_cells": len(values),
        "present": states["PRESENT"],
        "not_applicable": states["NOT_APPLICABLE"],
        "missing_applicable": states["MISSING_APPLICABLE"],
        "applicable": len(current_applicable),
        "historical_frozen_denominator": len(frozen_applicable),
        "filled_in_historical_denominator": historically_present,
        "historical_applicable_now_excluded": len(frozen_applicable - current_applicable),
        "historical_fixed_coverage_pct": f"{historical_coverage:.2%}",
        "current_coverage_pct": f"{current_coverage:.2%}",
        "status_counts": dict(status_counts),
        "provenance_coordinates": len(provenance),
        "present_cells_with_source_sha256": sum(bool(values[key] and row["source_sha256"]) for key, row in provenance.items()),
    }


def _json(inputs: dict, role: str, path: str):
    return json.loads(inputs[role, path])


def _verify_archive(repo: PinnedRepository, inputs: dict, audit: dict, recovery: dict) -> tuple[list[dict], dict]:
    archive = BASELINES["archive_path"]
    evidence = lambda name: inputs["engineering", f"{archive}/{name}"]
    release = json.loads(evidence("census_release.json"))
    verification = json.loads(evidence("verification.json"))
    asset_manifest = json.loads(evidence("asset_manifest.json"))
    bundle = json.loads(evidence("bundle_verification.json"))
    scope = BASELINES["profiles"][ARCHIVED_CENSUS]
    archive_sha = BASELINES["refs"][scope["science_ref"]]
    lock_sha = _sha(inputs["subset", BASELINES["snapshot_lock"]])
    cutoff = scope["cutoff"]
    checks = [
        _check("archive_science_ref", archive_sha, release["code"]["commit"]),
        _check("archive_verification_science_ref", archive_sha, verification["science_code"]["commit"]),
        _check("archive_cutoff", [cutoff] * 4, [audit["cutoff"], release["cutoff"], verification["cutoff"], recovery["contract"]["cutoff"]]),
        _check("archive_lock_sha", [lock_sha] * 4, [release["phase22_lock_sha256"], verification["phase22_lock_sha256"], bundle["lock_sha256"], recovery["contract"]["lock_sha256"]]),
        _check("archive_asset_manifest_sha", [bundle["manifest_sha256"]] * 2, [_sha(evidence("asset_manifest.json")), recovery["contract"]["manifest_sha256"]]),
        _check("archive_census_sha", release["census_csv_sha256"], _sha(evidence("REE project census.csv"))),
        _check("archive_source_references", scope["historical_source_references"], len(asset_manifest)),
        _check("archive_assets", scope["historical_source_assets"], len({entry["sha256"] for entry in asset_manifest})),
        _check("recovery_assets_match_archive", sorted({entry["sha256"] for entry in asset_manifest}), sorted(entry["expected_sha256"] for entry in recovery["assets"])),
    ]
    for name, digest in audit["archive_file_sha256"].items():
        checks.append(_check(f"archive_receipt_sha:{name}", digest, _sha(evidence(name)), path=f"{archive}/{name}"))

    gap_rules = inputs["science", "config/census_gap_review.json"]
    frozen = json.loads(gap_rules)["baseline"]
    frozen_states = inputs["science", frozen["path"]]
    checks.append(_check("frozen_gap_rule_sha", release["configuration_files"]["census_gap_review.json"], _sha(gap_rules)))
    checks.append(_check("frozen_denominator_sha", frozen["sha256"], _sha(frozen_states), path=frozen["path"]))
    counts = census_counts(
        evidence("REE project census.csv"), evidence("field_status.csv"), evidence("field_provenance.csv"),
        frozen_states, evidence("gap_review.csv"), release["selected_census_ids"],
    )
    expected = {key: value for key, value in audit["census"].items() if key in counts}
    checks.append(_check("exact_census_counts_and_states", expected, {key: counts[key] for key in expected}))
    reported = {
        "projects": verification["projects"],
        "columns": verification["columns"],
        "total_cells": verification["total_cells"],
        "present": verification["populated"],
        "not_applicable": verification["not_applicable"],
        "missing_applicable": verification["missing"],
        "historical_frozen_denominator": verification["fixed_denominator"],
    }
    checks.append(_check("verification_receipt_counts", reported, {key: counts[key] for key in reported}))
    config = {}
    for name, digest in sorted(release["configuration_files"].items()):
        science_digest = _sha(inputs["science", f"config/{name}"])
        engineering = inputs.get(("engineering", f"config/{name}"))
        config[name] = {
            "published_sha256": digest,
            "science_ref_sha256": science_digest,
            "engineering_ref_sha256": _sha(engineering) if engineering is not None else None,
            "status": "PASS" if digest == science_digest else "BLOCKED",
            "reason": None if digest == science_digest else "REPLAY_ENVIRONMENT_MISMATCH",
        }
        checks.append(_check(
            f"config_comparison_receipt:{name}", audit["config_comparison"]["archive_commit_config_sha256"][name],
            science_digest, path=f"config/{name}",
        ))

    products = {}
    for panel, record in sorted(release["ranking_panels"].items()):
        products[panel] = {}
        for name, expected_hash in sorted(record["digests"].items()):
            path = f"{archive}/MCDA/{panel}/{name}"
            data = repo.read("engineering", path)
            if data is None:
                tier = "RECEIPT_FINGERPRINT_ONLY"
                if name == "ranking.csv":
                    checks.append(_check(f"archived_panel:{panel}/{name}", expected_hash, None, path=path))
            else:
                tier = "REHASHED_ARCHIVED_BYTES"
                checks.append(_check(f"archived_panel:{panel}/{name}", expected_hash, _sha(data), path=path))
                if name == "ranking.csv":
                    cohort = {row["project_id"] for row in checked_csv(data)}
                    checks.append(_check(f"archived_panel_ranked_cohort:{panel}", record["ranked_projects"], len(cohort), path=path))
            products[panel][name] = {"sha256": expected_hash, "evidence_tier": tier, "recomputed": False}
    checks.append(_check("archived_panel_count", verification["mcda"]["panels"], len(products)))
    return checks, {
        "census": counts, "configuration": config, "panel_artifacts": products,
        "archived_output_status": "ARCHIVED_OUTPUT_VERIFIED" if _status(checks) == "PASS" else _status(checks),
    }


def _verify_subset(inputs: dict, subset: dict, recovery: dict) -> tuple[list[dict], dict]:
    config = BASELINES["profiles"][PHASE22_SUBSET]
    lock_data = inputs["subset", BASELINES["snapshot_lock"]]
    lock = json.loads(lock_data)
    selection = recovery["verified_subset_source_inputs"]
    required = set(selection["required_sha256"])
    checks = [
        _check("subset_cutoff", [config["cutoff"]] * 2, [subset["cutoff"], selection["cutoff"]]),
        _check("subset_code_ref", BASELINES["refs"]["subset"], subset["code"]["commit"]),
        _check("subset_lock_sha", [_sha(lock_data)] * 2, [subset["locked_input_file_sha256"], selection["lock_sha256"]]),
        _check("subset_lock_snapshots", selection["snapshot_entries"], len(lock)),
        _check("subset_required_assets", [config["historical_source_assets"]] * 3, [len(required), subset["required_source_assets"], subset["verified_source_assets"]]),
        _check("subset_lock_sha_membership", [], sorted({row["sha256"] for row in lock} - required)),
        _check("subset_recovery_membership", [], sorted(required - {row["expected_sha256"] for row in recovery["assets"]})),
        _check("subset_panel_count", [config["historical_panel_count"]] * 2, [len(subset["panel_results"]), subset["completed_panels"]]),
        _check(
            "subset_replay_receipt_sha",
            recovery["verified_subset_replay"]["receipt_sha256"],
            _sha(inputs["engineering", f"{BASELINES['audit_path']}/verified-subset.json"]),
        ),
    ]
    products = {
        panel: {
            path: {"sha256": digest, "evidence_tier": "V2_REPLAY_RECEIPT_FINGERPRINT_ONLY", "recomputed": False}
            for path, digest in artifact["sha256"].items()
        }
        for panel, artifact in subset["panel_results"].items()
    }
    return checks, {"panel_artifacts": products, "historical_subset_replay": subset}


def verify_baseline(v2_root: Path, profile: str, out: Path, assets: Path | None = None) -> dict:
    """Verify one profile's reference data independently of a source replay."""
    start = perf_counter()
    specification = BASELINES["profiles"][profile]
    _ensure_output_outside_reference(v2_root, out)
    repo = PinnedRepository(v2_root)
    inputs, checks = pinned_inputs(repo, profile)
    detail: dict = {}
    temporal_inventory = None
    inventory: list[dict] = []
    baseline_status = _status(checks)
    replay = {"status": "BLOCKED", "reason": "REFERENCE_UNVERIFIED"}
    if baseline_status == "PASS":
        try:
            audit_path = BASELINES["audit_path"]
            recovery = _json(inputs, "engineering", f"{audit_path}/source_recovery_manifest.json")
            if profile == ARCHIVED_CENSUS:
                audit = _json(inputs, "engineering", f"{audit_path}/phase10_baseline.json")
                profile_checks, detail = _verify_archive(repo, inputs, audit, recovery)
                required = set(audit["source_lock"]["unique_sha256"])
            else:
                subset = _json(inputs, "engineering", f"{audit_path}/verified-subset.json")
                profile_checks, detail = _verify_subset(inputs, subset, recovery)
                required = set(recovery["verified_subset_source_inputs"]["required_sha256"])
            checks.extend(profile_checks)
            inventory = asset_inventory(repo, required, recovery, assets)
            snapshot_path = BASELINES["snapshot_lock"]
            temporal_inventory = source_temporal_inventory(
                inventory, specification["cutoff"], inputs["subset", snapshot_path],
                BASELINES["refs"]["subset"], snapshot_path,
                frozenset(recovery["verified_subset_source_inputs"]["required_sha256"]),
            )
            checks.append(_check("required_assets_covered_by_inventory", sorted(required), sorted(
                row["expected_sha256"] for row in inventory if row["status"] != "NOT_REQUIRED_FOR_SCOPE")))
            if any(item["status"] == "BLOCKED" for item in detail.get("configuration", {}).values()):
                replay = {"status": "BLOCKED", "reason": "REPLAY_ENVIRONMENT_MISMATCH"}
            elif any(row["status"] in {"WRONG_SHA", "MISSING"} for row in inventory):
                replay = {"status": "BLOCKED", "reason": "SOURCE_UNAVAILABLE"}
            else:
                replay = {"status": "NOT_RUN", "reason": "V3_SCIENTIFIC_REPLAY_NOT_EXECUTED"}
            detail["historical_v2_archive_recovery"] = {
                "cutoff": recovery["contract"]["cutoff"],
                "exact": recovery["verified_assets"],
                "missing": recovery["source_unavailable"],
                "missing_by_kind": recovery["source_unavailable_kind_counts"],
                "direct_historical_impacts": recovery["direct_historical_impacts"],
                "missing_assets": recovery["missing_assets"],
            }
        except (KeyError, ValueError, OSError) as error:
            checks.append({"name": "profile_inputs", "status": "FAIL", "reason": str(error)})

    status = _status(checks)
    receipt = {
        "schema": "migration-baseline-verification-v1",
        "profile": profile,
        "status": status,
        "cutoff": specification["cutoff"],
        "refs": BASELINES["refs"],
        "checks": checks,
        "detail": detail,
        "asset_inventory_counts": dict(Counter(row["status"] for row in inventory)),
        "asset_inventory_path": "source_availability.json",
        "source_temporal_inventory": ({
            "path": "source_temporal.json",
            "decisions_by_status": temporal_inventory["decisions_by_status"],
            "proof_scope": temporal_inventory["proof_scope"],
        } if temporal_inventory is not None else {"status": "NOT_RUN"}),
        "source_to_output_replay": replay if status == "PASS" else {"status": "BLOCKED", "reason": "REFERENCE_UNVERIFIED"},
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "elapsed_seconds": perf_counter() - start,
    }
    _write_json(out / "source_availability.json", {
        "schema": "migration-source-availability-v1",
        "profile": profile, "cutoff": specification["cutoff"], "assets": inventory,
        "status": "PASS" if inventory else "NOT_RUN",
    })
    if temporal_inventory is not None:
        _write_json(out / "source_temporal.json", temporal_inventory)
    _write_json(out / "baseline_verification.json", receipt)
    return receipt
