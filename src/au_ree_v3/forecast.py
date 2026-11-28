"""Verify native study production scenarios as isolated Census shadow observations."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from .contracts import RawObservation, canonical_json
from .contracts.temporal import evaluate_temporal
from .development import _anchor, _date_token
from .documents import DocumentCache, DocumentError
from .normalization import reported_interval
from .observations import NUMBER
from .registry import load_profile, load_registry
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_SCHEMA, validate_edition_proofs


SCHEMA = "source-bound-production-forecasts-v1"


@dataclass(frozen=True, slots=True)
class StudyScenario:
    role: str
    quantity: str
    product: str
    unit: str
    material: str
    chemistry: str | None
    pattern: re.Pattern


_SCENARIOS = {
    "BASE_CASE_LIFE_OF_MINE_AVERAGE": StudyScenario(
        "FORECAST", "annual_contained_treo_in_mrec", "CONTAINED_TREO_IN_MREC", "tonnes per annum", "MREC", "TREO",
        re.compile(
            rf"\bBase Case financial model assum(?:es|ing) a life-of-mine average of "
            rf"(?P<qualifier>approximately )?(?P<token>{NUMBER}) tonnes per annum of contained TREO in MREC\b",
            re.I,
        ),
    ),
    "NOMINAL_PROCESSING_CAPACITY": StudyScenario(
        "ENGINEERING_PLAN", "annual_contained_treo_in_mrec", "CONTAINED_TREO_IN_MREC", "tonnes per annum", "MREC", "TREO",
        re.compile(
            rf"\bprocess design basis provides for production of MREC containing "
            rf"(?P<qualifier>approximately )?(?P<token>{NUMBER}) tonnes per annum of TREO at nominal processing capacity\b",
            re.I,
        ),
    ),
    "BASE_CASE_WET_FLOTATION_CONCENTRATE": StudyScenario(
        "FORECAST", "annual_wet_flotation_concentrate", "WET_FLOTATION_CONCENTRATE", "wet tonnes per annum",
        "FLOTATION_CONCENTRATE", None,
        re.compile(
            rf"\bselected base case treats {NUMBER} Mtpa of ROM ore and produces "
            rf"(?P<qualifier>approximately )?(?P<token>{NUMBER}) wet tonnes per annum of "
            rf"heavy rare earth enriched flotation concentrate\b",
            re.I,
        ),
    ),
}
_LIFETIME = re.compile(r"\blife-of-mine MREC production tonnes (?:" + NUMBER + r")\b", re.I)


def _fingerprint(value) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _quote_anchors(document, source_key, binding, project_name):
    native = binding["statement"]
    statement = _anchor(document, source_key, native["page"], native["quote"], "STUDY_PRODUCTION_CLAUSE")
    scope = _anchor(document, source_key, native["page"], native["scope_quote"], "STUDY_PRODUCTION_SCOPE")
    gap = max(statement.text_span["start"], scope.text_span["start"]) - min(
        statement.text_span["end"], scope.text_span["end"],
    )
    if gap > 160:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "STUDY_SCOPE_NOT_LOCAL_TO_PRODUCTION_CLAUSE")
    release = binding["publication"]
    publication = _anchor(
        document, source_key, release["page"], release["quote"], "STUDY_PRODUCTION_PUBLICATION",
    )
    if release["date_token"] not in publication.quote:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "STUDY_RELEASE_DATE_NOT_BOUND_TO_QUOTE")
    identity = binding["project_identity"]
    project = _anchor(document, source_key, identity["page"], identity["quote"], "STUDY_PROJECT_IDENTITY")
    marker = re.escape(identity["marker"])
    if not marker or not all(re.match(rf"^{marker}\b", label, re.I) for label in (project_name, project.quote, binding["study"])):
        raise DocumentError("SCIENTIFIC_CONFLICT", "NATIVE_STUDY_PROJECT_IDENTITY_MISMATCH")
    return statement, scope, publication, project


def _review(document, edition, binding, proofs, cutoff, project_name):
    key, digest = edition["source_key"], edition["sha256"]
    statement, scope, publication, project = anchors = _quote_anchors(document, key, binding, project_name)
    scenario = _SCENARIOS[binding["scenario"]]
    if scenario.role != binding["role"]:
        raise DocumentError("SCIENTIFIC_CONFLICT", "PRODUCTION_ROLE_AND_SCENARIO_MISMATCH")
    matches = list(scenario.pattern.finditer(statement.quote))
    if len(matches) != 1:
        reason = ("LIFETIME_PRODUCT_MASS_IS_NOT_ANNUAL_CONTAINED_TREO" if _LIFETIME.search(statement.quote)
                  else "EXACT_ANNUAL_TREO_PRODUCT_AND_SCENARIO_UNBOUND")
        return {"status": "REJECTED", "reason": reason, "evidence": [item.to_dict() for item in anchors]}
    match, = matches
    rate_anchor = _anchor(document, key, statement.page, match[0], "STUDY_ANNUAL_RATE_VALUE")
    token = match["token"]
    point, low, high = reported_interval(token)
    if point <= 0:
        raise DocumentError("SCIENTIFIC_CONFLICT", "ANNUAL_PRODUCTION_RATE_MUST_BE_POSITIVE")
    publication_date = _date_token(binding["publication"]["date_token"])
    decision = evaluate_temporal(
        source_sha256=digest, cutoff=cutoff, observation_role=scenario.role, publication_date=publication_date,
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proofs,
    )
    context = {
        "scenario": binding["scenario"], "study": binding["study"],
        "product_basis": scenario.product, "chemical_basis": scenario.chemistry,
        "study_publication_date": publication_date, "role": scenario.role, "annual_rate_basis": scenario.unit,
        "source_edition_id": edition["edition_id"], "precision_qualifier": match["qualifier"].strip()
        if match["qualifier"] else None,
        "printed_decimal_interval": [format(low, "f"), format(high, "f")],
        "forecast_uncertainty_interval": None,
        "scope_anchor_id": scope.anchor_id, "publication_anchor_id": publication.anchor_id,
        "project_anchor_id": project.anchor_id, "statement_anchor_id": statement.anchor_id,
    }
    scope_id = _fingerprint([SCHEMA, binding["project_id"], binding["study"], binding["scenario"], scenario.product])
    observation = RawObservation(
        observation_id=_fingerprint([SCHEMA, scope_id, rate_anchor.anchor_id, scenario.role, token]),
        project_id=binding["project_id"], scope_id=scope_id,
        source_key=key, source_sha=digest, native_raw_id=None, id_version=SCHEMA,
        variable=scenario.quantity, quantity_kind="PRODUCTION_MASS_RATE",
        raw_value=token, raw_unit=scenario.unit, status="SHADOW", transform_version=SCHEMA,
        anchor_id=rate_anchor.anchor_id, material_basis=scenario.material, chemical_basis=scenario.chemistry,
        denominator_basis="YEAR",
        reported_publication_date=publication_date, acquisition_date=edition["acquired_at"],
        observation_role=scenario.role, original_payload=canonical_json(context),
    )
    return {
        "status": "VERIFIED_SHADOW" if decision.status == "ELIGIBLE" else "CANDIDATE",
        "reason": "NATIVE_ANNUAL_STUDY_RATE_VERIFIED" if decision.status == "ELIGIBLE" else decision.reason,
        "evidence": [item.to_dict() for item in (rate_anchor, *anchors)], "source_url": edition["url"],
        "temporal_decision": decision.to_dict(), "temporal_evidence": [asdict(item) for item in proofs],
        "scenario_context": context, "raw_observation": observation.to_dict(),
    }


def _select_shadows(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row["status"] == "VERIFIED_SHADOW":
            raw = row["raw_observation"]
            grouped[raw["source_sha"], raw["scope_id"]].append(row)
    selected = set()
    for choices in grouped.values():
        quantities = {choice["raw_observation"]["raw_value"] for choice in choices}
        if len(quantities) == 1:
            selected.add(min(choice["raw_observation"]["observation_id"] for choice in choices))
        else:
            for choice in choices:
                choice.update(status="SCIENTIFIC_CONFLICT", reason="SAME_EDITION_SCENARIO_RATES_DISAGREE")
    return sorted(selected)


def review_production_forecasts(profile: Path, sources: Path, assets: Path, bindings: Path, out: Path) -> dict:
    """Read exact PDF editions without writing a formal release or selection store."""
    if out.resolve().is_relative_to(assets.resolve()) or out.resolve() in {
        path.resolve() for path in (profile, sources, bindings)
    }:
        raise ValueError("Forecast receipt requires an output path separate from locked inputs")
    settings = load_profile(profile)
    cutoff = settings.research_cutoff.isoformat()
    registry = {project.project_id: project.name for project in load_registry(settings.registry_path).projects}
    manifest_bytes, binding_bytes = sources.read_bytes(), bindings.read_bytes()
    manifest_sha = sha256(manifest_bytes).hexdigest()
    manifest, spec = json.loads(manifest_bytes), json.loads(binding_bytes)
    if (spec["schema"] != SCHEMA or spec["source_manifest_sha256"] != manifest_sha
            or manifest["schema"] != SOURCE_SCHEMA
            or (manifest["profile_id"], manifest["cutoff"], manifest["timezone"])
            != (settings.profile_id, cutoff, settings.timezone.key)):
        raise ValueError("Production study bindings and exact source profile differ")
    cases = spec["cases"]
    if not cases or len({row["case_id"] for row in cases}) != len(cases):
        raise ValueError("Production study cases require unique identities")
    if any(item["project_id"] not in registry or item["scenario"] not in _SCENARIOS for item in cases):
        raise ValueError("Production study cases require registered Projects and declared scenarios")

    editions = {item["receipt_sha256"]: item for item in manifest["editions"]}
    required = {}
    for case in cases:
        digest = case["source_sha256"]
        required[digest] = max(required.get(digest, 0), *(case[name]["page"] for name in (
            "statement", "publication", "project_identity",
        )))
    content, cache = ContentStore(assets), DocumentCache()
    views, capture_proofs, rows = {}, {}, []
    for binding in cases:
        project, digest, capture = (binding[key] for key in ("project_id", "source_sha256", "receipt_sha256"))
        row = {"case_id": binding["case_id"], "project_id": project, "source_sha256": digest,
               "receipt_sha256": capture, "study": binding["study"], "role": binding["role"],
               "scenario": binding["scenario"], "formal_selection": False, "formal_mcda": False}
        try:
            edition = editions.get(capture)
            if edition is None:
                raise DocumentError("SOURCE_UNAVAILABLE", "IMMUTABLE_STUDY_EDITION_MISSING")
            if edition["sha256"] != digest or project not in edition["project_ids"] or edition["kind"] != "pdf":
                raise DocumentError("SCIENTIFIC_CONFLICT", "STUDY_EDITION_OR_PROJECT_MISMATCH")
            if capture not in capture_proofs:
                capture_proofs[capture] = validate_edition_proofs(content, edition, settings.timezone)
            proofs = capture_proofs[capture]
            if not proofs:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "STUDY_EXACT_PUBLIC_PROOF_MISSING")
            if digest not in views:
                views[digest] = cache.pdf(content.read(digest), digest, required[digest])
            row.update(_review(views[digest], edition, binding, proofs, cutoff, registry[project]))
        except DocumentError as error:
            row.update(status=error.outcome, reason=str(error))
        except OSError:
            row.update(status="SOURCE_UNAVAILABLE", reason="SOURCE_OR_PROOF_BYTES_UNAVAILABLE")
        except IntegrityError:
            row.update(status="SCIENTIFIC_CONFLICT", reason="SOURCE_OR_PROOF_DIGEST_MISMATCH")
        except KeyError:
            row.update(status="CODE_ERROR", reason="STUDY_BINDING_OR_RECEIPT_REQUIRED_FIELD_MISSING")
        rows.append(row)

    selected = _select_shadows(rows)
    statuses = Counter(row["status"] for row in rows)
    if statuses["SCIENTIFIC_CONFLICT"] or statuses["CODE_ERROR"]:
        status = "FAIL"
    elif not selected or any(statuses.get(state) for state in ("SOURCE_UNAVAILABLE", "EVIDENCE_INSUFFICIENT", "CANDIDATE")):
        status = "BLOCKED"
    else:
        status = "PASS"
    result = {
        "schema": SCHEMA, "scope": "CENSUS_RESEARCH_SHADOW", "source_manifest_sha256": manifest_sha,
        "bindings_sha256": sha256(binding_bytes).hexdigest(), "profile_sha256": settings.sha256,
        "research_cutoff": cutoff, "timezone": settings.timezone.key,
        "status": status,
        "counts_by_state": dict(statuses), "cases": rows, "shadow_selected_observation_ids": selected,
        "native_document_count": len(views), "native_pages_decoded": sum(len(view.pages) for view in views.values()),
        "native_runtime": next(iter(views.values())).runtime if views else None,
        "formal_observations": 0, "formal_census_cells": 0, "formal_mcda_criteria": 0,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result
