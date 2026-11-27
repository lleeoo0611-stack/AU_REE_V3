"""Replay the fixed resource cohort through distinct semantic experiment stages."""

from collections import Counter, defaultdict
from dataclasses import replace
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
from shutil import copyfile
import unicodedata

from . import comparability
from .contracts import EvidenceAnchor, RawObservation, ResearchRelease, canonical_json
from .contracts.temporal import evaluate_temporal
from .documents import DocumentCache
from .fresh_decision import load_policy
from .fresh_research import verify_source_manifest
from .normalization import SCHEMA_VERSION as NORMALIZATION_SCHEMA, normalize_measurement
from .observations import OXIDE_VARIABLES
from .registry import load_profile, load_registry
from .research_delta import ResearchBuild
from .research_inputs import checked_research_inputs
from .semantics import POLICY_VERSION, SemanticContext, assess_semantic_use, compare_semantic_use, vocabulary
from .sources import ContentStore, validate_edition_proofs
from .store import Store

SCHEMA = "ontology-resource-experiment-v1"
_UNITS = {"ore_mt": "Mt", "grade": "%", "contained_treo_kt": "kt TREO"}
_REVIEW_UNITS = {
    "resource_ore_mass": ("ORE_MASS", "Mt"), "resource_treo_grade": ("GRADE", "%"),
    "contained_treo": ("CONTAINED_OXIDE_MASS", "kt TREO"),
}


def _object(path):
    return json.loads(path.read_bytes())


def _fingerprint(value):
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _space_normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def _read_originals(store, raw_records, content, verified):
    """Locate original evidence without making inference or changing candidate tokens."""
    cache, by_source, verdicts = DocumentCache(), defaultdict(list), {}
    for raw in raw_records:
        anchor = store.get(EvidenceAnchor, raw.anchor_id)
        if anchor is None or anchor.source_sha != raw.source_sha:
            raise ValueError("Observation lacks its checked original anchor")
        context = _statement_quotes(raw)
        by_source[raw.source_sha].append((raw, anchor, context))
    for source_sha, linked in by_source.items():
        if source_sha not in verified:
            for raw, anchor, _ in linked:
                verdicts[raw.observation_id] = _evidence_result(raw, anchor, "SOURCE_UNAVAILABLE")
            continue
        last_page = max(page for _, anchor, extra in linked
                        for page in [anchor.page, *(quote["physical_page"] for quote in extra)])
        try:
            document = cache.pdf(content.read(source_sha), source_sha, last_page)
        except (OSError, ValueError):
            for raw, anchor, _ in linked:
                verdicts[raw.observation_id] = _evidence_result(raw, anchor, "SOURCE_UNAVAILABLE")
            continue
        pages = tuple(_space_normalized(page) for page in document.pages)
        for raw, anchor, extra in linked:
            checks = [{"physical_page": anchor.page, "quote": anchor.quote}, *extra]
            for check in checks:
                needle = _space_normalized(check["quote"])
                check["located"] = bool(needle) and needle in pages[check["physical_page"] - 1]
            status = "ORIGINAL_SPANS_LOCATED" if all(check["located"] for check in checks) else "CONTEXT_SPAN_NOT_LOCATED"
            verdicts[raw.observation_id] = {**_evidence_result(raw, anchor, status), "checks": checks}
    return verdicts


def _statement_quotes(raw):
    payload = json.loads(raw.original_payload)
    return [{"physical_page": item["physical_page"], "quote": item["quote"]}
            for item in payload.get("statement_context", ())]


def _evidence_result(raw, anchor, status):
    return {"observation_id": raw.observation_id, "source_sha256": raw.source_sha,
            "anchor_id": anchor.anchor_id, "status": status}


def _normalization(selected, reread):
    conversions, groups = {}, defaultdict(dict)
    for item in selected:
        raw = item.raw
        if reread[raw.observation_id]["status"] != "ORIGINAL_SPANS_LOCATED":
            continue
        normalized = comparability.normalize_input(item, _UNITS.get(raw.variable, "ppm"))
        conversions[raw.observation_id] = normalized.to_dict()
        groups[raw.project_id][raw.variable] = item
    derived = {}
    for project, group in sorted(groups.items()):
        if {"ore_mt", "grade"} <= group.keys():
            derived[project] = comparability.shadow_contained_mass(
                group["ore_mt"], group["grade"], reported=group.get("contained_treo_kt"),
            ).to_dict()
    return conversions, derived


def _use_assessments(store, comparisons, reread):
    assessments, evidence_uses, pairs = defaultdict(list), defaultdict(list), []
    for project, criteria in sorted(comparisons.items()):
        for purpose in ("R1", "S1"):
            comparison = criteria[purpose]
            if not comparison.comparable or any(
                reread[item.raw.observation_id]["status"] != "ORIGINAL_SPANS_LOCATED"
                for item in comparison.inputs
            ):
                continue
            by_variable = {item.raw.variable: item for item in comparison.inputs}
            base = by_variable["contained_treo_kt" if purpose == "R1" else "grade"]
            context = comparability.resource_semantic_context(base)
            transforms = ()
            if purpose == "S1":
                context = replace(context, quantity_kind="OXIDE_COMPOSITION", chemical_species=tuple(OXIDE_VARIABLES),
                                  treo_members=tuple(comparison.context["treo_denominator"]["chemical_species"]),
                                  denominator_basis="TREO_WITHIN_WHOLE_ORE")
                transforms = ({"kind": "DERIVATION", "formula_version": comparison.derivation["method"],
                               **comparison.derivation},)
            assessment = assess_semantic_use(
                context, purpose=purpose, source_sha256=base.source.sha256,
                observation_ids=tuple(item.raw.observation_id for item in comparison.inputs),
                evidence_anchor_ids=tuple(item.anchor.anchor_id for item in comparison.inputs),
                transforms=transforms,
            )
            store.add(assessment)
            assessments[purpose].append(assessment)
            for identity in assessment.observation_ids:
                evidence_uses[identity].append({"purpose": purpose, "assessment_id": assessment.assessment_id})
    for purpose, values in sorted(assessments.items()):
        for left, right in combinations(values, 2):
            pairs.append({"purpose": purpose, "projects": [left.normalized_context.project_id,
                                                            right.normalized_context.project_id],
                          **compare_semantic_use(left, right).to_dict()})
    return ([item.to_dict() for purpose in sorted(assessments) for item in assessments[purpose]], evidence_uses, pairs)


def _review_candidates(binding_path, source_manifest, source_sha, settings, content):
    """Reread predeclared design spans against the same locked source corpus."""
    bindings = _object(binding_path)
    if (bindings["schema"] != "semantic-resource-original-reread-design-v1"
            or bindings["source_manifest_sha256"] != source_sha):
        raise ValueError("Original reread bindings belong to another source corpus or schema")
    verified = []
    cache = DocumentCache()
    for case in bindings["cases"]:
        row = _review_case(case, source_manifest, settings, content, cache)
        verified.append(row)
    return verified


def _review_case(case, manifest, settings, content, cache):
    source_sha, project = case["source_sha256"], case["project_id"]
    native = case["baseline_measurements"]
    raw = native["raw_values"]
    row = {"case_id": case["case_id"], "partition": case["partition"], "project_id": project,
           "source_sha256": source_sha, "A": {"status": "NO_GOVERNED_SELECTION"},
           "C": {}, "D": None}
    sources = [item for item in manifest["editions"] if item["sha256"] == source_sha and project in item["project_ids"]]
    if not sources:
        return {**row, "B": {"status": "SOURCE_UNAVAILABLE"}}
    edition = min(sources, key=lambda item: item["receipt_sha256"])
    spans = [{"field": "native_row", "physical_page": native["physical_page"], "quote": native["row_quote"]},
             *case["original_anchors"]]
    try:
        proofs = validate_edition_proofs(content, edition, settings.timezone)
        doc = cache.pdf(content.read(source_sha), source_sha, max(item["physical_page"] for item in spans))
    except (ValueError, OSError):
        return {**row, "B": {"status": "SOURCE_UNAVAILABLE"}}
    for span in spans:
        text = doc.locate(span["physical_page"]).text
        if _space_normalized(span["quote"]) not in _space_normalized(text):
            return {**row, "B": {"status": "CONTEXT_SPAN_NOT_LOCATED", "missing": span["field"]}}
    if any(item["text"] not in native["row_quote"].split() for item in raw.values()):
        raise ValueError("An original token is outside the reviewed source line")
    declared = case["source_context"]
    temporal = evaluate_temporal(
        source_sha256=source_sha, cutoff=settings.research_cutoff.isoformat(), observation_role="HISTORICAL_MEASUREMENT",
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"],
        effective_date=declared["effective_date"], evidence=proofs,
    )
    anchor_ids = tuple(_fingerprint([source_sha, x["physical_page"], x["quote"]]) for x in spans)
    row["B"] = {"status": "ORIGINAL_SPANS_LOCATED", "spans": [dict(span, anchor_id=fingerprint)
                                                       for span, fingerprint in zip(spans, anchor_ids, strict=True)],
                "original_values": raw, "source_context": declared, "temporal": temporal.to_dict()}
    context = SemanticContext(
        quantity_kind="CONTAINED_OXIDE_MASS", project_id=project, scope_id=_fingerprint([project, declared["scope"]]),
        statement_id=_fingerprint([source_sha, declared["scope"], declared["effective_date"]]),
        resource_class="MINERAL_RESOURCE", jorc_categories=tuple(declared["jorc_categories"]),
        effective_date=declared["effective_date"], cutoff_grade=declared["cutoff"]["grade"],
        cutoff_unit=declared["cutoff"]["unit"], cutoff_analyte=declared["cutoff"]["species"],
        treo_members=tuple(declared["treo_members"]), chemical_species=tuple(declared["treo_members"]),
        material=declared.get("material"), denominator_basis="ABSOLUTE_RESOURCE_MASS",
        temporal_status=temporal.status, temporal_decision_id=temporal.decision_id,
        research_cutoff=settings.research_cutoff.isoformat(),
    )
    identified, normalized = {}, {}
    for name, reported in sorted(raw.items()):
        kind, unit = _REVIEW_UNITS[name]
        observation = _fingerprint([source_sha, native["physical_page"], name, reported])
        identified[name] = observation
        basis = declared.get("grade_denominator") if kind == "GRADE" else context.denominator_basis
        normalized[name] = normalize_measurement(
            reported["text"], reported["unit"], unit,
            source_context=replace(context, quantity_kind=kind, denominator_basis=basis),
            observation_id=observation, source_sha256=source_sha, evidence_anchor_id=anchor_ids[0],
        )
        row["C"][name] = normalized[name].to_dict()
    assessment = assess_semantic_use(context, purpose="R1", observation_ids=tuple(identified.values()),
                                     source_sha256=source_sha, evidence_anchor_ids=anchor_ids)
    derivation = comparability.contained_mass_shadow(normalized["resource_ore_mass"], normalized["resource_treo_grade"],
                                                   reported=normalized["contained_treo"])
    row["D"] = {"assessment": assessment.to_dict(), "formal_admission": "CANDIDATE",
                "formal_reason": "NO_APPROVED_R1_SOURCE_BINDING_FOR_THIS_RESOURCE_STATEMENT",
                "contained_derivation": derivation.to_dict()}
    return row


def _keys(fixture):
    return [[project, kind] for project, criteria in sorted(fixture.decision_inputs["criteria_by_project"].items())
            for kind, comparison in sorted(criteria.items()) if comparison["state"] == "COMPARABLE"]


def run_resource_ablation(profile: Path, baseline: Path, sources: Path, assets: Path, out: Path,
                          *, reread_bindings: Path | None = None) -> dict:
    """Separate evidence reread, numeric transforms and purpose checks on one cohort."""
    reference = ResearchBuild.load(baseline)
    cutoff = reference.receipt["research_cutoff"]
    source_bytes = sources.read_bytes()
    source_sha = sha256(source_bytes).hexdigest()
    settings = load_profile(profile)
    if source_sha != reference.receipt["input_artifacts"]["sources"] or (
        sha256(profile.read_bytes()).hexdigest() != reference.receipt["input_artifacts"]["profile"]
    ):
        raise ValueError("Semantic ablation requires the same locked profile and source bytes")
    store = ContentStore(assets)
    manifest = json.loads(source_bytes)
    registry = load_registry(settings.registry_path)
    verification = verify_source_manifest(settings, manifest, registry, store)
    extraction = _object(baseline / "observations/extraction.json")
    science_rows = {row["observation_id"]: row for row in map(
        json.loads, (baseline / "observations/observations.jsonl").read_bytes().splitlines(),
    )}
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Semantic ablation requires an empty output directory")
    database = copyfile(baseline / "observations/research.sqlite", out / "semantic_research.sqlite")
    with Store(database, trusted_temporal_evidence_ids=extraction["trusted_temporal_evidence_ids"]) as experiment:
        raw = experiment.records(RawObservation)
        release = experiment.get(ResearchRelease, reference.receipt["observation_release_id"])
        selected = checked_research_inputs(experiment, release, purpose="MCDA")
        if {item.observation_id for item in raw} != science_rows.keys() or (
            {item.raw.observation_id for item in selected} != reference.observations.keys()
        ):
            raise ValueError("Semantic ablation observations differ from the release receipt")
        for item in raw:
            frozen = science_rows[item.observation_id]
            for field, expected in (("raw_value", item.raw_value), ("raw_unit", item.raw_unit),
                                    ("project_id", item.project_id), ("scope_id", item.scope_id),
                                    ("source_sha256", item.source_sha)):
                if frozen[field] != expected:
                    raise ValueError("Stored observation differs from the immutable observation artifact")
        reread = _read_originals(experiment, raw, store, set(verification["eligible_sha256"]))
        comparisons = comparability.evaluate_comparability(
            [project.project_id for project in registry.projects], selected, policy=load_policy(),
        )
        actual_keys = [[project, kind] for project, criteria in sorted(comparisons.items())
                       for kind, result in sorted(criteria.items()) if result.comparable]
        if actual_keys != _keys(reference):
            raise ValueError("Live A policy no longer reproduces the frozen comparable keys")
        conversions, derivations = _normalization(selected, reread)
        with experiment.transaction():
            assessments, evidence_uses, pairs = _use_assessments(experiment, comparisons, reread)
    rows = []
    for identity, row in sorted(science_rows.items()):
        rows.append({
            "observation_id": identity, "project_id": row["project_id"], "source_sha256": row["source_sha256"],
            "A": {"outcome": row["outcome"], "reason": row["reason"], "selection_status": row["selection_status"]},
            "B": reread[identity], "C": conversions.get(identity), "D": evidence_uses.get(identity, []),
        })
    fixed_keys = _keys(reference)
    reviews = (_review_candidates(reread_bindings, manifest, source_sha, settings, store) if reread_bindings else [])
    variants = {
        "A": {"formal_keys": fixed_keys, "observations": len(rows), "live_policy": comparability.SEMANTIC_POLICY_VERSION},
        "B": {"formal_keys": fixed_keys, "readback": dict(Counter(row["B"]["status"] for row in rows)),
              "design_contexts_recovered": sum(row["B"]["status"] == "ORIGINAL_SPANS_LOCATED" for row in reviews)},
        "C": {"formal_keys": fixed_keys, "normalization_results": len(conversions),
              "normalization_outcomes": dict(Counter(row["status"] for row in conversions.values())),
              "original_reread_outcomes": dict(Counter(result["status"] for row in reviews for result in row["C"].values()))},
        "D": {"formal_keys": fixed_keys, "semantic_assessments": len(assessments),
              "assessment_outcomes": dict(Counter(row["status"] for row in assessments)),
              "derived_mass_assessments": len(derivations),
              "derived_mass_outcomes": dict(Counter(row["status"] for row in derivations.values())),
              "pair_outcomes": dict(Counter(row["status"] for row in pairs))},
    }
    receipt = {
        "schema": SCHEMA, "status": verification["status"], "research_cutoff": cutoff,
        "normalization_schema": NORMALIZATION_SCHEMA, "semantic_policy": POLICY_VERSION,
        "semantic_vocabulary_sha256": _fingerprint(vocabulary()),
        "sources_sha256": source_sha, "corpus_sha_count": len(verification["verified_sha256"]),
        "eligible_capture_count": len(verification["verified_capture_sha256"]),
        "source_failures": verification["failures"], "observation_release_id": release.release_id,
        "source_candidate_set_sha256": _fingerprint({"frozen_observations": sorted(science_rows),
                                                      "review_cases": sorted(row["case_id"] for row in reviews)}),
        "original_reread_sha256": _fingerprint(reviews), "variants": variants,
        "rows_sha256": _fingerprint(rows), "derivations_sha256": _fingerprint(derivations),
        "assessments_sha256": _fingerprint(assessments), "pairs_sha256": _fingerprint(pairs),
        "formal_gain": {"new": [], "lost": [], "net": 0, "reason": "SHADOW_POLICY_NOT_APPROVED_FOR_FORMAL_ADMISSION"},
    }
    for name, content in (("semantic_ablation.json", receipt), ("semantic_observations.json", rows),
                          ("shadow_contained_mass.json", derivations), ("semantic_assessments.json", assessments),
                          ("semantic_project_pairs.json", pairs), ("resource_original_reread.json", reviews)):
        (out / name).write_text(canonical_json(content) + "\n", encoding="utf-8")
    return receipt
