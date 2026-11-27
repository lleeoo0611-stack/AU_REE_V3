"""Classify locked source dates while preserving independent byte availability."""

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from urllib.parse import urlsplit

from ..contracts.temporal import TemporalEvidence, evaluate_temporal


def public_snapshot_capture(row, commit, path, fingerprint) -> TemporalEvidence | None:
    """Interpret one pinned, public PDF snapshot without guessing its release date."""
    url = urlsplit(row["url"])
    if (row["kind"] != "pdf" or not row["acquired_at"] or url.scheme != "https"
            or not url.hostname or url.username or url.password):
        return None
    instant = datetime.fromisoformat(row["acquired_at"])
    if instant.utcoffset() is None:
        return None
    public_on = instant.astimezone(timezone.utc).date().isoformat()
    if row["available_date"] != public_on:
        return None
    source = row["sha256"]
    reference = f"https://github.com/lleeoo0611-stack/AU_REE_V2/blob/{commit}/{path}#key={row['key']}"
    return TemporalEvidence(source, "ARCHIVED_PUBLIC_CAPTURE", public_on, reference, fingerprint)


def snapshot_public_captures(payload: bytes, commit: str, path: str, verified_shas) -> dict:
    fingerprint = sha256(payload).hexdigest()
    proofs = defaultdict(list)
    for row in json.loads(payload):
        if row["sha256"] in verified_shas:
            evidence = public_snapshot_capture(row, commit, path, fingerprint)
            if evidence is not None:
                proofs[row["sha256"], row["key"]].append(evidence)
    return proofs


def source_temporal_inventory(inventory, cutoff, snapshot, commit, snapshot_path, verified_shas) -> dict:
    proofs = snapshot_public_captures(snapshot, commit, snapshot_path, verified_shas)
    statuses = Counter()
    results = []
    for row in inventory:
        if row["status"] == "NOT_REQUIRED_FOR_SCOPE":
            continue
        source = row["expected_sha256"]
        for locked in row["locked_sources"]:
            key = locked["source_key"]
            evidence = proofs.get((source, key), ())
            raw_dates = {field: locked[field] for field in ("publication_date", "available_date", "historical_acquired_at")}
            decision = evaluate_temporal(
                source_sha256=source, cutoff=cutoff, observation_role="DOCUMENT",
                publication_date=raw_dates["publication_date"] or None,
                available_date=raw_dates["available_date"] or None,
                acquired_at=raw_dates["historical_acquired_at"] or None, evidence=evidence,
            )
            statuses[decision.status] += 1
            results.append({
                "source_key": key, "source_sha256": source, "source_integrity": row["status"],
                "citation_eligibility": decision.status, "decision": decision.to_dict(),
                "raw_v2_dates": raw_dates, "temporal_evidence": [asdict(item) for item in evidence],
                "use": "ARCHIVED_REFERENCE_ONLY",
            })
    return {
        "schema": "source-temporal-inventory-v1",
        "proof_scope": "FIXED_V2_PUBLIC_PDF_SNAPSHOT_LOCK",
        "cutoff": cutoff, "decisions": results, "decisions_by_status": dict(statuses),
    }
