"""Versioned scientific-use context and evidence-bound shadow assessments."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from importlib.resources import files
from typing import ClassVar

from .contracts import ContractViolation, Record, canonical_json

VOCABULARY_FILE = "semantic_vocabulary_v1.json"
POLICY_VERSION = "ontology-use-contract-v1"
ASSESSMENT_SCHEMA = "semantic-assessment-v1"
_VOCABULARY_BYTES = files(__package__).joinpath(VOCABULARY_FILE).read_bytes()
VOCABULARY_SHA256 = sha256(_VOCABULARY_BYTES).hexdigest()
_VOCABULARY = json.loads(_VOCABULARY_BYTES)
if _VOCABULARY["schema_version"] != "semantic-vocabulary-v1" or _VOCABULARY["policy_version"] != POLICY_VERSION:
    raise ContractViolation("semantic vocabulary and policy versions disagree")


def vocabulary() -> dict:
    return json.loads(_VOCABULARY_BYTES)


def _fingerprint(value) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha(label: str, value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ContractViolation(f"{label} requires a lowercase SHA-256")


def _texts(values, label) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ContractViolation(f"{label} requires nonempty text values")
    if len(set(values)) != len(values):
        raise ContractViolation(f"{label} has duplicate identities")
    return tuple(sorted(values))


def _conditions(values) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, (tuple, list)) or any(
        not isinstance(item, (tuple, list)) or len(item) != 2
        or any(not isinstance(value, str) or not value.strip() for value in item)
        for item in values
    ):
        raise ContractViolation("conditions require named textual pairs")
    if len(set(name for name, _ in values)) != len(values):
        raise ContractViolation("condition names must be unique")
    return tuple(sorted(tuple(item) for item in values))


class SemanticStatus(StrEnum):
    COMPARABLE_DIRECT = "COMPARABLE_DIRECT"
    COMPARABLE_DERIVED = "COMPARABLE_DERIVED"
    DESCRIPTIVE_ONLY = "DESCRIPTIVE_ONLY"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    SCIENTIFIC_CONFLICT = "SCIENTIFIC_CONFLICT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


_COMPARABLE = {SemanticStatus.COMPARABLE_DIRECT, SemanticStatus.COMPARABLE_DERIVED}
_DATA_STATES = {"PRESENT", "NOT_DISCLOSED", "NOT_LOCATED", "UNKNOWN", "NOT_APPLICABLE", "CONFLICT"}
_ROLE_STATES = {"HISTORICAL_MEASUREMENT", "HISTORICAL_EVENT", "FORECAST", "ENGINEERING_PLAN"}
_TEMPORAL_STATES = {"ELIGIBLE", "INELIGIBLE", "CANDIDATE", "CONFLICT"}
_PROCESS_MASS_BASES = {"DRY_MASS", "WET_MASS", "AS_RECEIVED_MASS", "DISSOLVED_ANALYTE_MASS"}


@dataclass(frozen=True, kw_only=True)
class IdentityContext:
    subject_id: str
    subject_kind: str
    object_id: str
    object_kind: str
    relation_type: str
    source_sha256: str
    evidence_anchor_ids: tuple[str, ...]
    confidence: str

    def __post_init__(self):
        if self.subject_kind not in _VOCABULARY["entities"] or self.object_kind not in _VOCABULARY["entities"]:
            raise ContractViolation("unrecognized semantic identity kind")
        if self.subject_id == self.object_id or not self.subject_id or not self.object_id:
            raise ContractViolation("identity relations require two preserved identities")
        if self.confidence not in {"DOCUMENTED", "CANDIDATE", "CONFLICT"} or not self.relation_type:
            raise ContractViolation("identity relation and evidence confidence are required")
        _sha("identity source", self.source_sha256)
        object.__setattr__(self, "evidence_anchor_ids", _texts(self.evidence_anchor_ids, "identity anchors"))
        if self.confidence == "DOCUMENTED" and not self.evidence_anchor_ids:
            raise ContractViolation("documented identity relations require a source anchor")


@dataclass(frozen=True, kw_only=True)
class ProcessContext:
    experiment_id: str | None = None
    test_date: str | None = None
    feed: str | None = None
    product: str | None = None
    step: str | None = None
    start: str | None = None
    end: str | None = None
    scale: str | None = None
    target_species: tuple[str, ...] = ()
    sample_representativeness: str | None = None
    procedure: str | None = None
    conditions: tuple[tuple[str, str], ...] = ()
    uncertainty_or_balance: str | None = None
    feed_moisture_basis: str | None = None
    product_moisture_basis: str | None = None
    intensity_denominator_stream: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "target_species", _texts(self.target_species, "target species"))
        object.__setattr__(self, "conditions", _conditions(self.conditions))


@dataclass(frozen=True, kw_only=True)
class SemanticContext:
    quantity_kind: str
    project_id: str | None = None
    scope_id: str | None = None
    deposit_id: str | None = None
    statement_id: str | None = None
    resource_class: str | None = None
    jorc_categories: tuple[str, ...] = ()
    cutoff_grade: str | None = None
    cutoff_unit: str | None = None
    cutoff_analyte: str | None = None
    effective_date: str | None = None
    event_date: str | None = None
    includes_stockpiles: bool | None = None
    combined_deposit_ids: tuple[str, ...] = ()
    material: str | None = None
    denominator_basis: str | None = None
    chemical_species: tuple[str, ...] = ()
    treo_members: tuple[str, ...] = ()
    temporal_status: str | None = None
    temporal_decision_id: str | None = None
    research_cutoff: str | None = None
    observation_role: str = "HISTORICAL_MEASUREMENT"
    value_state: str = "PRESENT"
    status_role: str | None = None
    geometry_role: str | None = None
    native_feature_id: str | None = None
    crs: str | None = None
    comparison_group: str | None = None
    unit: str | None = None
    cost_currency: str | None = None
    cost_price_basis: str | None = None
    cost_year: str | None = None
    process: ProcessContext | None = None
    identities: tuple[IdentityContext, ...] = ()

    def __post_init__(self):
        vocab = _VOCABULARY
        if self.quantity_kind not in vocab["quantity_dimensions"]:
            raise ContractViolation(f"unsupported semantic quantity kind {self.quantity_kind!r}")
        if self.resource_class is not None and self.resource_class not in vocab["resource_classes"]:
            raise ContractViolation("unsupported resource classification")
        if self.material is not None and self.material not in vocab["material_kinds"]:
            raise ContractViolation(f"unsupported material basis {self.material!r}")
        if self.observation_role not in _ROLE_STATES or self.value_state not in _DATA_STATES:
            raise ContractViolation("unknown observation role or value state")
        if self.temporal_status is not None and self.temporal_status not in _TEMPORAL_STATES:
            raise ContractViolation("unknown temporal decision status")
        if self.temporal_decision_id is not None:
            _sha("temporal decision identity", self.temporal_decision_id)
        if self.cutoff_grade is not None:
            try:
                cutoff = Decimal(self.cutoff_grade) if isinstance(self.cutoff_grade, str) else Decimal("NaN")
            except InvalidOperation as error:
                raise ContractViolation("cutoff grade must preserve a finite nonnegative decimal token") from error
            if not cutoff.is_finite() or cutoff < 0:
                raise ContractViolation("cutoff grade must preserve a finite nonnegative decimal token")
        for name in ("jorc_categories", "combined_deposit_ids", "chemical_species", "treo_members"):
            object.__setattr__(self, name, _texts(getattr(self, name), name))
        if set(self.jorc_categories) - set(vocab["jorc_categories"]):
            raise ContractViolation("unrecognized JORC categories")
        if self.includes_stockpiles is not None and type(self.includes_stockpiles) is not bool:
            raise ContractViolation("stockpile scope must be explicit true, false, or unknown")
        if isinstance(self.process, dict):
            object.__setattr__(self, "process", ProcessContext(**self.process))
        if self.process is not None and not isinstance(self.process, ProcessContext):
            raise ContractViolation("invalid process context")
        if not isinstance(self.identities, (tuple, list)):
            raise ContractViolation("identity context requires a sequence")
        identities = tuple(IdentityContext(**row) if isinstance(row, dict) else row for row in self.identities)
        if any(not isinstance(item, IdentityContext) for item in identities) or len(set(identities)) != len(identities):
            raise ContractViolation("identity context has invalid or duplicate relations")
        object.__setattr__(self, "identities", tuple(sorted(identities, key=lambda item: canonical_json(asdict(item)))))


class MatchRelation(StrEnum):
    EXACT = "exactMatch"
    CLOSE = "closeMatch"
    BROAD = "broadMatch"
    RELATED = "relatedMatch"


@dataclass(frozen=True, kw_only=True)
class ConceptMapping:
    original: str
    canonical: str
    relation: MatchRelation
    mapping_version: str
    source_sha256: str
    evidence_anchor_ids: tuple[str, ...]
    applicable_context: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if not all(isinstance(value, str) and value.strip() for value in (self.original, self.canonical, self.mapping_version)):
            raise ContractViolation("concept terms and mapping version require explicit text")
        _sha("mapping evidence source", self.source_sha256)
        object.__setattr__(self, "relation", MatchRelation(self.relation))
        object.__setattr__(self, "evidence_anchor_ids", _texts(self.evidence_anchor_ids, "concept mapping anchors"))
        object.__setattr__(self, "applicable_context", _conditions(self.applicable_context))


def resolve_concept(mapping: ConceptMapping, *, context: dict[str, str]) -> str | None:
    """Apply one explicitly evidenced SKOS mapping inside its stated context."""
    if (
        mapping.relation != MatchRelation.EXACT or not mapping.evidence_anchor_ids or not mapping.applicable_context
        or any(context.get(name) != value for name, value in mapping.applicable_context)
    ):
        return None
    return mapping.canonical


@dataclass(frozen=True, kw_only=True)
class SemanticAssessment(Record):
    contract_version: ClassVar[str] = ASSESSMENT_SCHEMA

    normalized_context: SemanticContext
    purpose: str
    observation_ids: tuple[str, ...]
    source_sha256: str
    evidence_anchor_ids: tuple[str, ...]
    status: SemanticStatus
    policy_version: str = POLICY_VERSION
    vocabulary_sha256: str = VOCABULARY_SHA256
    missing_dimensions: tuple[str, ...] = ()
    incompatible_dimensions: tuple[str, ...] = ()
    transforms: tuple[dict, ...] = ()
    reason_codes: tuple[str, ...] = ()
    assessment_id: str | None = field(default=None)

    def _serialized_fields(self) -> dict:
        data = asdict(self)
        process = data["normalized_context"]["process"]
        if process is not None:
            # Preserve the identity of v1 assessments stored before these optional fields.
            for name in ("feed_moisture_basis", "intensity_denominator_stream"):
                if process[name] is None:
                    del process[name]
        return data

    def to_dict(self) -> dict:
        return json.loads(canonical_json({"contract_version": self.contract_version, **self._serialized_fields()}))

    def __post_init__(self):
        if isinstance(self.normalized_context, dict):
            object.__setattr__(self, "normalized_context", SemanticContext(**self.normalized_context))
        if not isinstance(self.normalized_context, SemanticContext):
            raise ContractViolation("semantic assessment requires a typed observation context")
        if (
            self.purpose not in _VOCABULARY["purposes"] or self.policy_version != POLICY_VERSION
            or self.vocabulary_sha256 != VOCABULARY_SHA256
        ):
            raise ContractViolation("unrecognized semantic assessment purpose or policy")
        _sha("assessment source", self.source_sha256)
        object.__setattr__(self, "status", SemanticStatus(self.status))
        for name in ("observation_ids", "evidence_anchor_ids", "missing_dimensions", "incompatible_dimensions", "reason_codes"):
            object.__setattr__(self, name, _texts(getattr(self, name), name))
        if not self.observation_ids:
            raise ContractViolation("semantic assessment needs source-bound observation identities")
        if not isinstance(self.transforms, (tuple, list)) or any(not isinstance(item, dict) for item in self.transforms):
            raise ContractViolation("semantic transforms must be evidence records")
        encoded = [canonical_json(item) for item in self.transforms]
        if len(set(encoded)) != len(encoded):
            raise ContractViolation("semantic assessment repeats a transform")
        object.__setattr__(self, "transforms", tuple(json.loads(item) for item in sorted(encoded)))
        data = self._serialized_fields()
        supplied = data.pop("assessment_id")
        identity = _fingerprint({"schema": ASSESSMENT_SCHEMA, **data})
        if supplied is not None and supplied != identity:
            raise ContractViolation("semantic assessment ID differs from the exact policy and context")
        object.__setattr__(self, "assessment_id", identity)


def _unknown(value) -> bool:
    return value is None or value == () or isinstance(value, str) and value.strip().upper() in {
        "", "UNKNOWN", "NOT_LOCATED", "NOT_DISCLOSED",
    }


def _missing(obj, names, *, prefix="") -> list[str]:
    return [prefix + name for name in names if _unknown(getattr(obj, name))]


def _resource_context(context: SemanticContext, purpose: str) -> tuple[list[str], list[str]]:
    missing = _missing(context, (
        "statement_id", "resource_class", "jorc_categories", "effective_date", "cutoff_grade", "cutoff_unit",
        "cutoff_analyte", "material", "denominator_basis", "treo_members",
    ))
    incompatible = []
    if context.resource_class not in {None, "MINERAL_RESOURCE"}:
        incompatible.append("resource_class")
    if set(context.jorc_categories) - {"MEASURED", "INDICATED", "INFERRED"}:
        incompatible.append("jorc_categories")
    if context.material not in {None, "WHOLE_ORE", "BULK_ORE"}:
        incompatible.append("material")
    if context.cutoff_unit not in {None, "%", "ppm"}:
        incompatible.append("cutoff_unit")
    if context.treo_members and set(context.treo_members) - set(_VOCABULARY["oxide_species"]):
        incompatible.append("treo_members")
    if purpose == "R1" and set(context.chemical_species) - {"TREO", *_VOCABULARY["oxide_species"]}:
        incompatible.append("chemical_species")
    if purpose == "S1":
        if not context.chemical_species:
            missing.append("chemical_species")
        elif not {"Nd2O3", "Pr6O11", "Dy2O3", "Tb4O7"} <= set(context.chemical_species):
            incompatible.append("chemical_species")
        if context.treo_members and set(context.chemical_species) - set(context.treo_members):
            incompatible.append("treo_members")
    return missing, incompatible


def _process_context(context: SemanticContext, purpose: str) -> tuple[list[str], list[str]]:
    missing = _missing(context, ("material", "denominator_basis", "chemical_species"))
    process = context.process or ProcessContext()
    missing.extend(_missing(
        process, (
            "experiment_id", "feed", "product", "step", "start", "end", "scale", "target_species",
            "procedure", "conditions", "sample_representativeness",
        ),
        prefix="process.",
    ))
    if process.test_date is None and context.effective_date is None:
        missing.append("process.test_date")
    if any(_unknown(value) for _, value in process.conditions):
        missing.append("process.conditions")
    if "TREO" in context.chemical_species and not context.treo_members:
        missing.append("treo_members")
    mass_basis_fields = ()
    if purpose == "T1A":
        mass_basis_fields = ("feed_moisture_basis", "product_moisture_basis")
    elif purpose == "T1B":
        mass_basis_fields = ("product_moisture_basis",)
    elif purpose == "T1C":
        if process.intensity_denominator_stream in {"FEED", "PRODUCT"}:
            mass_basis_fields = (process.intensity_denominator_stream.lower() + "_moisture_basis",)
        else:
            missing.append("process.intensity_denominator_stream")
    for name in mass_basis_fields:
        basis = getattr(process, name)
        if not isinstance(basis, str) or basis not in _PROCESS_MASS_BASES:
            missing.append(f"process.{name}")
    if purpose == "T1C" and context.quantity_kind == "COST_INTENSITY":
        missing.extend(_missing(context, ("cost_currency", "cost_year", "cost_price_basis")))
    incompatible = ["process.target_species"] if process.target_species and context.chemical_species and (
        set(process.target_species) != set(context.chemical_species)
    ) else []
    return missing, incompatible


def _dimensions(context: SemanticContext, purpose: str) -> tuple[list[str], list[str]]:
    if purpose in {"R1", "S1"}:
        return _resource_context(context, purpose)
    if purpose in {"T1A", "T1B", "T1C", "T1D"}:
        return _process_context(context, purpose)
    if purpose == "D1":
        return _missing(context, ("event_date", "status_role")), (
            ["status_role"] if context.status_role not in {None, "COMPLETED"} else []
        )
    if purpose == "GIS":
        return _missing(context, ("geometry_role", "native_feature_id", "crs")), (
            ["geometry_role"] if context.geometry_role not in {None, "VERIFIED_MINE_POINT", "VERIFIED_DEPOSIT_POINT"} else []
        )
    raise ContractViolation(f"unknown semantic purpose {purpose!r}")


def assess_semantic_use(
    context: SemanticContext, *, purpose: str, observation_ids: tuple[str, ...], source_sha256: str,
    evidence_anchor_ids: tuple[str, ...], transforms: tuple[dict, ...] = (),
) -> SemanticAssessment:
    """Assess intended use while retaining the existing scientific admission boundary."""
    purpose = purpose.upper()
    missing, incompatible = _dimensions(context, purpose)
    missing.extend(_missing(context, ("project_id", "scope_id", "temporal_status", "temporal_decision_id")))
    if not evidence_anchor_ids:
        missing.append("evidence_anchor_ids")
    if context.quantity_kind not in _VOCABULARY["purposes"][purpose]:
        incompatible.append("quantity_kind")
    expected_role = "HISTORICAL_EVENT" if purpose == "D1" else "HISTORICAL_MEASUREMENT"
    if context.observation_role != expected_role:
        incompatible.append("observation_role")
    if context.temporal_status == "INELIGIBLE":
        incompatible.append("temporal_status")
    if context.temporal_status == "CANDIDATE" and "temporal_status" not in missing:
        missing.append("temporal_status")
    derived = [
        transform for transform in transforms if isinstance(transform, dict)
        and (transform.get("kind") == "DERIVATION" or transform.get("result_role") == "DERIVED")
    ]
    for transform in derived:
        if not transform.get("formula") or not (
            transform.get("policy_version") or transform.get("formula_version") or transform.get("version")
        ):
            missing.append("transforms.derivation_formula_and_version")

    if context.temporal_status == "CONFLICT" or context.value_state == "CONFLICT":
        status = SemanticStatus.SCIENTIFIC_CONFLICT
        reason = "TEMPORAL_EVIDENCE_CONFLICT" if context.temporal_status == "CONFLICT" else "REPORTED_VALUE_CONFLICT"
    elif context.value_state == "NOT_APPLICABLE":
        status, reason = SemanticStatus.NOT_APPLICABLE, "VALUE_NOT_APPLICABLE"
    elif incompatible:
        status, reason = SemanticStatus.NOT_COMPARABLE, "PURPOSE_DIMENSIONS_INCOMPATIBLE"
    elif context.value_state != "PRESENT":
        status, reason = SemanticStatus.INSUFFICIENT_CONTEXT, context.value_state
        missing.append("reported_value")
    elif missing:
        status, reason = SemanticStatus.INSUFFICIENT_CONTEXT, "REQUIRED_CONTEXT_MISSING"
    elif purpose == "T1D":
        status, reason = SemanticStatus.DESCRIPTIVE_ONLY, "EVIDENCE_APPLICABILITY_ONLY"
    elif derived:
        status, reason = SemanticStatus.COMPARABLE_DERIVED, "PURPOSE_COMPATIBLE_DERIVATION"
    else:
        status, reason = SemanticStatus.COMPARABLE_DIRECT, "PURPOSE_COMPATIBLE_MEASUREMENT"
    return SemanticAssessment(
        normalized_context=context, purpose=purpose, observation_ids=observation_ids,
        source_sha256=source_sha256, evidence_anchor_ids=evidence_anchor_ids, transforms=transforms,
        status=status, missing_dimensions=tuple(sorted(set(missing))),
        incompatible_dimensions=tuple(sorted(set(incompatible))), reason_codes=(reason,),
    )


@dataclass(frozen=True, kw_only=True)
class SemanticCompatibility(Record):
    contract_version: ClassVar[str] = "semantic-compatibility-v1"

    assessment_ids: tuple[str, str]
    status: SemanticStatus
    missing_dimensions: tuple[str, ...] = ()
    incompatible_dimensions: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


def compare_semantic_use(left: SemanticAssessment, right: SemanticAssessment) -> SemanticCompatibility:
    """Test two independent projects for use-specific cohort comparability."""
    first, second = left.normalized_context, right.normalized_context
    missing = set(left.missing_dimensions) | set(right.missing_dimensions)
    incompatible = set(left.incompatible_dimensions) | set(right.incompatible_dimensions)
    common = (
        "quantity_kind", "material", "chemical_species", "denominator_basis", "treo_members",
        "comparison_group", "unit", "research_cutoff",
    )
    incompatible.update(_differences(first, second, common))
    if left.purpose != right.purpose:
        incompatible.add("purpose")
    if first.project_id is not None and first.project_id == second.project_id:
        incompatible.add("project_id")
    if left.purpose == right.purpose and left.purpose in {"R1", "S1"}:
        resource = ("resource_class", "jorc_categories", "cutoff_grade", "cutoff_unit", "cutoff_analyte", "includes_stockpiles")
        incompatible.update(_differences(first, second, resource))
        missing.update(_missing(first, resource) + _missing(second, resource))
    if first.process is not None and second.process is not None:
        process = (
            "step", "start", "end", "scale", "target_species", "feed", "product",
            "feed_moisture_basis", "product_moisture_basis", "intensity_denominator_stream",
            "conditions", "procedure", "sample_representativeness",
        )
        incompatible.update("process." + name for name in _differences(first.process, second.process, process))
    if first.quantity_kind == second.quantity_kind == "COST_INTENSITY":
        incompatible.update(_differences(first, second, ("cost_year", "cost_currency", "cost_price_basis")))
    fields = {
        "assessment_ids": tuple(sorted((left.assessment_id, right.assessment_id))),
        "missing_dimensions": tuple(sorted(missing)), "incompatible_dimensions": tuple(sorted(incompatible)),
    }
    statuses = {left.status, right.status}
    if SemanticStatus.SCIENTIFIC_CONFLICT in statuses:
        return SemanticCompatibility(
            **fields, status=SemanticStatus.SCIENTIFIC_CONFLICT, reason_codes=("INPUT_HAS_SCIENTIFIC_CONFLICT",),
        )
    if incompatible:
        return SemanticCompatibility(
            **fields, status=SemanticStatus.NOT_COMPARABLE,
            reason_codes=("INDEPENDENT_PROJECT_USE_CONTEXT_DIFFERS",),
        )
    if statuses == {SemanticStatus.DESCRIPTIVE_ONLY}:
        return SemanticCompatibility(**fields, status=SemanticStatus.DESCRIPTIVE_ONLY, reason_codes=("DESCRIPTIVE_USE_ONLY",))
    if missing or statuses - _COMPARABLE:
        return SemanticCompatibility(
            **fields, status=SemanticStatus.INSUFFICIENT_CONTEXT,
            reason_codes=("INPUTS_REQUIRE_COMPARABLE_USE_ASSESSMENTS",),
        )
    derived = SemanticStatus.COMPARABLE_DERIVED in {left.status, right.status}
    return SemanticCompatibility(
        **fields, status=SemanticStatus.COMPARABLE_DERIVED if derived else SemanticStatus.COMPARABLE_DIRECT,
        reason_codes=("INDEPENDENT_PROJECT_USE_CONTEXT_MATCHES",),
    )


def _differences(first, second, fields) -> tuple[str, ...]:
    return tuple(
        name for name in fields
        if not _unknown(getattr(first, name)) and not _unknown(getattr(second, name))
        and getattr(first, name) != getattr(second, name)
    )
