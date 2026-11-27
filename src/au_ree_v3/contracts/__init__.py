"""Versioned, lossless records shared by the observation and publication layers."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import ClassVar, Self


class ContractViolation(ValueError):
    """A value violates the schema or one of its identity constraints."""


class EntityKind(StrEnum):
    COMPANY = "COMPANY"
    PROJECT = "PROJECT"
    DEPOSIT = "DEPOSIT"


class ValidationOutcome(StrEnum):
    VALIDATED = "VALIDATED"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    SCIENTIFIC_CONFLICT = "SCIENTIFIC_CONFLICT"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    AFTER_CUTOFF = "AFTER_CUTOFF"
    CODE_ERROR = "CODE_ERROR"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _required_text(**fields: str) -> None:
    for field, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise ContractViolation(f"{field} must be nonempty text")


def _digest(field: str, value: str, length: int = 64) -> None:
    if not isinstance(value, str) or not re.fullmatch(f"[a-f0-9]{{{length}}}", value):
        raise ContractViolation(f"{field} must be a lowercase hexadecimal digest of length {length}")


def _day(field: str, value: str | None, *, partial: bool = False, timestamp: bool = False) -> None:
    if value is None:
        return
    try:
        if timestamp and len(value) > 10 and value[10] in "T ":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return
        if partial and len(value) in (4, 7):
            value += "-01-01" if len(value) == 4 else "-01"
        if date.fromisoformat(value).isoformat() == value:
            return
    except (TypeError, ValueError):
        pass
    raise ContractViolation(f"{field} must be an ISO date")


def _decimal(field: str, value: str) -> None:
    try:
        if isinstance(value, str) and Decimal(value).is_finite():
            return
    except InvalidOperation:
        pass
    raise ContractViolation(f"{field} must be a finite decimal string")


def _reject_json_constant(value: str) -> None:
    raise ContractViolation(f"invalid JSON number {value}")


@dataclass(frozen=True, kw_only=True)
class Record:
    contract_version: ClassVar[str]

    def to_dict(self) -> dict:
        """Serialize losslessly using only JSON-compatible public fields."""
        return json.loads(canonical_json({"contract_version": self.contract_version, **asdict(self)}))

    @classmethod
    def from_dict(cls, value: dict) -> Self:
        fields = dict(value)
        if fields.pop("contract_version", None) != cls.contract_version:
            raise ContractViolation(f"expected {cls.contract_version} record")
        return cls(**fields)


@dataclass(frozen=True, kw_only=True)
class Source(Record):
    contract_version = "source-v1"

    source_key: str
    url: str
    sha256: str
    kind: str
    source_version: str
    publication_date: str | None = None
    available_date: str | None = None
    acquired_at: str | None = None
    original_path: str | None = None
    source_locator: str | None = None

    def __post_init__(self) -> None:
        _required_text(source_key=self.source_key, url=self.url, kind=self.kind, source_version=self.source_version)
        _digest("sha256", self.sha256)
        _day("publication_date", self.publication_date)
        _day("available_date", self.available_date)
        if self.acquired_at is not None:
            try:
                acquired = datetime.fromisoformat(self.acquired_at.replace("Z", "+00:00"))
            except (AttributeError, ValueError) as error:
                raise ContractViolation("acquired_at must be an ISO timezone-aware timestamp") from error
            if acquired.utcoffset() is None:
                raise ContractViolation("acquired_at must be an ISO timezone-aware timestamp")


@dataclass(frozen=True, kw_only=True)
class Entity(Record):
    contract_version = "entity-v1"

    entity_id: str
    kind: EntityKind
    native_namespace: str
    native_id: str
    name: str
    parent_id: str | None = None

    def __post_init__(self) -> None:
        _required_text(entity_id=self.entity_id, native_namespace=self.native_namespace, native_id=self.native_id, name=self.name)
        object.__setattr__(self, "kind", EntityKind(self.kind))


@dataclass(frozen=True, kw_only=True)
class ResourceScope(Record):
    contract_version = "resource-scope-v1"

    scope_id: str
    project_id: str
    name: str
    deposit_id: str | None = None
    parent_scope_id: str | None = None

    def __post_init__(self) -> None:
        _required_text(scope_id=self.scope_id, project_id=self.project_id, name=self.name)


@dataclass(frozen=True, kw_only=True)
class EntityRelationship(Record):
    contract_version = "entity-relationship-v1"

    relationship_id: str
    subject_id: str
    object_id: str
    relation: str
    anchor_id: str

    def __post_init__(self) -> None:
        _required_text(
            relationship_id=self.relationship_id, subject_id=self.subject_id,
            object_id=self.object_id, relation=self.relation, anchor_id=self.anchor_id,
        )
        if self.subject_id == self.object_id:
            raise ContractViolation("relationship endpoints must be distinct")


@dataclass(frozen=True, kw_only=True)
class EvidenceAnchor(Record):
    contract_version = "evidence-anchor-v1"

    anchor_id: str
    source_key: str
    source_sha: str
    locator: str | None = None
    page: int | None = None
    table: str | int | None = None
    row: str | int | None = None
    column: str | int | None = None
    quote: str | None = None
    bbox: tuple | None = None
    offset: str | int | tuple | None = None
    text_span: str | tuple | dict | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        _required_text(anchor_id=self.anchor_id, source_key=self.source_key)
        _digest("source_sha", self.source_sha)
        if self.page is not None and (type(self.page) is not int or self.page < 1):
            raise ContractViolation("page must be a positive one-based integer")
        for field in ("bbox", "offset", "text_span"):
            value = getattr(self, field)
            if isinstance(value, list):
                object.__setattr__(self, field, tuple(value))
        for field in ("locator", "quote", "role"):
            value = getattr(self, field)
            if value is not None:
                _required_text(**{field: value})
        for field in ("table", "row", "column"):
            value = getattr(self, field)
            if value is not None and not (type(value) is int and value >= 0):
                _required_text(**{field: value})
        if self.bbox is not None and (
            not isinstance(self.bbox, tuple) or len(self.bbox) != 4
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in self.bbox)
            or self.bbox[0] > self.bbox[2] or self.bbox[1] > self.bbox[3]
        ):
            raise ContractViolation("bbox requires four finite ordered coordinates")


@dataclass(frozen=True, kw_only=True)
class RawObservation(Record):
    contract_version = "raw-observation-v1"

    observation_id: str
    project_id: str
    scope_id: str
    source_key: str
    source_sha: str
    variable: str
    raw_value: str | None
    raw_unit: str | None
    status: str
    transform_version: str
    original_payload: str
    anchor_id: str | None = None
    material_basis: str | None = None
    chemical_basis: str | None = None
    denominator_basis: str | None = None
    statement_id: str | None = None
    category: str | None = None
    effective_date: str | None = None
    event_date: str | None = None
    reported_publication_date: str | None = None
    acquisition_date: str | None = None
    deposit_id: str | None = None
    quantity_kind: str | None = None
    native_raw_id: str | None = None
    null_reason: str | None = None
    id_version: str = "observation-v1"

    def __post_init__(self) -> None:
        _required_text(
            observation_id=self.observation_id, project_id=self.project_id, scope_id=self.scope_id,
            source_key=self.source_key, variable=self.variable, status=self.status,
            transform_version=self.transform_version, id_version=self.id_version,
        )
        _digest("source_sha", self.source_sha)
        for field in ("effective_date", "event_date", "reported_publication_date", "acquisition_date"):
            _day(field, getattr(self, field), partial=True, timestamp=True)
        if self.raw_value is not None and not isinstance(self.raw_value, str):
            raise ContractViolation("raw_value must be a lossless source token or null")
        if isinstance(self.original_payload, dict):
            object.__setattr__(self, "original_payload", canonical_json(self.original_payload))
        if not isinstance(self.original_payload, str):
            raise ContractViolation("original_payload must be the original JSON text")
        try:
            decoded = json.loads(self.original_payload, parse_int=str, parse_float=str, parse_constant=_reject_json_constant)
        except (TypeError, ValueError) as error:
            raise ContractViolation("original_payload must be an original JSON object") from error
        if not isinstance(decoded, dict):
            raise ContractViolation("original_payload must be an original JSON object")
        if self.native_raw_id is not None:
            _required_text(native_raw_id=self.native_raw_id)

    @property
    def raw_record_id(self) -> str:
        """Use the V2 extraction edition key when it exists."""
        return self.native_raw_id or self.observation_id


@dataclass(frozen=True, kw_only=True)
class NormalizedObservation(Record):
    contract_version = "normalized-observation-v1"

    candidate_id: str
    observation_id: str
    value: str | None
    unit: str | None
    material_basis: str | None
    chemical_basis: str | None
    transform_version: str
    denominator_basis: str | None = None
    raw_record_id: str | None = None

    def __post_init__(self) -> None:
        _required_text(candidate_id=self.candidate_id, observation_id=self.observation_id, transform_version=self.transform_version)
        if self.value is not None:
            _decimal("value", self.value)
            _required_text(unit=self.unit)
        if self.raw_record_id is not None:
            _required_text(raw_record_id=self.raw_record_id)


@dataclass(frozen=True, kw_only=True)
class ValidatedObservation(Record):
    contract_version = "observation-validation-v1"

    validation_id: str
    candidate_id: str
    cutoff: str
    rules_sha: str
    outcome: ValidationOutcome
    reason: str
    evidence_anchor_ids: tuple[str, ...] = ()
    comparable: bool = False

    def __post_init__(self) -> None:
        _required_text(validation_id=self.validation_id, candidate_id=self.candidate_id, cutoff=self.cutoff, reason=self.reason)
        _day("cutoff", self.cutoff)
        _digest("rules_sha", self.rules_sha)
        object.__setattr__(self, "outcome", ValidationOutcome(self.outcome))
        if not isinstance(self.evidence_anchor_ids, (tuple, list)):
            raise ContractViolation("evidence_anchor_ids must be an ordered sequence")
        object.__setattr__(self, "evidence_anchor_ids", tuple(self.evidence_anchor_ids))
        for anchor_id in self.evidence_anchor_ids:
            _required_text(anchor_id=anchor_id)
        if len(set(self.evidence_anchor_ids)) != len(self.evidence_anchor_ids):
            raise ContractViolation("evidence_anchor_ids must be unique")
        if type(self.comparable) is not bool:
            raise ContractViolation("comparable must be boolean")
        if self.outcome != ValidationOutcome.VALIDATED and self.comparable:
            raise ContractViolation("a rejected candidate cannot be comparable")


@dataclass(frozen=True, kw_only=True)
class AcceptedResearchValue(Record):
    contract_version = "accepted-research-value-v1"

    acceptance_id: str
    validation_id: str
    release_id: str
    selection_key: str
    reason: str

    def __post_init__(self) -> None:
        _required_text(
            acceptance_id=self.acceptance_id, validation_id=self.validation_id, release_id=self.release_id,
            selection_key=self.selection_key, reason=self.reason,
        )


@dataclass(frozen=True, kw_only=True)
class DerivedValue(Record):
    contract_version = "derived-value-v1"

    derived_id: str
    release_id: str
    project_id: str
    scope_id: str
    variable: str
    value: str
    unit: str
    formula: str
    transform_version: str
    input_observation_ids: tuple[str, ...]
    factor: str
    denominator_basis: str
    material_basis: str | None = None
    chemical_basis: str | None = None

    def __post_init__(self) -> None:
        _required_text(
            derived_id=self.derived_id, release_id=self.release_id, project_id=self.project_id,
            scope_id=self.scope_id, variable=self.variable, unit=self.unit,
            formula=self.formula, transform_version=self.transform_version,
            denominator_basis=self.denominator_basis,
        )
        if not isinstance(self.input_observation_ids, (tuple, list)) or not self.input_observation_ids:
            raise ContractViolation("derived values require observation inputs")
        for observation_id in self.input_observation_ids:
            _required_text(observation_id=observation_id)
        object.__setattr__(self, "input_observation_ids", tuple(self.input_observation_ids))
        _decimal("value", self.value)
        _decimal("factor", self.factor)


@dataclass(frozen=True, kw_only=True)
class ResearchRelease(Record):
    contract_version = "research-release-v1"

    release_id: str
    input_sha: str
    code_commit: str
    rules_sha: str
    cutoff: str
    cohort: tuple[str, ...]
    validation_status: str
    schema_version: str = "research-release-v1"
    release_mode: str = "EXPERIMENTAL"
    mcda_method: str | None = None
    weights: dict[str, str] | None = None
    seed: int | None = None
    result_fingerprints: dict[str, str] | None = None

    def __post_init__(self) -> None:
        _required_text(release_id=self.release_id, schema_version=self.schema_version, cutoff=self.cutoff)
        _digest("input_sha", self.input_sha)
        _digest("code_commit", self.code_commit, length=40)
        _digest("rules_sha", self.rules_sha)
        _day("cutoff", self.cutoff)
        if not isinstance(self.cohort, (tuple, list)) or not self.cohort:
            raise ContractViolation("release cohort must be a nonempty ordered sequence")
        object.__setattr__(self, "cohort", tuple(self.cohort))
        for project_id in self.cohort:
            _required_text(project_id=project_id)
        if len(set(self.cohort)) != len(self.cohort):
            raise ContractViolation("release cohort must contain unique project IDs")
        if self.validation_status not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "UNMEASURED"}:
            raise ContractViolation("unknown release validation status")
        if self.release_mode not in {"EXPERIMENTAL", "HISTORICAL", "FORMAL"}:
            raise ContractViolation("unknown release mode")
        if self.release_mode == "FORMAL" and (self.validation_status != "PASS" or not self.result_fingerprints):
            raise ContractViolation("formal releases require passed validation and result fingerprints")
        if self.seed is not None and type(self.seed) is not int:
            raise ContractViolation("seed must be an integer")
        for name, value in (self.weights or {}).items():
            _required_text(weight_name=name)
            _decimal(f"weight {name}", value)
        for name, digest in (self.result_fingerprints or {}).items():
            _required_text(fingerprint_name=name)
            _digest(name, digest)
