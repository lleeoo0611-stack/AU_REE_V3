"""Deterministic evidence gate for a source edition and observation at a cutoff."""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Iterable

TEMPORAL_POLICY_VERSION = "temporal-evidence-v1"
ELIGIBLE = "ELIGIBLE"
INELIGIBLE = "INELIGIBLE"
CANDIDATE = "CANDIDATE"
CONFLICT = "CONFLICT"
PUBLIC_EVIDENCE_KINDS = frozenset({"ARCHIVED_PUBLIC_CAPTURE", "PUBLISHER_EDITION_RECORD"})
AVAILABILITY_ROLES = frozenset({"UNCLASSIFIED", "REGISTRY_SELECTION"})
OBSERVATION_ROLES = frozenset({
    "DOCUMENT", "HISTORICAL_MEASUREMENT", "HISTORICAL_EVENT", "FORECAST", "ENGINEERING_PLAN",
})
_SHA256 = re.compile(r"[a-f0-9]{64}")
_DATE = re.compile(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?")


def _fingerprint(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _date_bounds(token: str, *, timestamp: bool = False) -> tuple[date, date]:
    if timestamp and isinstance(token, str) and len(token) > 10 and token[10] in "T ":
        value = datetime.fromisoformat(token.replace("Z", "+00:00"))
        day = value.astimezone(timezone.utc).date() if value.utcoffset() is not None else value.date()
        return day, day
    if not isinstance(token, str) or not _DATE.fullmatch(token):
        raise ValueError("expected a year, month, or calendar date")
    parts = [int(part) for part in token.split("-")]
    if len(parts) == 1:
        return date(parts[0], 1, 1), date(parts[0], 12, 31)
    if len(parts) == 2:
        year, month = parts
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    day = date(*parts)
    return day, day


def _timestamp(token: str | None) -> datetime | None:
    if token is None:
        return None
    instant = datetime.fromisoformat(token.replace("Z", "+00:00"))
    if instant.utcoffset() is None:
        raise ValueError("acquisition timestamp requires a UTC offset")
    return instant.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class TemporalEvidence:
    """A reviewed reference attesting public access to the exact source edition.

    Ingress resolves the reference and verifies its fingerprint. This contract
    records the result of that review; it does not fetch or authenticate URLs.
    """

    source_sha256: str
    kind: str
    public_on: str
    reference: str
    reference_sha256: str

    def __post_init__(self):
        fingerprints = (self.source_sha256, self.reference_sha256)
        if not all(isinstance(item, str) and _SHA256.fullmatch(item) for item in fingerprints):
            raise ValueError("temporal evidence requires source and reference SHA-256 fingerprints")
        if self.kind not in PUBLIC_EVIDENCE_KINDS or not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("temporal evidence requires a supported kind and a reviewable reference")
        _date_bounds(self.public_on)

    @property
    def evidence_id(self) -> str:
        return _fingerprint(asdict(self))


@dataclass(frozen=True, slots=True)
class TemporalDecision:
    decision_id: str
    policy_version: str
    status: str
    reason: str
    cutoff: str
    source_sha256: str
    observation_role: str
    publication_date: str | None
    available_date: str | None
    availability_role: str
    acquired_at: str | None
    reacquired_at: str | None
    effective_date: str | None
    event_date: str | None
    reported_publication_date: str | None
    earliest_proven_public_date: str | None
    evidence_ids: tuple[str, ...]
    matching_evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _chronology_conflict(publication, reported, available, role, acquired, reacquired, proofs) -> str | None:
    if publication and reported and (publication[1] < reported[0] or reported[1] < publication[0]):
        return "CONFLICTING_ORIGINAL_PUBLICATION_DATES"
    if acquired and reacquired and reacquired < acquired:
        return "REACQUISITION_PRECEDES_ACQUISITION"
    if acquired and role == "UNCLASSIFIED" and available and available[0] > acquired.date():
        return "UNCLASSIFIED_AVAILABILITY_AFTER_ACQUISITION"
    declared = publication or reported
    if declared and proofs and any(end < declared[0] for _, end in proofs):
        return "EXACT_PUBLIC_EVIDENCE_PRECEDES_DECLARED_PUBLICATION"
    return None


def evaluate_temporal(
    *, source_sha256: str, cutoff: str, publication_date: str | None = None,
    available_date: str | None = None, availability_role: str = "UNCLASSIFIED",
    acquired_at: str | None = None, reacquired_at: str | None = None,
    effective_date: str | None = None, event_date: str | None = None,
    reported_publication_date: str | None = None, observation_role: str = "HISTORICAL_MEASUREMENT",
    evidence: Iterable[TemporalEvidence] = (),
) -> TemporalDecision:
    """Bind a decision to the unaltered inputs, evidence, policy and cutoff."""
    if not _SHA256.fullmatch(source_sha256):
        raise ValueError("temporal gate requires the exact source SHA-256")
    if observation_role not in OBSERVATION_ROLES or availability_role not in AVAILABILITY_ROLES:
        raise ValueError("unsupported temporal role")
    cutoff_start, cutoff_end = _date_bounds(cutoff)
    if len(cutoff) != 10 or cutoff_start != cutoff_end:
        raise ValueError("research cutoff requires a calendar day")
    publication, available, effective, event, reported = (
        _date_bounds(value, timestamp=index >= 2) if value is not None else None
        for index, value in enumerate((publication_date, available_date, effective_date, event_date, reported_publication_date))
    )
    acquired, reacquired = _timestamp(acquired_at), _timestamp(reacquired_at)
    supplied = {item.evidence_id: item for item in evidence}
    matching = {key: item for key, item in supplied.items() if item.source_sha256 == source_sha256}
    proofs = [_date_bounds(item.public_on) for item in matching.values()]
    earliest = min((end for _, end in proofs), default=None)
    conflict = _chronology_conflict(publication, reported, available, availability_role, acquired, reacquired, proofs)

    historical = observation_role in {"HISTORICAL_MEASUREMENT", "HISTORICAL_EVENT"}
    historical_dates = [bounds for bounds in (effective, event) if bounds] if historical else []
    if conflict:
        status, reason = CONFLICT, conflict
    elif any(bounds and bounds[0] > cutoff_end for bounds in (publication, reported)):
        status, reason = INELIGIBLE, "PUBLICATION_AFTER_CUTOFF"
    elif historical and not historical_dates:
        status, reason = CANDIDATE, "HISTORICAL_OBSERVATION_DATE_MISSING"
    elif any(start > cutoff_end for start, _ in historical_dates):
        status, reason = INELIGIBLE, "HISTORICAL_OBSERVATION_AFTER_CUTOFF"
    elif any(end > cutoff_end for _, end in historical_dates):
        status, reason = CANDIDATE, "HISTORICAL_DATE_PRECISION_SPANS_CUTOFF"
    elif earliest is None:
        status = CANDIDATE
        reason = "NO_SAME_EDITION_PUBLIC_EVIDENCE" if supplied else "PUBLIC_EVIDENCE_MISSING"
    elif earliest > cutoff_end:
        status, reason = CANDIDATE, "PRE_CUTOFF_PUBLIC_EVIDENCE_MISSING"
    else:
        status, reason = ELIGIBLE, "EXACT_EDITION_PUBLIC_BY_CUTOFF"

    inputs = {
        "policy_version": TEMPORAL_POLICY_VERSION, "status": status, "reason": reason,
        "cutoff": cutoff, "source_sha256": source_sha256, "observation_role": observation_role,
        "publication_date": publication_date, "available_date": available_date,
        "availability_role": availability_role, "acquired_at": acquired_at, "reacquired_at": reacquired_at,
        "effective_date": effective_date, "event_date": event_date, "reported_publication_date": reported_publication_date,
        "earliest_proven_public_date": earliest.isoformat() if earliest is not None else None,
        "evidence_ids": tuple(sorted(supplied)), "matching_evidence_ids": tuple(sorted(matching)),
    }
    return TemporalDecision(decision_id=_fingerprint(inputs), **inputs)
