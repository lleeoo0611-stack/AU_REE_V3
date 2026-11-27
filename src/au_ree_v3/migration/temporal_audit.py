"""Trace temporal facts for the frozen Golden archive and apply the V3 cutoff gate."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from ..contracts.temporal import TemporalEvidence, evaluate_temporal
from .baseline import (
    ARCHIVED_CENSUS,
    BASELINES,
    PHASE22_SUBSET,
    PinnedRepository,
    _ensure_output_outside_reference,
    _status,
    _write_json,
    checked_csv,
    pinned_inputs,
)
from .source_temporal import public_snapshot_capture


SCHEMA = "golden-temporal-audit-v1"
ACQUISITION_CODE = "src/au_ree_v2/acquisition.py"
ACQUISITION_CODE_SHA256 = "16df3e2cfe36600a7911d130964f2198325460fdeea982ee36a0f3fe951a1ca5"


@dataclass(frozen=True)
class TemporalInputs:
    observations: dict
    selection: dict
    acquisition: dict
    release: dict
    captures: dict
    phase22_required: frozenset[str]
    references: dict
    cutoff: str


def _keyed(rows, key) -> dict:
    result = {}
    for row in rows:
        identifier = key(row)
        if identifier in result:
            raise ValueError(f"Duplicate frozen identifier {identifier!r}")
        result[identifier] = row
    return result


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _day(value: str) -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError(f"Acquisition timestamp has no timezone: {value!r}")
    return stamp.astimezone(timezone.utc).date().isoformat()


def _pinned_bytes(repo: PinnedRepository, role: str, path: str, digest: str) -> bytes:
    content = repo.read(role, path)
    if content is None or _sha(content) != digest:
        raise ValueError(f"Frozen input unavailable or checksum mismatch: {role}:{path}")
    return content


def _load_inputs(repo: PinnedRepository, pinned: dict[tuple[str, str], bytes]) -> TemporalInputs:
    archive = BASELINES["archive_path"]
    names = {
        "observations": ("engineering", f"{archive}/resource_statement_observations.csv"),
        "selection": ("engineering", f"{archive}/source_selection.csv"),
        "release": ("engineering", f"{archive}/census_release.json"),
        "bundle": ("engineering", f"{archive}/bundle_verification.json"),
        "capture": ("subset", BASELINES["snapshot_lock"]),
        "subset_receipt": ("engineering", f"{BASELINES['audit_path']}/verified-subset.json"),
        "recovery": ("engineering", f"{BASELINES['audit_path']}/source_recovery_manifest.json"),
    }
    data = {name: pinned[key] for name, key in names.items()}
    release, bundle, subset, recovery = (json.loads(data[name]) for name in ("release", "bundle", "subset_receipt", "recovery"))
    cutoff = BASELINES["profiles"][ARCHIVED_CENSUS]["cutoff"]
    if release["cutoff"] != cutoff or release["code"]["commit"] != BASELINES["refs"]["science"]:
        raise ValueError("Frozen release does not match the audit cutoff or science commit")
    if release["source_selection_sha256"] != _sha(data["selection"]):
        raise ValueError("Source selection ledger does not match the frozen release")
    lock_sha = _sha(data["capture"])
    if {bundle["lock_sha256"], release["phase22_lock_sha256"], subset["locked_input_file_sha256"]} != {lock_sha}:
        raise ValueError("Source lock disagrees across frozen receipts")
    required = frozenset(recovery["verified_subset_source_inputs"]["required_sha256"])
    subset_cutoff = BASELINES["profiles"][PHASE22_SUBSET]["cutoff"]
    if (subset["code"]["commit"] != BASELINES["refs"]["subset"] or subset["cutoff"] != subset_cutoff
            or subset["verified_source_assets"] != len(required) or subset["required_source_assets"] != len(required)
            or subset["status"] != "COMPLETED"):
        raise ValueError("Frozen subset receipt does not attest exact locked source verification")

    names["acquisition"] = ("engineering", f"{archive}/source_acquisition.json")
    data["acquisition"] = _pinned_bytes(repo, *names["acquisition"], bundle["receipt_sha256"])
    acquisition = json.loads(data["acquisition"])
    if acquisition["cutoff"] != cutoff:
        raise ValueError("Portable acquisition receipt has another cutoff")
    names["policy"] = ("science", ACQUISITION_CODE)
    data["policy"] = _pinned_bytes(repo, *names["policy"], ACQUISITION_CODE_SHA256)

    references = {
        name: {"commit": BASELINES["refs"][role], "path": path, "sha256": _sha(data[name])}
        for name, (role, path) in names.items()
    }
    return TemporalInputs(
        observations=_keyed(checked_csv(data["observations"]), lambda row: row["observation_id"]),
        selection=_keyed(checked_csv(data["selection"]), lambda row: (row["key"], row["source_sha256"])),
        acquisition=_keyed(acquisition["snapshots"], lambda row: (row["key"], row["sha256"])),
        release=_keyed(release["sources"], lambda row: (row["key"], row["sha256"])),
        captures=_keyed(json.loads(data["capture"]), lambda row: (row["key"], row["sha256"])),
        phase22_required=required,
        references={
            **references,
            "native_acquisition_receipt_sha256_in_release": release["source_receipt_sha256"],
        },
        cutoff=cutoff,
    )


def _read_expected(expected: Path, oracle_manifest: Path) -> tuple[list[dict], str, str]:
    data = expected.read_bytes()
    manifest_data = oracle_manifest.read_bytes()
    manifest = json.loads(manifest_data)
    expected_sha = _sha(data)
    if (manifest["expected_file"]["path"] != expected.name or manifest["expected_file"]["sha256"] != expected_sha
            or manifest["ref"] != BASELINES["refs"]["engineering"] or manifest["science_ref"] != BASELINES["refs"]["science"]):
        raise ValueError("Golden expected file does not match its frozen Oracle manifest")
    rows = [json.loads(line) for line in data.splitlines()]
    if len({row["case_id"] for row in rows}) != len(rows) or len(rows) != manifest["case_count"]:
        raise ValueError("Golden case count or identity disagrees with its Oracle manifest")
    observations = [row for row in rows if row["case_id"].startswith(f"{ARCHIVED_CENSUS}:observation:")]
    if not observations:
        raise ValueError("Golden expected file contains no frozen archive observations")
    return sorted(observations, key=lambda row: row["case_id"]), expected_sha, _sha(manifest_data)


def _capture_evidence(values: dict, key: tuple[str, str], inputs: TemporalInputs) -> tuple[TemporalEvidence, ...]:
    capture = inputs.captures.get(key)
    if capture is None or key[1] not in inputs.phase22_required:
        return ()
    release = inputs.release[key]
    capture_date = _day(capture["acquired_at"])
    if (capture["acquired_at"] != values["acquired_at"] or capture["acquired_at"] != release["acquired_at"]["value"]
            or capture["available_date"] != release["available_date"]["value"]
            or capture["publication_date"] != values["publication_date"] or capture_date > inputs.cutoff):
        return ()
    ref = inputs.references["capture"]
    evidence = public_snapshot_capture(capture, ref["commit"], ref["path"], ref["sha256"])
    return (evidence,) if evidence is not None else ()


def _source_key(values: dict, candidates: list[tuple[str, str]], inputs: TemporalInputs) -> tuple[str, str]:
    matching = []
    for key in candidates:
        decision = inputs.selection[key]
        episode = inputs.acquisition.get(key)
        if (episode and decision["available_date"] == values["available_date"]
                and decision["publication_date"] == values["publication_date"]
                and episode["url"] == values["source_url"]):
            matching.append(key)
    if len(matching) != 1:
        raise ValueError(f"Ambiguous or missing frozen source episode for {values['observation_id']}")
    return matching[0]


def _audit_row(row: dict, candidates: list[tuple[str, str]], inputs: TemporalInputs) -> dict:
    values = row["values"]
    observation_id = values["observation_id"]
    if row.get("synthetic") or row["case_id"] != f"{ARCHIVED_CENSUS}:observation:{observation_id}":
        raise ValueError(f"Invalid frozen observation identity {observation_id}")
    raw = inputs.observations[observation_id]
    if (values["research_cutoff"] != inputs.cutoff or any(
        (raw[key] or None) != values[key] for key in (
            "project_id", "statement_id", "scope", "source_sha256", "publication_date",
            "publication_date_precision", "effective_date", "effective_date_precision", "acquired_at",
        )
    ) or bool(int(raw["selected_for_census"])) is not values["selected_for_census"]):
        raise ValueError(f"Golden temporal facts disagree with frozen V2 observation {observation_id}")

    key = _source_key(values, candidates, inputs)
    selection, episode, release = (source[key] for source in (inputs.selection, inputs.acquisition, inputs.release))
    if (selection["cutoff"] != inputs.cutoff or episode["available_date"] != selection["available_date"]
            or episode["publication_date"] != selection["publication_date"]
            or release["publication_date"]["value"] != values["publication_date"]
            or release["acquired_at"]["value"] != values["acquired_at"]
            or _day(episode["acquired_at"]) != selection["available_date"]):
        raise ValueError(f"Frozen source episode dates disagree for {observation_id}")
    evidence = _capture_evidence(values, key, inputs)
    kwargs = {
        "source_sha256": key[1],
        "cutoff": inputs.cutoff,
        "publication_date": values["publication_date"],
        "available_date": values["available_date"],
        "acquired_at": values["acquired_at"],
        "effective_date": values["effective_date"],
    }
    unclassified = evaluate_temporal(**kwargs)
    reacquired_at = episode["acquired_at"] if episode["acquired_at"] != values["acquired_at"] else None
    resolved = evaluate_temporal(**kwargs, reacquired_at=reacquired_at, availability_role="REGISTRY_SELECTION", evidence=evidence)
    capture = inputs.captures.get(key)

    return {
        "case_id": row["case_id"],
        "project_id": values["project_id"],
        "observation_id": observation_id,
        "statement_id": values["statement_id"],
        "scope": values["scope"],
        "source_key": key[0],
        "source_sha256": key[1],
        "observed_v2_dates": {
            "research_cutoff": inputs.cutoff,
            "effective_date": values["effective_date"],
            "effective_date_precision": values["effective_date_precision"],
            "publication_date": values["publication_date"],
            "publication_date_precision": values["publication_date_precision"],
            "publication_date_role": episode.get("publication_date_role", "V2_DOCUMENT_PUBLICATION"),
            "available_date": values["available_date"],
            "acquired_at": values["acquired_at"],
        },
        "v2_publication": {
            "selected_for_census": values["selected_for_census"],
            "selection_role": values["selection_role"],
            "source_selection": selection,
            "retained_release_available_date": release["available_date"]["value"],
            "portable_registry_acquired_at": episode["acquired_at"],
        },
        "temporal_evidence": [
            {
                "kind": item.kind,
                "reference": item.reference,
                "reference_sha256": item.reference_sha256,
                "source_sha256": key[1],
                "public_on": item.public_on,
                "source_url": capture["url"],
                "note": "The pinned Phase 22 snapshot and verified subset receipt attest exact PDF capture by this UTC day.",
            }
            for item in evidence
        ],
        "registry_role": "REGISTRY_SELECTION",
        "reacquired_at": reacquired_at,
        "observed_availability_after_cutoff": date.fromisoformat(values["available_date"]) > date.fromisoformat(inputs.cutoff),
        "observed_availability_after_acquisition": values["available_date"] > _day(values["acquired_at"]),
        "joined_distinct_acquisition_episodes": reacquired_at is not None,
        "unclassified_temporal_decision": unclassified.to_dict(),
        "v3_temporal_decision": resolved.to_dict(),
        "formal_selection_disposition": "HISTORICAL_COMPARISON_ONLY",
    }


def audit_temporal(v2_root: Path, expected: Path, oracle_manifest: Path, out: Path) -> dict:
    """Audit dated provenance for the frozen Golden observation rows without rewriting them."""
    _ensure_output_outside_reference(v2_root, out)
    repo = PinnedRepository(v2_root)
    pinned, checks = pinned_inputs(repo, ARCHIVED_CENSUS)
    return _audit_pinned_temporal(repo, expected, oracle_manifest, out, pinned, checks)


def _audit_pinned_temporal(
    repo: PinnedRepository, expected: Path, oracle_manifest: Path, out: Path,
    pinned: dict[tuple[str, str], bytes], checks: list[dict],
) -> dict:
    """Reuse the provenance checked by the Golden generator."""
    for name in ("temporal_dispositions.jsonl", "temporal_audit.json"):
        if (out / name).is_symlink():
            raise ValueError(f"Temporal audit output cannot be a symlink: {name}")
    status = _status(checks)
    if status != "PASS":
        receipt = {"schema": SCHEMA, "status": status, "reason": "PINNED_V2_INPUTS_UNVERIFIED", "checks": checks}
        _write_json(out / "temporal_audit.json", receipt)
        return receipt

    expected_rows, expected_sha, manifest_sha = _read_expected(expected, oracle_manifest)
    inputs = _load_inputs(repo, pinned)
    source_keys = defaultdict(list)
    for key in inputs.selection:
        source_keys[key[1]].append(key)
    dispositions = [_audit_row(row, source_keys[row["values"]["source_sha256"]], inputs) for row in expected_rows]
    payload = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in dispositions
    )
    (out / "temporal_dispositions.jsonl").write_bytes(payload)
    late = [row for row in dispositions if row["observed_availability_after_cutoff"]]
    contradictions = [row for row in dispositions if row["observed_availability_after_acquisition"]]
    receipt = {
        "schema": SCHEMA,
        "status": "PASS",
        "audit_scope": "FIXED_HISTORICAL_OBSERVATION_REFERENCE",
        "cutoff": inputs.cutoff,
        "expected_sha256": expected_sha,
        "oracle_manifest_sha256": manifest_sha,
        "temporal_dispositions": {"path": "temporal_dispositions.jsonl", "sha256": _sha(payload)},
        "references": inputs.references,
        "counts": {
            "golden_observation_rows": len(dispositions),
            "observed_availability_after_cutoff": len(late),
            "observed_availability_after_cutoff_previously_selected": sum(row["v2_publication"]["selected_for_census"] for row in late),
            "observed_availability_after_acquisition": len(contradictions),
            "observed_acquisition_after_cutoff": sum(_day(row["observed_v2_dates"]["acquired_at"]) > inputs.cutoff for row in dispositions),
            "joined_distinct_acquisition_episodes": sum(row["joined_distinct_acquisition_episodes"] for row in dispositions),
            "exact_pre_cutoff_capture_supported": sum(bool(row["temporal_evidence"]) for row in dispositions),
            "fresh_formal_promotions": 0,
        },
        "unclassified_temporal_status_counts": dict(Counter(row["unclassified_temporal_decision"]["status"] for row in dispositions)),
        "v3_temporal_status_counts": dict(Counter(row["v3_temporal_decision"]["status"] for row in dispositions)),
        "proof_scope": "Exact Phase 22 pre-cutoff capture verified in the fixed subset receipt.",
    }
    _write_json(out / "temporal_audit.json", receipt)
    return receipt
