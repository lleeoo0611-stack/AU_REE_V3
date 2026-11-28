"""Verify native study production scenarios as isolated Census shadow observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .contracts import RawObservation, canonical_json
from .contracts.temporal import evaluate_temporal
from .development import bind_native_quote, reported_calendar_date
from .documents import DocumentCache, DocumentError
from .normalization import reported_interval
from .observations import NUMBER
from .registry import load_profile, load_registry
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_SCHEMA, validate_edition_proofs


if TYPE_CHECKING:
    from .census_display import DisplayFact


SCHEMA = "source-bound-production-forecasts-v1"
_CONTEXT_ORACLE_SHA256 = "3db34d7949618bf2a27fde25fbd5d17d17fb2952c82fc7a3d27dc519c1bde3c9"
_CONTEXT_ORACLE_V2_SHA256 = "d2af32c8cc08509e7d8eeb58306c8427c65b38b611eb0a87c34b58715644a871"
_ANNUAL_NDPR_TARGET = rf"\bAverage {NUMBER} tpa NdPr production during years [0-9]+-[0-9]+"


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
    "POST_RAMP_UP_AVERAGE_NDPR_OXIDE": StudyScenario(
        "FORECAST", "annual_ndpr_oxide", "NDPR_OXIDE", "tonnes per annum", "NDPR_OXIDE", "NDPR_OXIDE",
        re.compile(rf"\bAverage annual production (?P<token>{NUMBER}) tonnes per annum of NdPr oxide\b", re.I),
    ),
    "TARGET_WINDOW_AVERAGE_NDPR_OXIDE": StudyScenario(
        "FORECAST", "annual_ndpr_oxide", "NDPR_OXIDE", "tpa", "NDPR_OXIDE", "NDPR_OXIDE",
        re.compile(rf"\bAverage (?P<token>{NUMBER}) tpa NdPr production during years [0-9]+-[0-9]+\b", re.I),
    ),
    "LIFE_OF_MINE_TARGET_AVERAGE_NDPR_OXIDE": StudyScenario(
        "FORECAST", "annual_ndpr_oxide", "NDPR_OXIDE", "tpa", "NDPR_OXIDE", "NDPR_OXIDE",
        re.compile(rf"{_ANNUAL_NDPR_TARGET} \((?P<token>{NUMBER}) tpa over LOM\)", re.I),
    ),
}
_LIFETIME = re.compile(r"\blife-of-mine MREC production tonnes (?:" + NUMBER + r")\b", re.I)


def _fingerprint(value) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _quote_anchors(document, source_key, binding, project_name):
    native = binding["statement"]
    statement = bind_native_quote(document, source_key, native["page"], native["quote"], "STUDY_PRODUCTION_CLAUSE")
    scope = bind_native_quote(document, source_key, native["page"], native["scope_quote"], "STUDY_PRODUCTION_SCOPE")
    gap = max(statement.text_span["start"], scope.text_span["start"]) - min(
        statement.text_span["end"], scope.text_span["end"],
    )
    if gap > 160:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "STUDY_SCOPE_NOT_LOCAL_TO_PRODUCTION_CLAUSE")
    release = binding["publication"]
    publication = bind_native_quote(
        document, source_key, release["page"], release["quote"], "STUDY_PRODUCTION_PUBLICATION",
    )
    if release["date_token"] not in publication.quote:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "STUDY_RELEASE_DATE_NOT_BOUND_TO_QUOTE")
    identity = binding["project_identity"]
    project = bind_native_quote(document, source_key, identity["page"], identity["quote"], "STUDY_PROJECT_IDENTITY")
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
    rate_anchor = bind_native_quote(document, key, statement.page, match[0], "STUDY_ANNUAL_RATE_VALUE")
    token = match["token"]
    qualifier = match.groupdict().get("qualifier")
    point, low, high = reported_interval(token)
    if point <= 0:
        raise DocumentError("SCIENTIFIC_CONFLICT", "ANNUAL_PRODUCTION_RATE_MUST_BE_POSITIVE")
    publication_date = reported_calendar_date(binding["publication"]["date_token"])
    decision = evaluate_temporal(
        source_sha256=digest, cutoff=cutoff, observation_role=scenario.role, publication_date=publication_date,
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proofs,
    )
    context = {
        "scenario": binding["scenario"], "study": binding["study"],
        "product_basis": scenario.product, "chemical_basis": scenario.chemistry,
        "study_publication_date": publication_date, "role": scenario.role, "annual_rate_basis": scenario.unit,
        "source_edition_id": edition["edition_id"], "precision_qualifier": qualifier.strip() if qualifier else None,
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


def _production_forecast_review(profile: Path, sources: Path, assets: Path, bindings: Path) -> dict:
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
    return result


def review_production_forecasts(profile: Path, sources: Path, assets: Path, bindings: Path, out: Path) -> dict:
    """Read exact PDF editions without writing a formal release or selection store."""
    if out.resolve().is_relative_to(assets.resolve()) or out.resolve() in {
        path.resolve() for path in (profile, sources, bindings)
    }:
        raise ValueError("Forecast receipt requires an output path separate from locked inputs")
    result = _production_forecast_review(profile, sources, assets, bindings)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


def _file_sha256(path):
    return sha256(path.read_bytes()).hexdigest()


def _literal(view, span):
    page, (first, last) = span["page"], span["lines"]
    actual = " ".join(" ".join(view.locate(page, line=line).text for line in range(first, last + 1)).split())
    if actual.count(span["contains"]) != 1:
        raise ValueError(f"Independent forecast excerpt differs at page {page}, lines {first}–{last}")
    return {"physical_page": page, "first_line": first, "last_line": last, "excerpt": span["contains"],
            "layout_sha256": sha256(actual.encode()).hexdigest()}


def verify_forecast_context(production, frozen_sources, assets, oracle_path):
    """Corroborate shadow values with independently pinned source rows and notes."""
    manifest = json.loads(frozen_sources.read_bytes())
    oracle = json.loads(oracle_path.read_bytes())
    scope = {
        "native-production-forecast-context-oracle-v1": "FIXED_144",
        "native-production-forecast-context-oracle-v2": "FIXED_NATIVE_EDITION",
    }.get(oracle["schema"])
    if scope is None:
        raise ValueError("Unknown forecast context oracle")
    if production["source_manifest_sha256"] != _file_sha256(frozen_sources):
        raise ValueError("Forecast source lineage differs from the independent oracle input")
    cases = {row["case_id"]: row for row in production["cases"]}
    content, cache, views, confirmed = ContentStore(assets), DocumentCache(), {}, []
    for expected in oracle["cases"]:
        digest = expected["source_sha256"]
        if digest not in views:
            required = max(span["page"] for item in oracle["cases"] if item["source_sha256"] == digest
                           for span in item["spans"])
            views[digest] = cache.pdf(content.read(digest), digest, required)
        row = cases[expected["case_id"]]
        if scope == "FIXED_NATIVE_EDITION" and any(row[key] != expected[key] for key in (
            "project_id", "study", "scenario", "receipt_sha256",
        )):
            raise ValueError(f"Original forecast project, study, scenario or edition disagrees: {expected['case_id']}")
        raw = row.get("raw_observation")
        if row["status"] != "VERIFIED_SHADOW" or raw is None:
            raise ValueError(f"Expected source forecast did not qualify: {expected['case_id']}")
        pins = ("role", "amount", "native_unit", "material", "chemistry", "date", "source_sha256")
        columns = ("observation_role", "raw_value", "raw_unit", "material_basis", "chemical_basis",
                   "reported_publication_date", "source_sha")
        source, = (item for item in manifest["editions"] if item["receipt_sha256"] == row["receipt_sha256"]
                   and item["sha256"] == digest and row["project_id"] in item["project_ids"])
        proofs = validate_edition_proofs(content, source, ZoneInfo(manifest["timezone"]))
        temporal = evaluate_temporal(
            source_sha256=digest, observation_role=expected["role"], cutoff=manifest["cutoff"],
            publication_date=expected["date"], acquired_at=source["acquired_at"], reacquired_at=source["reacquired_at"],
            evidence=proofs,
        )
        if not (tuple(raw[key] for key in columns) == tuple(expected[key] for key in pins)
                and proofs and temporal.status == "ELIGIBLE"
                and canonical_json(temporal.to_dict()) == canonical_json(row["temporal_decision"])
                and not row["formal_selection"] and not row["formal_mcda"]
                and raw["status"] == "SHADOW" and raw["denominator_basis"] == "YEAR"
                and raw["effective_date"] is None and raw["event_date"] is None):
            raise ValueError(f"Original forecast quantity, role, basis or date disagrees: {expected['case_id']}")
        confirmed.append({key: expected[key] for key in ("case_id", *pins, "period")} | {
            "project_id": row["project_id"], "source_url": source["url"], "receipt_sha256": source["receipt_sha256"],
            "state": "SHADOW", "observation_id": raw["observation_id"],
            "source_spans": [_literal(views[digest], span) for span in expected["spans"]],
        } | {key: expected[key] for key in ("period_display",) if key in expected})
    negative = oracle["negative"]
    row = cases[negative["case_id"]]
    if row["status"] != "REJECTED" or row["reason"] != "LIFETIME_PRODUCT_MASS_IS_NOT_ANNUAL_CONTAINED_TREO":
        raise ValueError("Life-of-mine product mass is not an annual contained TREO observation")
    return {
        "schema": oracle["schema"], "scope": scope, "status": "PASS",
        "source_manifest_sha256": _file_sha256(frozen_sources), "oracle_sha256": _file_sha256(oracle_path),
        "cases": confirmed, "negative": {"reason": row["reason"], "source_sha256": negative["source_sha256"],
                                       "source_span": _literal(views[negative["source_sha256"]], negative)},
        "native_pdf_opens": len(views), "native_pages_decoded": sum(len(view.pages) for view in views.values()),
        "new_observations": 0, "formal_census": 0, "formal_mcda": 0,
    }


_DISPLAY_PRODUCT = {
    "BASE_CASE_LIFE_OF_MINE_AVERAGE": ("contained TREO in MREC", "Contained TREO in MREC"),
    "NOMINAL_PROCESSING_CAPACITY": ("MREC containing", "Contained TREO in MREC"),
    "BASE_CASE_WET_FLOTATION_CONCENTRATE": (
        "heavy rare earth enriched flotation concentrate", "Heavy rare earth enriched flotation concentrate (wet)",
    ),
    "POST_RAMP_UP_AVERAGE_NDPR_OXIDE": ("NdPr oxide", "NdPr oxide"),
    "TARGET_WINDOW_AVERAGE_NDPR_OXIDE": ("NdPr oxide", "NdPr oxide"),
    "LIFE_OF_MINE_TARGET_AVERAGE_NDPR_OXIDE": ("NdPr oxide", "NdPr oxide"),
}
_DISPLAY_PERIOD = {
    "BASE_CASE_LIFE_OF_MINE_AVERAGE": (
        "life-of-mine annual average", "Base case life-of-mine annual average, including ramp-up and scheduling",
    ),
    "NOMINAL_PROCESSING_CAPACITY": (
        "nominal 8.0 Mtpa processing rate", "Nominal design rate at 8.0 Mtpa processing capacity",
    ),
    "BASE_CASE_WET_FLOTATION_CONCENTRATE": ("selected base case", "PFS base case, averaging window unspecified"),
}


def _display_quote(anchor, spans, token):
    if token in anchor["quote"]:
        return anchor["quote"], anchor["locator"]
    matching = [span for span in spans if token in span["excerpt"]]
    if len(matching) != 1:
        raise ValueError(f"Forecast display token lacks a unique native witness: {token}")
    span, = matching
    return (span["excerpt"], f"PDF page {span['physical_page']}, native layout lines "
            f"{span['first_line']}–{span['last_line']}")


def forecast_display_facts(profile: Path, sources: Path, assets: Path, bindings: Path, *,
                           oracle_path: Path) -> tuple[DisplayFact, ...]:
    """Project independently corroborated study context into display-only fields."""
    from .census_display import DisplayFact

    if _file_sha256(oracle_path) not in {_CONTEXT_ORACLE_SHA256, _CONTEXT_ORACLE_V2_SHA256}:
        raise ValueError("Forecast display requires the registered independent native context oracle")
    production = _production_forecast_review(profile, sources, assets, bindings)
    verified = verify_forecast_context(production, sources, assets, oracle_path)
    by_case = {row["case_id"]: row for row in production["cases"]}
    facts = []
    for case in verified["cases"]:
        row = by_case[case["case_id"]]
        if case["observation_id"] not in production["shadow_selected_observation_ids"]:
            raise ValueError("Forecast document proof is ambiguous within its source scope")
        anchor = row["evidence"][0]
        date = case["date"]
        scenario = row["scenario"]
        qualifier = row["scenario_context"]["precision_qualifier"]
        context = {
            "as_reported_at": date, "source_kind": "pdf", "temporal_status": "ELIGIBLE",
            "temporal_decision_id": row["temporal_decision"]["decision_id"],
            "scenario": scenario, "period": case["period"], "material_basis": case["material"],
            "chemical_basis": case["chemistry"] or "UNKNOWN_CONTEXT", "study": row["study"],
            "raw_observation_id": case["observation_id"], "precision_qualifier": qualifier or "UNREPORTED",
            "forecast_uncertainty": "UNREPORTED",
            "source_scope": verified["scope"] if "period_display" in case else "FIXED_144",
        }
        common = {
            "project_id": case["project_id"], "source_sha256": case["source_sha256"],
            "source_capture_sha256": case["receipt_sha256"], "source_url": case["source_url"],
            "publication_date": date, "scope": row["study"], "subject_id": row["raw_observation"]["scope_id"],
            "data_role": case["role"], "context": context,
        }
        product_token, product_value = _DISPLAY_PRODUCT[scenario]
        period_token, period_value = case["period_display"] if "period_display" in case else _DISPLAY_PERIOD[scenario]
        product_quote, product_locator = _display_quote(anchor, case["source_spans"], product_token)
        period_quote, period_locator = _display_quote(anchor, case["source_spans"], period_token)
        rate_value = " ".join(part for part in (qualifier, case["amount"], case["native_unit"]) if part)
        facts.extend((
            DisplayFact(field_id="forecast_product_and_basis", value=product_value, raw_token=product_token,
                        raw_unit="", locator=product_locator, quote=product_quote, **common),
            DisplayFact(field_id="forecast_scenario_or_period", value=period_value, raw_token=period_token,
                        raw_unit="", locator=period_locator, quote=period_quote, **common),
            DisplayFact(field_id="forecast_annual_rate_as_reported", value=rate_value, raw_token=case["amount"],
                        raw_unit=case["native_unit"], locator=anchor["locator"], quote=anchor["quote"], **common),
        ))
    return tuple(facts)
