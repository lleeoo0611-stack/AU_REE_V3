"""Verify source-declared textual attributes for Census display only."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re

from .contracts import ContractViolation, Entity, EvidenceAnchor, Record, Source, canonical_json
from .contracts.temporal import TemporalDecision, evaluate_temporal
from .attribute_batches import (
    DEPOSIT_TYPES as NATIVE_DEPOSIT_TYPES, POLICY_SHA as NATIVE_POLICY_SHA,
    POLICY_VERSION as NATIVE_POLICY_VERSION, NativeClaimDeclined, load_routes, native_claim_matches, read_native_claim,
)
from .development import (
    DATED_STUDY_POLICY_V2, DATED_STUDY_POLICY_VERSION, SCHEMA as MILESTONES_SCHEMA, STUDY_RESULTS_RELEASED,
    STUDY_STAGES, bind_native_quote, calendar_precedes, dated_study_matches, dated_study_policy_definition,
    read_milestone_evidence, reported_calendar_date,
)
from .documents import DocumentCache, DocumentError
from .semantics import ConceptMapping, MatchRelation, resolve_concept
from .sources import ContentStore, IntegrityError, validate_edition_proofs

SCHEMA = "source-bound-project-attributes-v1"
POLICY_VERSION = "census-attribute-selection-v1"
ATTRIBUTE_EVIDENCE_STATE = "VALIDATED_DESCRIPTIVE"
_DECISION_SCOPE = "CENSUS_DISPLAY_ONLY"
_SHA = re.compile(r"[a-f0-9]{64}")
_DEPOSIT_TERMS = {"CARBONATITE_SUPERGENE_REGOLITH": "碳酸岩風化殼超基因富集型"}
_STAGE_TERMS = {
    "MRE": "已公布礦產資源估算", "Scoping": "已完成範疇研究", "PFS": "已完成預可行性研究",
    "DFS": "已完成最終可行性研究", "Construction": "已開始建設", "Production": "已開始生產",
}
_ROLES = {
    "DEPOSIT_TYPE": {"EXPLORATION_MODEL", "HOST_DESCRIPTION", "CONFIRMED_DEPOSIT_TYPE"},
    "DEVELOPMENT_STAGE": {"MENTIONED", "PLANNED", "ONGOING", "COMPLETED", "ATTAINED",
                          "CURRENT_AS_OF_CUTOFF", "NOT_COMPLETED", "INSUFFICIENT_CONTEXT", "EARLY_WORKS",
                          STUDY_RESULTS_RELEASED},
}
_SUPPORTED_ROLES = {"DEPOSIT_TYPE": {"CONFIRMED_DEPOSIT_TYPE"}, "DEVELOPMENT_STAGE": {"COMPLETED", "ATTAINED"}}
_SUPPORTED_STAGES = {"PFS", "DFS"}
_STUDY_RELEASE_LABELS = {"Scoping": "已公布範疇研究結果", "PFS": "已公布預可行性研究結果", "DFS": "已公布最終可行性研究結果"}
_DEPOSIT_PATTERNS = {
    "CARBONATITE_SUPERGENE_REGOLITH":
        r"rare earth deposit is hosted within a supergene enriched zone of the [^.]{1,90}Carbonatite[^.]{0,30} regolith",
}
_PROJECT_SCOPE = {"DEPOSIT_TYPE": "NAMED_PROJECT_DEPOSIT", "DEVELOPMENT_STAGE": "PROJECT"}
_PROGRESS = re.compile(r"\b(?:underway|in progress|ongoing)\b", re.I)
_CURRENT = re.compile(r"\bcurrent\s+as\s+(?:of|at)\b", re.I)
_PHASE = re.compile(r"\b(?:phase|stage)\s+(?:[IVX]+|[0-9]+)\b", re.I)
_DATED_STUDY_NARROW = re.compile(
    r"\b(?:(?:phase|stage)\s+(?:[IVX]+|[0-9]+|one|two|three)|pilot|bench-scale|demonstration)\b", re.I,
)
_TENTATIVE = re.compile(r"\b(?:may|might|could|interpreted|potential|target|exploration model)\b", re.I)
_POLICY = {"policy_version": POLICY_VERSION, "purpose": _DECISION_SCOPE, "roles": {
    key: sorted(roles) for key, roles in _SUPPORTED_ROLES.items()
}, "codes": {**_DEPOSIT_TERMS, **_STAGE_TERMS}, "deposit_patterns": _DEPOSIT_PATTERNS, "scopes": _PROJECT_SCOPE,
    "stage_codes_approved": sorted(_SUPPORTED_STAGES), "phase_scope_pattern": _PHASE.pattern}
_POLICY.update(progress_pattern=_PROGRESS.pattern, current_pattern=_CURRENT.pattern,
               tentative_pattern=_TENTATIVE.pattern)
POLICY_SHA = sha256(canonical_json(_POLICY).encode()).hexdigest()
_DATED_STUDY_POLICY_SHAS = {
    version: sha256(canonical_json({
        "policy_version": version,
        "purpose": _DECISION_SCOPE, "scope": _PROJECT_SCOPE["DEVELOPMENT_STAGE"],
        "code_labels": _STUDY_RELEASE_LABELS, "completion_labels": {key: _STAGE_TERMS[key] for key in STUDY_STAGES},
        "roles": ["COMPLETED", STUDY_RESULTS_RELEASED], "phase_scope_pattern": _DATED_STUDY_NARROW.pattern,
        "native_rules": dated_study_policy_definition(version),
    }).encode()).hexdigest()
    for version in (DATED_STUDY_POLICY_VERSION, DATED_STUDY_POLICY_V2)
}
DATED_STUDY_POLICY_SHA = _DATED_STUDY_POLICY_SHAS[DATED_STUDY_POLICY_VERSION]
_DATED_STUDY_VERSIONS = {digest: version for version, digest in _DATED_STUDY_POLICY_SHAS.items()}
_ATTRIBUTE_POLICIES = {
    POLICY_SHA: POLICY_VERSION, NATIVE_POLICY_SHA: NATIVE_POLICY_VERSION,
    **_DATED_STUDY_VERSIONS,
}
_DEPOSIT_LABELS = {**_DEPOSIT_TERMS, **{term: labels[0] for term, labels in NATIVE_DEPOSIT_TYPES.items()}}


def _digest(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def _identity(record, field):
    fields = asdict(record)
    supplied = fields.pop(field)
    expected = _digest([record.contract_version, fields])
    if supplied is not None and supplied != expected:
        raise ContractViolation(f"{field} differs from the exact record")
    object.__setattr__(record, field, expected)


def _sha_fields(*values):
    if any(not isinstance(value, str) or not _SHA.fullmatch(value) for value in values):
        raise ContractViolation("Attribute lineage requires exact SHA-256 identifiers")


@dataclass(frozen=True, kw_only=True)
class AttributeAssertion(Record):
    contract_version = "attribute-assertion-v1"

    entity_id: str
    entity_kind: str
    project_id: str
    attribute_kind: str
    source_sha256: str
    source_edition_id: str
    source_term: str
    controlled_term: str
    role: str
    scope: str
    scope_kind: str
    publication_date: str
    event_date: str | None
    event_date_token: str | None
    evidence_anchor_ids: tuple[str, ...]
    assertion_id: str | None = None

    def __post_init__(self):
        if self.entity_kind not in {"PROJECT", "DEPOSIT"} or not self.entity_id or not self.project_id:
            raise ContractViolation("Attribute assertion lacks Project or Deposit identity")
        if self.entity_kind == "PROJECT" and self.entity_id != self.project_id:
            raise ContractViolation("Project assertion entity and Project identities differ")
        terms = _DEPOSIT_LABELS if self.attribute_kind == "DEPOSIT_TYPE" else _STAGE_TERMS
        if (self.role not in _ROLES.get(self.attribute_kind, ()) or self.controlled_term not in terms
                or self.role == STUDY_RESULTS_RELEASED and self.controlled_term not in STUDY_STAGES):
            raise ContractViolation("Attribute assertion has an unsupported role or vocabulary term")
        if not all(isinstance(value, str) and value.strip() for value in (self.source_term, self.scope, self.scope_kind)):
            raise ContractViolation("Attribute assertion requires native text and scope")
        if not self.evidence_anchor_ids or len(set(self.evidence_anchor_ids)) != len(self.evidence_anchor_ids):
            raise ContractViolation("Attribute assertion requires individual anchors")
        _sha_fields(self.source_sha256, self.source_edition_id, *self.evidence_anchor_ids)
        object.__setattr__(self, "evidence_anchor_ids", tuple(self.evidence_anchor_ids))
        _identity(self, "assertion_id")


@dataclass(frozen=True, kw_only=True)
class ValidatedAttribute(Record):
    contract_version = "validated-attribute-v1"

    assertion_id: str
    policy_sha256: str
    temporal_decision_id: str
    temporal_status: str
    verdict: str
    reason: str
    validation_id: str | None = None

    def __post_init__(self):
        _sha_fields(self.assertion_id, self.policy_sha256, self.temporal_decision_id)
        if self.policy_sha256 not in _ATTRIBUTE_POLICIES or self.verdict not in {
            "VALIDATED", "CANDIDATE", "TEMPORAL_INELIGIBLE", "EVIDENCE_INSUFFICIENT", "SCIENTIFIC_CONFLICT",
        } or not self.reason:
            raise ContractViolation("Attribute assessment requires an approved policy and verdict")
        if self.verdict == "VALIDATED" and self.temporal_status != "ELIGIBLE":
            raise ContractViolation("Validated attributes require eligible temporal evidence")
        _identity(self, "validation_id")


@dataclass(frozen=True, kw_only=True)
class AttributeSelection(Record):
    contract_version = "attribute-selection-v1"

    release_id: str
    assertion_id: str
    validation_id: str
    policy_sha256: str
    profile_sha256: str
    source_manifest_sha256: str
    project_id: str
    attribute_kind: str
    purpose: str = _DECISION_SCOPE
    selection_id: str | None = None

    def __post_init__(self):
        _sha_fields(self.release_id, self.assertion_id, self.validation_id, self.policy_sha256,
                    self.profile_sha256, self.source_manifest_sha256)
        if (self.purpose != _DECISION_SCOPE or self.policy_sha256 not in _ATTRIBUTE_POLICIES
                or self.attribute_kind not in _ROLES or not self.project_id):
            raise ContractViolation("Attribute selection is restricted to approved Census display")
        _identity(self, "selection_id")


@dataclass(frozen=True)
class AttributeEvidence:
    assertion: AttributeAssertion
    source: Source
    anchors: tuple[EvidenceAnchor, ...]
    temporal: TemporalDecision
    assessment: ValidatedAttribute

    def __post_init__(self):
        ids = {anchor.anchor_id for anchor in self.anchors}
        if not (
            self.source.sha256 == self.assertion.source_sha256 == self.temporal.source_sha256
            and all(anchor.source_sha == self.source.sha256 and anchor.source_key == self.source.source_key
                    for anchor in self.anchors)
            and ids == set(self.assertion.evidence_anchor_ids)
            and self.assessment.assertion_id == self.assertion.assertion_id
            and self.assessment.temporal_decision_id == self.temporal.decision_id
            and self.assessment.temporal_status == self.temporal.status
        ):
            raise ContractViolation("Attribute assertion, evidence and validation belong to different identities")


def _verdict(assertion, temporal, native_role_reason, policy_sha=POLICY_SHA):
    if temporal.status != "ELIGIBLE":
        return {"CONFLICT": "SCIENTIFIC_CONFLICT", "INELIGIBLE": "TEMPORAL_INELIGIBLE"}.get(
            temporal.status, "EVIDENCE_INSUFFICIENT",
        ), temporal.reason
    dated = policy_sha in _DATED_STUDY_VERSIONS
    roles = _SUPPORTED_ROLES[assertion.attribute_kind]
    if dated and assertion.attribute_kind == "DEVELOPMENT_STAGE":
        roles = {"COMPLETED", STUDY_RESULTS_RELEASED}
    if assertion.role not in roles:
        return "CANDIDATE", native_role_reason
    if assertion.attribute_kind == "DEPOSIT_TYPE":
        terms = NATIVE_DEPOSIT_TYPES if policy_sha == NATIVE_POLICY_SHA else _DEPOSIT_TERMS
        if assertion.controlled_term not in terms:
            return "CANDIDATE", "DEPOSIT_TYPE_OUTSIDE_CENSUS_DISPLAY_POLICY"
    if assertion.attribute_kind == "DEVELOPMENT_STAGE":
        stages = STUDY_STAGES if dated else _SUPPORTED_STAGES
        if assertion.controlled_term not in stages:
            return "CANDIDATE", "STAGE_OUTSIDE_CENSUS_DISPLAY_POLICY"
        narrow = _DATED_STUDY_NARROW if dated else _PHASE
        if narrow.search(assertion.scope) or narrow.search(assertion.source_term):
            return "EVIDENCE_INSUFFICIENT", "NARROWER_REPORT_SCOPE_THAN_CENSUS_PROJECT"
    if assertion.scope_kind != _PROJECT_SCOPE[assertion.attribute_kind]:
        return "EVIDENCE_INSUFFICIENT", "NARROWER_REPORT_SCOPE_THAN_CENSUS_PROJECT"
    mapping = ConceptMapping(
        original=assertion.source_term, canonical=assertion.controlled_term, relation=MatchRelation.EXACT,
        mapping_version=_ATTRIBUTE_POLICIES[policy_sha], source_sha256=assertion.source_sha256,
        evidence_anchor_ids=assertion.evidence_anchor_ids,
        applicable_context=(("kind", assertion.attribute_kind), ("role", assertion.role),
                            ("scope_kind", assertion.scope_kind)),
    )
    resolution = resolve_concept(mapping, context={"kind": assertion.attribute_kind, "role": assertion.role,
                                                   "scope_kind": assertion.scope_kind})
    if resolution is None:
        return "EVIDENCE_INSUFFICIENT", "NO_CONTEXT_EXACT_TEXT_MAPPING"
    return "VALIDATED", "SOURCE_CONFIRMED_ATTRIBUTE_FOR_CENSUS_DISPLAY"


def _deposit_claim(binding, edition, document, proofs, cutoff):
    key, page = edition["source_key"], binding["page"]
    statement = bind_native_quote(document, key, page, binding["quote"], "DEPOSIT_TYPE_CLAIM")
    scope = bind_native_quote(document, key, page, binding["scope_quote"], "DEPOSIT_PROJECT_SCOPE")
    if not (statement.text_span["start"] <= scope.text_span["start"]
            < scope.text_span["end"] <= statement.text_span["end"]):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "DEPOSIT_SCOPE_NOT_IN_TYPE_QUOTE")
    publication = binding["publication"]
    date_quote = bind_native_quote(document, key, publication["page"], publication["quote"], "DEPOSIT_PUBLICATION_DATE")
    date_token = publication["date_token"]
    if date_token not in date_quote.quote:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "PUBLICATION_DATE_NOT_BOUND_TO_QUOTE")
    publication_date = reported_calendar_date(date_token)
    role, reason = "HOST_DESCRIPTION", "GEOLOGICAL_HOST_DESCRIPTION_ONLY"
    anchors = [statement, scope, date_quote]
    if _TENTATIVE.search(statement.quote):
        role, reason = "EXPLORATION_MODEL", "TENTATIVE_GEOLOGICAL_INTERPRETATION"
    elif re.search(_DEPOSIT_PATTERNS[binding["term"]], statement.quote, re.I):
        classification = bind_native_quote(
            document, key, page, binding["classification_quote"], "JORC_DEPOSIT_TYPE_SECTION",
        )
        if "Deposit type, geological setting" not in classification.quote or not (
            0 <= statement.text_span["start"] - classification.text_span["end"] <= 160
        ):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "DEPOSIT_TYPE_SECTION_NOT_LOCAL_TO_CLAIM")
        anchors.append(classification)
        role, reason = "CONFIRMED_DEPOSIT_TYPE", "ISSUER_CONFIRMED_DEPOSIT_TYPE"
    temporal = evaluate_temporal(
        source_sha256=edition["sha256"], cutoff=cutoff, observation_role="DOCUMENT",
        publication_date=publication_date, reported_publication_date=publication_date,
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proofs,
    )
    return role, reason, None, None, publication_date, anchors, temporal


def _attribute_evidence(binding, kind, entity, edition, document, proofs, cutoff):
    policy, scope, scope_kind = POLICY_SHA, binding.get("scope"), binding.get("scope_kind")
    term = binding.get("term", binding.get("stage"))
    if "adapter" in binding:
        claim = read_native_claim(binding, edition, document)
        policy, scope, scope_kind = NATIVE_POLICY_SHA, claim.scope, claim.scope_kind
        term, role = claim.term, claim.role
        reason = "SOURCE_CONFIRMED_NATIVE_ATTRIBUTE"
        event, token, publication, anchors = claim.event_date, claim.event_token, claim.publication_date, claim.anchors
        temporal = evaluate_temporal(
            source_sha256=edition["sha256"], cutoff=cutoff,
            observation_role="HISTORICAL_EVENT" if kind == "DEVELOPMENT_STAGE" else "DOCUMENT",
            publication_date=publication, reported_publication_date=publication, event_date=event,
            acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proofs,
        )
    elif kind == "DEPOSIT_TYPE":
        role, reason, event, token, publication, anchors, temporal = _deposit_claim(
            binding, edition, document, proofs, cutoff,
        )
    else:
        policy = _DATED_STUDY_POLICY_SHAS.get(binding.get("event_policy"), POLICY_SHA)
        role, reason, _, event, token, publication, anchors, temporal = read_milestone_evidence(
            binding, edition, document, proofs, cutoff,
        )
        role, reason = _development_role(role, reason, anchors[0].quote, binding.get("current_as_of_token"), cutoff)
    if edition.get("publication_date") and edition["publication_date"] != publication:
        raise DocumentError("SCIENTIFIC_CONFLICT", "NATIVE_DATE_AND_SOURCE_MANIFEST_DISAGREE")
    source = Source(
        source_key=edition["source_key"], url=edition["url"], sha256=edition["sha256"], kind=edition["kind"],
        source_version=edition["source_version"], publication_date=publication, acquired_at=edition["acquired_at"],
        reacquired_at=edition["reacquired_at"],
    )
    assertion = AttributeAssertion(
        entity_id=entity.entity_id, entity_kind=entity.kind, project_id=entity.entity_id, attribute_kind=kind,
        source_sha256=source.sha256, source_edition_id=edition["edition_id"], source_term=anchors[0].quote,
        controlled_term=term, role=role, scope=scope, scope_kind=scope_kind, publication_date=publication,
        event_date=event, event_date_token=token, evidence_anchor_ids=tuple(anchor.anchor_id for anchor in anchors),
    )
    verdict, reason = _verdict(assertion, temporal, reason, policy)
    validation = ValidatedAttribute(
        assertion_id=assertion.assertion_id, policy_sha256=policy, temporal_decision_id=temporal.decision_id,
        temporal_status=temporal.status, verdict=verdict, reason=reason,
    )
    return AttributeEvidence(assertion, source, tuple(anchors), temporal, validation)


def _development_role(role, reason, quote, current_as_of_token, cutoff):
    if role != "MENTIONED":
        return role, reason
    if current_as_of_token and re.search(rf"{_CURRENT.pattern}\s+{re.escape(current_as_of_token)}\b", quote, re.I):
        if reported_calendar_date(current_as_of_token) == cutoff:
            return "CURRENT_AS_OF_CUTOFF", "EXPLICIT_CURRENT_AT_CUTOFF_LANGUAGE"
    if _PROGRESS.search(quote):
        return "ONGOING", "EXPLICIT_IN_PROGRESS_LANGUAGE"
    return role, reason


def _eligible_cases(spec, milestones):
    references = {case["case_id"]: case for case in milestones["bindings"]}
    cases = [(kind, binding) for kind, bindings in (
        ("DEPOSIT_TYPE", spec["deposit_types"]),
        ("DEVELOPMENT_STAGE", [{**references[row["case_id"]], "scope_kind": row["scope_kind"],
                                "current_as_of_token": row.get("current_as_of_token")}
                               for row in spec["development_milestones"]]),
    ) for binding in bindings]
    if len({binding["case_id"] for _, binding in cases}) != len(cases):
        raise ContractViolation("Duplicate attribute case ID")
    return cases


def _retained_earlier_studies(cases, evidence_by_case):
    retained = {}
    for kind, binding in cases:
        if (kind != "DEVELOPMENT_STAGE" or binding.get("event_policy") not in _DATED_STUDY_POLICY_SHAS
                or not binding.get("later_study_case_id") or binding["case_id"] not in evidence_by_case):
            continue
        earlier_case, later_case = binding["case_id"], binding["later_study_case_id"]
        first, second = evidence_by_case[earlier_case], evidence_by_case.get(later_case)
        if second is None or any(item.assessment.verdict != "VALIDATED" for item in (first, second)):
            continue
        old, new = first.assertion, second.assertion
        old_scope = (old.project_id, old.scope_kind, old.scope, old.controlled_term)
        new_scope = (new.project_id, new.scope_kind, new.scope, new.controlled_term)
        if (old_scope == new_scope and old.role == new.role == "COMPLETED"
                and calendar_precedes(old.event_date, new.event_date)):
            retained[earlier_case] = later_case
    return retained


def _select_attributes(evidence_by_case, release, profile_sha, manifest_sha, historical_cases=()):
    choices = defaultdict(list)
    for case_id, evidence in evidence_by_case.items():
        assertion = evidence.assertion
        if evidence.assessment.verdict == "VALIDATED" and case_id not in historical_cases:
            choices[assertion.project_id, assertion.attribute_kind].append((case_id, evidence))
    selected, unresolved = [], {}
    for (project, kind), candidates in sorted(choices.items()):
        exact = {evidence.assertion.assertion_id for _, evidence in candidates}
        if len(exact) != 1:
            unresolved[project, kind] = "CONFLICTING_ELIGIBLE_ATTRIBUTE_ASSERTIONS"
            continue
        case_id, evidence = min(candidates, key=lambda pair: pair[0])
        selected.append((case_id, evidence, AttributeSelection(
            release_id=release, project_id=project, attribute_kind=kind, policy_sha256=evidence.assessment.policy_sha256,
            assertion_id=evidence.assertion.assertion_id, validation_id=evidence.assessment.validation_id,
            profile_sha256=profile_sha, source_manifest_sha256=manifest_sha,
        )))
    return selected, unresolved


def _attribute_values(assertion):
    code = assertion.controlled_term
    if assertion.attribute_kind == "DEPOSIT_TYPE":
        return {"deposit_type_code": code, "deposit_type": _DEPOSIT_LABELS[code]}
    return _stage_values(code, assertion.role, assertion.event_date_token, assertion.scope)


def _stage_values(code, role, date_token, scope):
    labels = _STUDY_RELEASE_LABELS if role == STUDY_RESULTS_RELEASED else _STAGE_TERMS
    return {"development_stage_code": code, "development_stage_category": labels[code],
            "development_stage": f"{labels[code]}（{date_token} · {scope}）"}


def matches_public_attribute_role(kind, column_id, value, scope, metadata):
    """Verify public labels against the historical and dated study roles."""
    role = metadata.get("attribute_role")
    if metadata.get("policy_sha256") not in _DATED_STUDY_VERSIONS:
        return role == ("CONFIRMED_DEPOSIT_TYPE" if kind == "DEPOSIT_TYPE" else "COMPLETED")
    if kind != "DEVELOPMENT_STAGE" or role not in {"COMPLETED", STUDY_RESULTS_RELEASED} or not scope:
        return False
    date_token = metadata.get("event_date_token")
    publication = metadata.get("publication_date")
    if not isinstance(date_token, str) or not isinstance(publication, str) or not publication:
        return False
    try:
        event = reported_calendar_date(date_token)
    except ValueError:
        return False
    return bool(event and not calendar_precedes(publication, event)) and any(
        _stage_values(code, role, date_token, scope).get(column_id) == value
               for code in _STUDY_RELEASE_LABELS)


def _dated_attribute_matches(assertion, anchors, temporal, policy):
    publication = next((anchor for anchor in anchors if anchor.role == "DEVELOPMENT_PUBLICATION_DATE"), None)
    return (
        assertion.attribute_kind == "DEVELOPMENT_STAGE"
        and anchors[0].role == "DEVELOPMENT_STAGE_CLAIM"
        and assertion.event_date == reported_calendar_date(assertion.event_date_token) == temporal["event_date"]
        and temporal["observation_role"] == "HISTORICAL_EVENT"
        and not calendar_precedes(assertion.publication_date, assertion.event_date)
        and dated_study_matches(assertion.controlled_term, assertion.role,
                                assertion.event_date_token, anchors[0], publication, policy=policy)
    )


def _project_attribute(evidence, selection, receipt_sha):
    assertion, source, temporal = evidence.assertion, evidence.source, evidence.temporal
    anchor = evidence.anchors[0]
    policy = evidence.assessment.policy_sha256
    provenance = {
        "evidence_type": "SOURCE_BOUND_TEXT_ATTRIBUTE", "policy_sha256": policy, "display_only": True,
        "source_capture_sha256": receipt_sha, "source_manifest_sha256": selection.source_manifest_sha256,
        "attribute_kind": assertion.attribute_kind, "attribute_role": assertion.role,
        "publication_date": assertion.publication_date, "event_date_token": assertion.event_date_token,
        "native_value": assertion.source_term, "assertion": assertion.to_dict(),
        "assessment": evidence.assessment.to_dict(), "selection": selection.to_dict(),
        "temporal_decision": temporal.to_dict(),
        "anchors": [ref.to_dict() for ref in evidence.anchors],
    }
    common = {
        "value_origin": "SOURCE_EVIDENCE", "value_state": "PRESENT", "evidence_state": ATTRIBUTE_EVIDENCE_STATE,
        "reason": "SOURCE_CONFIRMED_ATTRIBUTE_FOR_CENSUS_DISPLAY", "source_sha256": source.sha256,
        "source_key": source.source_key, "source_url": source.url, "anchor_id": anchor.anchor_id,
        "locator": anchor.locator, "physical_page": anchor.page, "scope": assertion.scope,
        "rules_sha256": policy, "release_id": selection.release_id,
        "selection_id": selection.selection_id, "validation_id": evidence.assessment.validation_id,
        "temporal_decision_id": temporal.decision_id, "temporal_status": temporal.status,
        "temporal_evidence_ids": canonical_json(temporal.matching_evidence_ids),
        "temporal_policy_version": temporal.policy_version, "metadata_evidence": canonical_json(provenance),
    }
    return {(assertion.project_id, key): {**common, "value": value} for key, value in _attribute_values(assertion).items()}


def validate_attribute_cell(cell):
    """Check text display lineage without treating an attribute as a numeric observation."""
    saved = json.loads(cell["metadata_evidence"])
    if saved["evidence_type"] != "SOURCE_BOUND_TEXT_ATTRIBUTE" or saved["display_only"] is not True:
        raise ContractViolation("Census attribute metadata is not display-only source evidence")
    assertion = AttributeAssertion.from_dict(saved["assertion"])
    assessment = ValidatedAttribute.from_dict(saved["assessment"])
    selection = AttributeSelection.from_dict(saved["selection"])
    anchors = tuple(EvidenceAnchor.from_dict(row) for row in saved["anchors"])
    temporal = saved["temporal_decision"]
    policy = assessment.policy_sha256
    if not anchors or len({a.anchor_id for a in anchors}) != len(anchors):
        raise ContractViolation("Census attribute lacks unique source anchors")
    _sha_fields(saved["source_capture_sha256"])
    matched = {
        "project_id": assertion.project_id, "source_sha256": assertion.source_sha256,
        "scope": assertion.scope, "value": _attribute_values(assertion).get(cell["column_id"]),
        "rules_sha256": policy, "value_origin": "SOURCE_EVIDENCE", "evidence_state": ATTRIBUTE_EVIDENCE_STATE,
        "selection_id": selection.selection_id, "validation_id": assessment.validation_id,
        "release_id": selection.release_id, "anchor_id": anchors[0].anchor_id, "locator": anchors[0].locator,
        "temporal_decision_id": assessment.temporal_decision_id, "temporal_status": "ELIGIBLE",
        "temporal_policy_version": temporal["policy_version"], "source_key": anchors[0].source_key,
        "temporal_evidence_ids": canonical_json(temporal["matching_evidence_ids"]),
    }
    if not (
        all(cell[key] == value for key, value in matched.items())
        and str(cell["physical_page"]) == str(anchors[0].page)
        and saved["policy_sha256"] == policy == selection.policy_sha256
        and assertion.source_term == anchors[0].quote
        and assertion.source_sha256 == temporal["source_sha256"]
        and {anchor.anchor_id for anchor in anchors} == set(assertion.evidence_anchor_ids)
        and all(anchor.source_sha == assertion.source_sha256 and anchor.source_key == cell["source_key"] for anchor in anchors)
        and assertion.assertion_id == assessment.assertion_id == selection.assertion_id
        and selection.validation_id == assessment.validation_id
        and (selection.project_id, selection.attribute_kind) == (assertion.project_id, assertion.attribute_kind)
        and temporal["decision_id"] == assessment.temporal_decision_id == _digest({
            key: value for key, value in temporal.items() if key != "decision_id"
        }) and temporal["status"] == "ELIGIBLE" and temporal["matching_evidence_ids"]
        and assessment.verdict == "VALIDATED" and not cell.get("observation_id")
        and _verdict(assertion, TemporalDecision(**temporal), "", policy)[0] == "VALIDATED"
        and (policy != NATIVE_POLICY_SHA or native_claim_matches(assertion, anchors))
        and (policy not in _DATED_STUDY_VERSIONS
             or _dated_attribute_matches(assertion, anchors, temporal, _DATED_STUDY_VERSIONS[policy]))
        and cell.get("comparable") in (None, "", "NO")
        and saved["source_manifest_sha256"] == selection.source_manifest_sha256
        and saved["attribute_kind"] == assertion.attribute_kind and saved["attribute_role"] == assertion.role
        and saved["publication_date"] == assertion.publication_date
        and saved["event_date_token"] == assertion.event_date_token and saved["native_value"] == assertion.source_term
    ):
        raise ContractViolation("Census attribute lineage differs from its selected source assertion")


def review_attributes(settings, registry, manifest, manifest_sha, source_receipt, assets, release_id):
    """Build release-bound text evidence using the ordinary source, PDF and temporal contracts."""
    if not assets or manifest is None or not source_receipt or not release_id:
        raise ContractViolation("Attribute projection requires an exact manifest, assets and research release")
    pins = settings.attribute_bindings_path, settings.attribute_milestones_path
    spec_data, milestones_data = (path.read_bytes() for path in pins)
    spec, milestones = (json.loads(data) for data in (spec_data, milestones_data))
    if (spec["schema"] != SCHEMA or milestones["schema"] != MILESTONES_SCHEMA
            or spec["source_manifest_sha256"] != manifest_sha or milestones["source_manifest_sha256"] != manifest_sha):
        raise ContractViolation("Attribute bindings are for another source manifest")
    editions = {edition["receipt_sha256"]: edition for edition in manifest["editions"]}
    verified = set(source_receipt["verified_capture_sha256"])
    projects = {project.project_id: project for project in registry.projects}
    content = ContentStore(assets)
    cache = DocumentCache()
    documents, proofs_by_receipt, results, evidence_by_case, captures = {}, {}, [], {}, {}
    cases = _eligible_cases(spec, milestones)
    batch_data = settings.attribute_batches_path.read_bytes() if settings.attribute_batches_path else None
    if batch_data:
        cases += load_routes(batch_data, manifest_sha)
    for kind, binding in cases:
        case_id, project, digest, capture = (binding[key] for key in
                                             ("case_id", "project_id", "source_sha256", "receipt_sha256"))
        result = {"case_id": case_id, "project_id": project, "attribute_kind": kind, "source_sha256": digest,
                  "capture_sha256": capture, "page": binding["page"], "selection": None}
        if "adapter" in binding:
            result["adapter"] = binding["adapter"]
        if project not in projects:
            raise ContractViolation("Attribute binding Project ID lies outside the pinned cohort")
        try:
            edition = editions.get(capture)
            if edition is None or capture not in verified:
                raise DocumentError("SOURCE_UNAVAILABLE", "VERIFIED_ATTRIBUTE_SOURCE_CAPTURE_MISSING")
            if edition["sha256"] != digest or project not in edition["project_ids"] or edition["kind"] != "pdf":
                raise DocumentError("SCIENTIFIC_CONFLICT", "ATTRIBUTE_SOURCE_EDITION_OR_PROJECT_MISMATCH")
            if capture not in proofs_by_receipt:
                proofs_by_receipt[capture] = validate_edition_proofs(content, edition, settings.timezone)
            if digest not in documents:
                documents[digest] = cache.pdf(content.read(digest), digest)
            entity = Entity(entity_id=project, kind="PROJECT", native_namespace="project_registry_v1",
                            native_id=project, name=projects[project].name)
            item = _attribute_evidence(binding, kind, entity, edition, documents[digest], proofs_by_receipt[capture],
                                       settings.research_cutoff.isoformat())
        except NativeClaimDeclined as error:
            result.update(verdict=error.outcome, reason=str(error), review_source_url=edition["url"],
                          review_evidence=[anchor.to_dict() for anchor in error.anchors])
        except DocumentError as error:
            result.update(verdict=error.outcome, reason=str(error))
        except OSError:
            result.update(verdict="SOURCE_UNAVAILABLE", reason="ATTRIBUTE_SOURCE_OR_PROOF_BYTES_UNAVAILABLE")
        except IntegrityError:
            result.update(verdict="SCIENTIFIC_CONFLICT", reason="ATTRIBUTE_SOURCE_OR_PROOF_SHA_MISMATCH")
        else:
            evidence_by_case[case_id] = item
            captures[case_id] = capture
            result.update(verdict=item.assessment.verdict, reason=item.assessment.reason,
                          assertion=item.assertion.to_dict(), assessment=item.assessment.to_dict(),
                          source=item.source.to_dict(), temporal_decision=item.temporal.to_dict(),
                          evidence=[anchor.to_dict() for anchor in item.anchors])
        results.append(result)
    historical_cases = _retained_earlier_studies(cases, evidence_by_case)
    selected, unresolved = _select_attributes(
        evidence_by_case, release_id, settings.sha256, manifest_sha, historical_cases,
    )
    selections = {case: selection.to_dict() for case, _, selection in selected}
    cells = {key: cell for case, evidence, selection in selected
             for key, cell in _project_attribute(evidence, selection, captures[case]).items()}
    for row in results:
        row["selection"] = selections.get(row["case_id"])
        if row["case_id"] in historical_cases and row["verdict"] == "VALIDATED":
            row["selection_blocker"] = "HISTORICAL_MILESTONE_RETAINED"
            row["later_study_case_id"] = historical_cases[row["case_id"]]
        conflict = unresolved.get((row["project_id"], row["attribute_kind"]))
        if conflict and row["verdict"] == "VALIDATED" and row["case_id"] not in historical_cases:
            row["selection_blocker"] = conflict
    receipt = {
        "schema": SCHEMA, "policy_version": POLICY_VERSION, "policy_sha256": POLICY_SHA,
        "source_manifest_sha256": manifest_sha, "bindings_sha256": sha256(spec_data).hexdigest(),
        "milestone_bindings_sha256": sha256(milestones_data).hexdigest(), "release_id": release_id,
        "cases": results, "verdicts": dict(Counter(row["verdict"] for row in results)),
        "roles": dict(Counter(row["assertion"]["role"] for row in results if "assertion" in row)),
        "selected_attributes": len(selected), "display_cells": len(cells), "mcda_inputs_added": 0,
    }
    if batch_data:
        receipt.update(batch_routes_sha256=sha256(batch_data).hexdigest(), batch_policy_sha256=NATIVE_POLICY_SHA)
    return cells, receipt
