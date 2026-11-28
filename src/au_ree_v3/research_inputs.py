"""Resolve temporally eligible selections for release-bound consumers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from .contracts import (
    AcceptedResearchValue,
    ContractViolation,
    EvidenceAnchor,
    NormalizedObservation,
    RawObservation,
    ResearchRelease,
    ResourceScope,
    Source,
    ValidatedObservation,
    canonical_json,
)

if TYPE_CHECKING:
    from .store import Store


ResearchPurpose = Literal["CENSUS", "MCDA", "PUBLICATION"]
CENSUS_RESOURCE_USE = "CENSUS_RESOURCE_OBSERVATION"
CENSUS_RESOURCE_RULE = "approved-census-resource-observation-v1"
RESOURCE_RULE = "approved-whole-ore-jorc-resource-v1"
MCDA_RESOURCE_USE = "MCDA_R1/S1"
NATIVE_RESOURCE_CANDIDATE = "NATIVE_RESOURCE_CANDIDATE"
_ACTUAL_ROLES = {"HISTORICAL_MEASUREMENT", "HISTORICAL_EVENT"}


@dataclass(frozen=True)
class ResearchInput:
    accepted: AcceptedResearchValue
    validation: ValidatedObservation
    candidate: NormalizedObservation
    raw: RawObservation
    source: Source
    scope: ResourceScope
    anchor: EvidenceAnchor
    temporal_trace: dict

    def lineage(self) -> dict:
        """Record the selected edition and its current temporal decision."""
        return {
            "release_id": self.accepted.release_id,
            "selection_id": self.accepted.acceptance_id,
            "validation_id": self.validation.validation_id,
            "candidate_id": self.candidate.candidate_id,
            "observation_id": self.raw.observation_id,
            "raw_record_id": self.raw.raw_record_id,
            "project_id": self.raw.project_id,
            "scope_id": self.scope.scope_id,
            "source_key": self.source.source_key,
            "source_sha256": self.source.sha256,
            "anchor_id": self.anchor.anchor_id,
            "temporal_decision": self.temporal_trace,
        }


def checked_research_inputs(
    store: Store, release: ResearchRelease, *, purpose: ResearchPurpose,
) -> tuple[ResearchInput, ...]:
    """Verify release selections for consumers of actual results."""
    if purpose not in {"CENSUS", "MCDA", "PUBLICATION"}:
        raise ValueError(f"Unknown research input purpose {purpose!r}")
    if store.get(ResearchRelease, release.release_id) != release:
        raise ContractViolation("research inputs require the exact stored release")
    if not release.temporal_policy_version:
        raise ContractViolation("research inputs require a temporally governed release")
    formal = purpose in {"MCDA", "PUBLICATION"}
    if formal and (
        release.release_mode != "FORMAL" or release.validation_status != "PASS"
    ):
        raise ContractViolation(f"{purpose} requires a verified formal release")

    records = []
    for accepted in store.accepted_for_release(release.release_id):
        validation = store.get(ValidatedObservation, accepted.validation_id)
        trace = store.validation_temporal_trace(accepted.validation_id)
        if validation is None or trace is None:
            raise ContractViolation("research input requires a persisted temporal decision")
        candidate = store.get(NormalizedObservation, validation.candidate_id)
        if candidate is None:
            raise ContractViolation("research input is missing the bound candidate")
        raw = store.raw_for_candidate(candidate.candidate_id)
        if raw.observation_role not in _ACTUAL_ROLES:
            raise ContractViolation(f"{purpose} actual results cannot use {raw.observation_role} as an observation")
        source = store.get(Source, (raw.source_key, raw.source_sha))
        scope = store.get(ResourceScope, raw.scope_id)
        anchor = store.get(EvidenceAnchor, raw.anchor_id)
        if source is None or scope is None or anchor is None:
            raise ContractViolation("research input lineage is incomplete")
        records.append(ResearchInput(accepted, validation, candidate, raw, source, scope, anchor, trace))
    if formal:
        if not records or not release.temporal_evidence_ids:
            raise ContractViolation(f"{purpose} requires accepted selections and temporal proof pins")
        consumed = {record.temporal_trace["decision_id"] for record in records}
        if consumed != set(release.temporal_decision_ids):
            raise ContractViolation(f"{purpose} selections must cover the exact temporal decision pins")
    if purpose == "MCDA":
        records = [record for record in records if record.accepted.reason != CENSUS_RESOURCE_RULE]
    return tuple(records)


def publication_temporal_lineage(store: Store, release: ResearchRelease) -> dict:
    """Resolve the exact temporal lineage needed when preparing a formal seal."""
    inputs = checked_research_inputs(store, release, purpose="PUBLICATION")
    snapshot = [
        {
            "accepted": item.accepted.to_dict(),
            "validation": item.validation.to_dict(),
            "candidate": item.candidate.to_dict(),
            "raw": item.raw.to_dict(),
            "source": item.source.to_dict(),
            "scope": item.scope.to_dict(),
            "anchor": item.anchor.to_dict(),
            "decision": item.temporal_trace,
        }
        for item in inputs
    ]
    return {
        "release_id": release.release_id,
        "cutoff": release.cutoff,
        "temporal_policy_version": release.temporal_policy_version,
        "temporal_decision_ids": list(release.temporal_decision_ids),
        "temporal_evidence_ids": list(release.temporal_evidence_ids),
        "accepted_selection_count": len(inputs),
        "selection_snapshot_sha256": sha256(canonical_json(snapshot).encode("utf-8")).hexdigest(),
        "inputs": [entry.lineage() for entry in inputs],
    }
