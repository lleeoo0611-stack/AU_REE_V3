"""Source-bound technical-processing measurements for a shadow-only pilot."""

from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
from itertools import combinations
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import perf_counter

from .contracts import (
    AcceptedResearchValue, Entity, EvidenceAnchor, NormalizedObservation,
    RawObservation, ResourceScope, Source, ValidatedObservation, canonical_json,
)
from .contracts.temporal import evaluate_temporal
from .documents import DocumentCache, DocumentError, pdf_parser_version
from .migration.reference_oracle import compare_files
from .registry import load_profile, load_registry
from .semantics import ProcessContext, SemanticContext, assess_semantic_use, compare_semantic_use
from .sources import ContentStore, IntegrityError, validate_edition_proofs
from .store import Store


PUBLIC_POLICY = "technical-processing-performance-v2"
BINDINGS_SCHEMA = "technical-evidence-bindings-v1"
ORACLE_SCHEMA = "technical-processing-oracle-v1"
BINDING_METHOD = "reviewed-native-quantity-span-v1"
_COMPARABLE = {"COMPARABLE_DIRECT", "COMPARABLE_DERIVED"}
_USES = {"T1A", "T1B", "T1C", "T1D"}
_SOURCE_NULLS = {"NOT_DISCLOSED", "UNKNOWN", "NOT_APPLICABLE"}
_TECHNICAL_UNITS = {"%", "kg", "kg/t", "kWh/t", "L/t", "AUD/t", "USD/t"}


def _fingerprint(value):
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _write(path, value):
    data = (canonical_json(value) + "\n").encode("utf-8")
    path.write_bytes(data)
    return sha256(data).hexdigest()


def _jsonl(path, rows):
    data = "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")
    path.write_bytes(data)
    return sha256(data).hexdigest()


def _load_inputs(profile_path, manifests, bindings_path):
    profile = load_profile(profile_path)
    projects = {project.project_id: project for project in load_registry(profile.registry_path).projects}
    manifest_bytes = manifests.read_bytes()
    source_manifest = json.loads(manifest_bytes)
    bindings = json.loads(bindings_path.read_bytes())
    expected = bindings.get("source_manifest_sha256")
    if (bindings.get("schema") != BINDINGS_SCHEMA or sha256(manifest_bytes).hexdigest() != expected
            or source_manifest.get("profile_id") != profile.profile_id
            or bindings.get("profile_id") != profile.profile_id
            or source_manifest.get("cutoff") != profile.research_cutoff.isoformat()
            or bindings.get("research_cutoff") != profile.research_cutoff.isoformat()):
        raise ValueError("Technical pilot requires the declared exact acquisition manifest, cohort and cutoff")
    editions = {(entry["edition_id"], entry["receipt_sha256"]): entry for entry in source_manifest["editions"]}
    if len(editions) != len(source_manifest["editions"]):
        raise ValueError("Technical pilot found ambiguous source edition identifiers")
    source_hashes = {entry["sha256"] for entry in editions.values()}
    if len(source_hashes) != bindings["expected_source_count"]:
        raise ValueError("Technical pilot source count disagrees with the pinned study corpus")
    records = bindings["bindings"]
    case_ids = {row["id"] for row in records}
    if not records or len(case_ids) != len(records):
        raise ValueError("A technical study requires unique reviewed observation IDs")
    if any(set(pair) != {"left", "right"} or pair["left"] == pair["right"]
           or not {pair["left"], pair["right"]} <= case_ids for pair in bindings["pairs"]):
        raise ValueError("Technical pair references must identify two different reviewed observations")
    return profile, projects, editions, bindings, source_hashes


def _check_corpus(content, source_hashes, reviewed_sources):
    inventory, bodies = [], {}
    for digest in sorted(source_hashes):
        try:
            payload = content.read(digest)
        except (OSError, IntegrityError) as error:
            inventory.append({"sha256": digest, "status": "SOURCE_UNAVAILABLE", "reason": str(error)})
        else:
            inventory.append({"sha256": digest, "status": "EXACT_SHA", "bytes": len(payload)})
            if digest in reviewed_sources:
                bodies[digest] = payload
    return inventory, bodies


def _locate(view, span):
    page = span["page"]
    if type(page) is not int or not 1 <= page <= len(view.pages):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "TECHNICAL_SPAN_PAGE_UNBOUND")
    quote = " ".join(span["quote"].split())
    if not quote:
        raise ValueError("A technical span cannot be empty")
    body = " ".join(view.pages[page - 1].split())
    if body.count(quote) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "TECHNICAL_SPAN_UNBOUND")
    return {
        "page": page, "quote": quote, "start_in_normalized_page": body.index(quote),
        "length": len(quote),
    }


def _direct_value(binding, main, supporting):
    token = binding["raw_value"]
    raw_unit = binding["raw_unit"]
    state = binding.get("value_state", "PRESENT")
    if state in _SOURCE_NULLS:
        if token is not None or raw_unit is not None:
            raise ValueError("A missing or inapplicable technical value cannot contain a number or unit")
        return None
    if state != "PRESENT":
        raise ValueError("Technical value state must identify a located number or an evidenced source null")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token):
        raise ValueError("Technical pilot expects a reviewed decimal value token")
    value = Decimal(token)
    if raw_unit == "%" and not 0 <= value <= 100:
        raise ValueError("Percentage is outside the reported measurement range")
    if raw_unit not in _TECHNICAL_UNITS:
        raise ValueError("Unreviewed technical unit")
    token_pattern = rf"(?<![\d.]){re.escape(token)}(?![\d.])"
    direct = re.search(rf"{token_pattern}\s*{re.escape(raw_unit)}\b" if raw_unit != "%"
                       else rf"{token_pattern}\s*%", main["quote"])
    if direct:
        return value
    if not binding.get("table") or not binding.get("column") or not re.search(token_pattern, main["quote"]):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "TECHNICAL_MEASUREMENT_UNIT_UNBOUND")
    # Column headers can be on separate lines in positioned PDF output. Keep
    # both the table row and its exact unit text as independent anchors.
    compact = "".join(row["quote"] for row in supporting).lower().replace(" ", "")
    if raw_unit.lower() not in compact and not ("kg" in compact and "/t" in compact and raw_unit == "kg/t"):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "TECHNICAL_TABLE_UNIT_UNBOUND")
    return value


def _mass_balance(binding, value, original_quote):
    spec = binding.get("material_balance")
    if not spec:
        return None
    if value is None or binding["quantity_kind"] != "PROCESS_YIELD" or binding["raw_unit"] != "%":
        raise ValueError("A mineral mass balance requires a mass yield fraction")
    head, product = (Decimal(spec[key]) for key in ("head_grade_pct", "product_grade_pct"))
    if not 0 < head <= 100 or not 0 <= product <= 100:
        raise ValueError("Mass balance grade tokens are outside the physical range")
    if not all(re.search(rf"(?<![\d.]){re.escape(spec[key])}\s*%(?!\d)", original_quote)
               for key in ("head_grade_pct", "product_grade_pct")):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "MASS_BALANCE_GRADES_UNBOUND")
    estimate = value * product / head
    # Use the favourable extreme of all three reported rounding intervals.
    # A discrepancy that remains over 100% is a scientific conflict.
    def half_width(number):
        return Decimal((0, (5,), number.as_tuple().exponent - 1))

    lower = (max(Decimal(0), value - half_width(value))
             * max(Decimal(0), product - half_width(product))
             / (head + half_width(head)))
    return {
        **spec, "formula": "mass_yield_pct * product_grade_pct / feed_grade_pct",
        "implied_recovery_pct": str(estimate), "conservative_lower_pct": str(lower),
        "status": "CONFLICT" if lower > 100 else "CONSISTENT_WITH_PHYSICAL_BOUND",
    }


def _source(edition):
    return Source(
        source_key=edition["source_key"], url=edition["url"], sha256=edition["sha256"], kind=edition["kind"],
        source_version=edition["source_version"], publication_date=edition["publication_date"],
        available_date=edition["available_date"], acquired_at=edition["acquired_at"],
        reacquired_at=edition["reacquired_at"],
    )


def _observation(store, cache, profile, project, edition, binding, payload, proofs, rules_sha):
    source = _source(edition)
    process = ProcessContext(**binding["process"])
    view = cache.pdf(payload, source.sha256)
    location = _locate(view, binding["source_span"])
    unit_locations = [_locate(view, item) for item in binding.get("unit_evidence", ())]
    context_locations = [_locate(view, item) for item in binding["context_spans"]]
    value = _direct_value(binding, location, unit_locations)
    balance = _mass_balance(binding, value, location["quote"])
    temporal = evaluate_temporal(
        source_sha256=source.sha256, cutoff=profile.research_cutoff.isoformat(),
        publication_date=source.publication_date, available_date=source.available_date,
        acquired_at=source.acquired_at, reacquired_at=source.reacquired_at, effective_date=process.test_date,
        reported_publication_date=source.publication_date, observation_role=binding["observation_role"],
        evidence=proofs,
    )
    project_entity = Entity(entity_id=project.project_id, native_namespace="research-project-registry-v1",
                            native_id=project.project_id, name=project.name, kind="PROJECT")
    scope = ResourceScope(scope_id=_fingerprint([project.project_id, binding["scope"]]),
                          name=binding["scope"], project_id=project.project_id)
    anchor_id = _fingerprint([
        BINDING_METHOD, source.source_key, source.sha256, location,
        binding.get("table"), binding.get("row"), binding.get("column"),
    ])
    evidence = EvidenceAnchor(
        anchor_id=anchor_id, source_key=source.source_key, source_sha=source.sha256,
        page=location["page"], locator=f"physical PDF page {location['page']}; reviewed text span",
        table=binding.get("table"), row=binding.get("row"), column=binding.get("column"),
        quote=location["quote"], text_span=location, role="TECHNICAL_QUANTITY_TEXT",
    )
    supporting_anchors = tuple(
        EvidenceAnchor(
            anchor_id=_fingerprint([BINDING_METHOD, source.source_key, source.sha256, role, item]),
            source_key=source.source_key, source_sha=source.sha256, page=item["page"],
            locator=f"physical PDF page {item['page']}; reviewed supporting text span",
            quote=item["quote"], text_span=item, role=role,
        )
        for role, group in (("TECHNICAL_UNIT_TEXT", unit_locations), ("TECHNICAL_PROCESS_CONTEXT", context_locations))
        for item in group
    )
    actual_status = {
        "ELIGIBLE":"VALIDATED", "CANDIDATE":"EVIDENCE_INSUFFICIENT",
        "INELIGIBLE":"AFTER_CUTOFF", "CONFLICT":"SCIENTIFIC_CONFLICT",
    }[temporal.status]
    if balance and balance["status"] == "CONFLICT":
        actual_status = "SCIENTIFIC_CONFLICT"
        reason = "REPORTED_MASS_BALANCE_IMPLIES_OVER_100_PERCENT"
    elif actual_status == "VALIDATED" and value is None:
        actual_status, reason = "EVIDENCE_INSUFFICIENT", binding["value_state"]
    elif actual_status == "VALIDATED":
        reason = "EXACT_TECHNICAL_SOURCE_QUOTE"
    else:
        reason = temporal.reason
    observation = RawObservation(
        observation_id=_fingerprint([
            BINDING_METHOD, source.sha256, project.project_id, location,
            binding["raw_value"], binding["raw_unit"], binding["quantity_kind"],
        ]),
        project_id=project.project_id, scope_id=scope.scope_id, source_key=source.source_key,
        source_sha=source.sha256, anchor_id=evidence.anchor_id,
        variable=binding["quantity_kind"].lower(), quantity_kind=binding["quantity_kind"],
        raw_value=binding["raw_value"], raw_unit=binding["raw_unit"], status="REPORTED",
        transform_version=BINDING_METHOD, observation_role=binding["observation_role"],
        effective_date=process.test_date, reported_publication_date=source.publication_date,
        material_basis=binding["material"], chemical_basis=binding["source_analyte_label"],
        denominator_basis=binding["denominator_basis"], category="TECHNICAL_PROCESSING",
        null_reason=binding["value_state"] if value is None else None,
        original_payload={
            "phase": "SHADOW", "source_span": location, "context_spans": context_locations,
            "unit_spans": unit_locations, "process": asdict(process), "value_statistic": binding["value_statistic"],
            "quantity_kind": binding["quantity_kind"], "value_state": binding["value_state"],
            "chemical_species": binding["chemical_species"], "material_balance": balance,
        },
    )
    normalized_value = str(value) if value is not None else None
    candidate = NormalizedObservation(
        candidate_id=_fingerprint([observation.raw_record_id, normalized_value, observation.raw_unit]),
        observation_id=observation.observation_id, raw_record_id=observation.raw_record_id,
        value=normalized_value, unit=observation.raw_unit, material_basis=observation.material_basis,
        chemical_basis=observation.chemical_basis, denominator_basis=observation.denominator_basis,
        transform_version="technical-reported-number-v1",
    )
    validation = ValidatedObservation(
        validation_id=_fingerprint([candidate.candidate_id, temporal.decision_id, actual_status, rules_sha]),
        candidate_id=candidate.candidate_id, cutoff=temporal.cutoff, rules_sha=rules_sha,
        outcome=actual_status, reason=reason,
        evidence_anchor_ids=(evidence.anchor_id,), comparable=False,
        temporal_decision_id=temporal.decision_id, temporal_evidence=proofs,
        reacquired_at=source.reacquired_at,
    )
    context = SemanticContext(
        quantity_kind=binding["quantity_kind"], project_id=project.project_id, scope_id=scope.scope_id,
        material=observation.material_basis, denominator_basis=observation.denominator_basis,
        chemical_species=binding["chemical_species"], treo_members=binding.get("treo_members", ()),
        temporal_status=temporal.status,
        temporal_decision_id=temporal.decision_id, research_cutoff=temporal.cutoff,
        observation_role=observation.observation_role, unit=binding["raw_unit"],
        effective_date=observation.effective_date, process=process,
        value_state="CONFLICT" if actual_status == "SCIENTIFIC_CONFLICT" else binding["value_state"],
        comparison_group=binding["value_statistic"], cost_currency=binding.get("cost_currency"),
        cost_year=binding.get("cost_year"), cost_price_basis=binding.get("cost_price_basis"),
    )
    semantics = assess_semantic_use(context, purpose=binding["purpose"], observation_ids=(observation.observation_id,),
                                   source_sha256=source.sha256, evidence_anchor_ids=(evidence.anchor_id,
                                   *(item.anchor_id for item in supporting_anchors)))
    with store.transaction():
        if store.source_verified(source.source_key, source.sha256):
            store.add(source)
        else:
            store.add_source(source, payload)
        for record in (project_entity, scope, evidence, *supporting_anchors, observation, candidate, validation, semantics):
            store.add(record)
    return {
        "case_id": binding["id"], "admission": "SHADOW", "observation": observation.to_dict(),
        "anchor": evidence.to_dict(), "candidate": candidate.to_dict(), "validation": validation.to_dict(),
        "supporting_anchors": [item.to_dict() for item in supporting_anchors],
        "temporal_decision": temporal.to_dict(), "assessment": semantics.to_dict(),
        "exact_source_quote": True, "source_raw": balance,
    }, semantics


def _pairs(results, annotated):
    explicit = {tuple(sorted((entry["left"], entry["right"]))) for entry in annotated}
    selected = set(explicit)
    for left, right in combinations(sorted(results), 2):
        if results[left].purpose == results[right].purpose:
            selected.add((left, right))
    return [
        {"left": left, "right": right, **compare_semantic_use(results[left], results[right]).to_dict()}
        for left, right in sorted(selected) if left in results and right in results
    ]


def _oracle(out, expected_path, observations, pairs):
    if expected_path is None:
        return {"status":"NOT_RUN", "holdout":"UNMEASURED"}
    expected = json.loads(expected_path.read_bytes())
    if expected.get("schema") != ORACLE_SCHEMA:
        raise ValueError("Unexpected technical oracle contract")
    actual = []
    for row in observations:
        raw, anchor, info = row["observation"], row["anchor"], row["assessment"]
        process = info["normalized_context"]["process"]
        actual.append({
            "case_id": row["case_id"],
            "v3_reference": {"observation_id": raw["observation_id"], "anchor_id": anchor["anchor_id"]},
            "values": {
                "project_id": raw["project_id"], "source_sha256": raw["source_sha"], "page": anchor["page"],
                "raw_value": raw["raw_value"], "raw_unit": raw["raw_unit"], "purpose": info["purpose"],
                "quantity_kind": raw["quantity_kind"], "observation_role": raw["observation_role"],
                "source_scope": process["experiment_id"], "process_step": process["step"],
                "process_end": process["end"], "process_scale": process["scale"],
            },
        })
    _jsonl(out/"reviewed_technical_expected.jsonl", expected["observations"])
    _jsonl(out/"reviewed_technical_actual.jsonl", actual)
    measured = compare_files(out/"reviewed_technical_expected.jsonl", out/"reviewed_technical_actual.jsonl", out)
    observed = {tuple(sorted((row["left"], row["right"]))):row["status"] for row in pairs}
    cross = Counter()
    comparisons = []
    for item in expected["pairs"]:
        actual = observed.get(tuple(sorted((item["left"], item["right"]))))
        comparisons.append({**item, "actual": actual, "status":"PASS" if actual == item["expected"] else
                                              "NOT_RUN" if actual is None else "FAIL"})
        if actual is not None:
            wanted, matched = item["expected"] in _COMPARABLE, actual in _COMPARABLE
            cross["TP" if wanted and matched else "FN" if wanted else "FP" if matched else "TN"] += 1
    return {
        "status":"PASS" if measured["status"] == "PASS" and all(row["status"] == "PASS" for row in comparisons) else "FAIL",
        "observation_regression": measured["counts"], "pair_regression": comparisons,
        "reviewed_pair_confusion": {field: cross[field] for field in ("TP","FP","TN","FN")},
        "holdout": expected["holdout"], "holdout_precision":"UNMEASURED", "holdout_recall":"UNMEASURED",
    }


def run_technical_pilot(
    profile: Path, sources: Path, assets: Path, bindings: Path, out: Path, *, oracle: Path | None = None,
) -> dict:
    """Verify anchored technical facts and assessed uses without accepting a value."""
    start = perf_counter()
    study, projects, editions, plan, source_hashes = _load_inputs(profile, sources, bindings)
    content = ContentStore(assets)
    if out.resolve().is_relative_to(assets.resolve()):
        raise ValueError("Place technical work outside immutable source storage")
    out.mkdir(parents=True, exist_ok=True)
    records = sorted(plan["bindings"], key=lambda row: row["id"])
    inventory, body = _check_corpus(content, source_hashes, {row["source_sha256"] for row in records})
    cache = DocumentCache()
    observed, semantics, rejected = [], {}, []
    rules_sha = sha256(bindings.read_bytes()).hexdigest()
    proof_cache = {}
    readback = _write(out/"source_readback.json", inventory)
    seen = set()
    with TemporaryDirectory(dir=out) as workspace:
        database = Path(workspace) / "technical_shadow.sqlite"
        with Store(database) as shadow:
            for row in records:
                digest, project_id = row["source_sha256"], row["project_id"]
                edition_key = (row["edition_id"], row["receipt_sha256"])
                binding_identity = _fingerprint([
                    digest, project_id, row["source_span"],
                    row["raw_value"], row["raw_unit"], row["quantity_kind"],
                ])
                if binding_identity in seen:
                    rejected.append({"case_id": row["id"], "status": "DUPLICATE_MEASUREMENT"})
                    continue
                try:
                    edition = editions[edition_key]
                    if (edition["sha256"] != digest or edition["kind"] != "pdf"
                        or project_id not in edition["project_ids"] or row["purpose"] not in _USES):
                        raise IntegrityError("TECHNICAL_BINDING_SOURCE_OR_PROJECT_UNBOUND")
                    if digest not in body:
                        raise FileNotFoundError("SOURCE_OBJECT_UNAVAILABLE")
                    if edition_key not in proof_cache:
                        proof_cache[edition_key] = validate_edition_proofs(content, edition, study.timezone)
                    record, assessment = _observation(
                        shadow, cache, study, projects[project_id], edition, row, body[digest],
                        proof_cache[edition_key], rules_sha,
                    )
                except FileNotFoundError as error:
                    rejected.append({"case_id": row["id"], "status":"SOURCE_UNAVAILABLE", "reason":str(error)})
                except DocumentError as error:
                    missing = "NOT_LOCATED" if str(error) in {"TECHNICAL_SPAN_PAGE_UNBOUND", "TECHNICAL_SPAN_UNBOUND"} else None
                    rejected.append({"case_id": row["id"], "status":error.outcome, "reason":str(error),
                                     "value_state":missing})
                except (KeyError, OSError, ValueError, ArithmeticError) as error:
                    rejected.append({"case_id":row["id"], "status":"BLOCKED", "reason":str(error)})
                else:
                    observed.append(record)
                    semantics[row["id"]] = assessment
                    seen.add(binding_identity)
            if shadow.records(AcceptedResearchValue):
                raise RuntimeError("Technical shadow may not contain accepted research values")
            integrity = shadow.integrity()
        os.replace(database, out / database.name)
    pairs = _pairs(semantics, plan["pairs"])
    assessment_sha = _jsonl(out/"technical_observations.jsonl", observed)
    comparison_sha = _write(out/"technical_pairs.json", pairs)
    oracle_report = _oracle(out, oracle, observed, pairs)
    _write(out/"technical_oracle.json", oracle_report)
    paired = [row for row in pairs if row["status"] in _COMPARABLE]
    projects_by_case = {row["id"]: row["project_id"] for row in records}
    cross_project_pairs = [row for row in paired if projects_by_case[row["left"]] != projects_by_case[row["right"]]]
    assessed_keys = {(projects_by_case[case], result.purpose) for case, result in semantics.items()}
    usable_keys = {(projects_by_case[case], semantics[case].purpose) for row in cross_project_pairs
                   for case in (row["left"], row["right"])}
    status = "PASS"
    if oracle_report["status"] == "FAIL" and not rejected:
        status = "FAIL"
    elif rejected or any(row["status"] != "EXACT_SHA" for row in inventory):
        status = "BLOCKED"
    receipt = {
        "schema":"technical-processing-pilot-v1", "profile_id":study.profile_id,
        "research_cutoff":study.research_cutoff.isoformat(), "timezone":study.timezone.key,
        "source_manifest_sha256":plan["source_manifest_sha256"], "bindings_sha256":rules_sha,
        "policy":PUBLIC_POLICY, "extraction_method":BINDING_METHOD, "pdf_parser":pdf_parser_version(),
        "status":status, "observation_validation":dict(Counter(row["validation"]["outcome"] for row in observed)),
        "assessments":dict(Counter(row.status for row in semantics.values())),
        "subcriteria":dict(Counter(row.purpose for row in semantics.values())),
        "source_objects_expected":len(source_hashes),
        "source_objects_read_back":sum(row["status"] == "EXACT_SHA" for row in inventory),
        "candidate_observations":len(records), "source_anchored_observations":len(observed),
        "source_families_reviewed":len({r["sample_family"] for r in records if r["id"] in semantics}),
        "candidate_pairs_assessed":len(pairs), "pairwise_compatible":len(paired),
        "cross_project_compatible_pairs":len(cross_project_pairs), "project_subcriteria_assessed":len(assessed_keys),
        "project_subcriteria_cross_project_usable":len(usable_keys),
        "pair_assessments":dict(Counter(row["status"] for row in pairs)),
        "accepted_count":0, "formal_t1_v2":"NOT_RUN", "rankings_status":"INSUFFICIENT_COHORT" if not cross_project_pairs else
        "NO_APPROVED_TECHNICAL_UTILITY_OR_WEIGHTS",
        "ranked_panels":0, "missing_or_rejected":rejected, "sqlite":integrity,
        "observation_artifact_sha256":assessment_sha, "pair_artifact_sha256":comparison_sha,
        "source_inventory_sha256":readback, "oracle_status":oracle_report["status"],
        "holdout":"UNMEASURED", "O3":status if oracle_report["status"] != "NOT_RUN" else "NOT_RUN",
        "elapsed_seconds":perf_counter()-start,
    }
    _write(out/"technical_pilot_receipt.json", receipt)
    return receipt
