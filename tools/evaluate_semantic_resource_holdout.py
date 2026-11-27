"""Replay independent, fixed-original R1 holdout labels with the production gates."""

from __future__ import annotations

import argparse
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from au_ree_v3.comparability import contained_mass_shadow
from au_ree_v3.composition_review import _verify_image
from au_ree_v3.contracts import EvidenceAnchor, RawObservation, canonical_json
from au_ree_v3.contracts.temporal import evaluate_temporal
from au_ree_v3.documents import DocumentCache
from au_ree_v3.normalization import normalize_measurement
from au_ree_v3.observations import digest
from au_ree_v3.semantics import SemanticContext, assess_semantic_use
from au_ree_v3.sources import ContentStore, validate_edition_proofs

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
ANNOTATIONS = FIXTURES / "semantic_resource_holdout_v1.json"
PROFILE = Path(__file__).resolve().parents[1] / "config" / "research_profiles"
DESIGN = PROFILE / "semantic_reread_bindings_v1.json"
KINDS = {"ore": "ORE_MASS", "grade": "GRADE", "contained": "CONTAINED_OXIDE_MASS"}
TARGET_UNITS = {"ore": "Mt", "grade": "%", "contained": "kt TREO"}
COMPARABLE = {"COMPARABLE_DERIVED", "COMPARABLE_DIRECT"}
NUMBER = re.compile(r"(?<!\w)\d[\d,]*(?:\.\d+)?")


def confusion(predictions: list[tuple[bool, bool]]) -> dict:
    """Count expected/actual outcomes and keep undefined metrics explicit."""
    tp = predictions.count((True, True))
    tn = predictions.count((False, False))
    fp = predictions.count((False, True))
    fn = predictions.count((True, False))
    return {
        "tp": tp, "tn": tn, "fp": fp, "fn": fn, "n": len(predictions),
        "precision": tp / (tp + fp) if tp + fp else "UNMEASURED",
        "recall": tp / (tp + fn) if tp + fn else "UNMEASURED",
    }


def _context_ref(case: dict, row: dict) -> dict:
    quote = row["quote"]
    page = row["page"]
    return {
        "physical_page": page, "quote": quote, "source_sha256": case["source_sha256"],
        "evidence_anchor_id": digest(["independent-holdout-context-v1", case["source_sha256"], page, quote]),
    }


def _select_edition(case: dict, editions: list[dict]) -> dict:
    matches = [
        item for item in editions
        if item["sha256"] == case["source_sha256"] and case["project_id"] in item["project_ids"]
    ]
    if not matches:
        raise ValueError(f"Exact project-linked source edition missing: {case['case_id']}")
    return min(matches, key=lambda item: (item["event_acquired_at"], item["receipt_sha256"]))


def _measure(case, row, pages, source_key, context) -> tuple[RawObservation, EvidenceAnchor] | None:
    quote = row["quote"]
    page = row["page"]
    if quote not in pages[page - 1]:
        return None
    tokens = NUMBER.findall(quote)
    token = tokens[row["number"]]
    identity = [case["source_sha256"], context.statement_id, page, quote, row["name"], token]
    anchor = EvidenceAnchor(
        anchor_id=digest(["independent-holdout-value-anchor-v1", identity]),
        source_key=source_key, source_sha=case["source_sha256"], page=page,
        locator=f"Physical PDF page {page}, reviewed {row['name']} cell", quote=quote,
        column=row["name"], role="REPORTED_RESOURCE_VALUE",
    )
    raw = RawObservation(
        observation_id=digest(["independent-holdout-observation-v1", identity]),
        project_id=context.project_id, scope_id=context.scope_id, statement_id=context.statement_id,
        source_key=source_key, source_sha=case["source_sha256"], anchor_id=anchor.anchor_id,
        variable=row["name"], raw_value=token, raw_unit=row["unit"], status="REPORTED",
        transform_version="independent-scoped-original-replay-v1",
        material_basis=context.material, chemical_basis=row["basis"],
        denominator_basis=context.denominator_basis, effective_date=context.effective_date,
        quantity_kind=context.quantity_kind,
        original_payload={"statement_context": case["context_anchors"], "partition": "HOLDOUT"},
    )
    return raw, anchor


def _formal_resource_context(base: SemanticContext, measurements: dict) -> SemanticContext:
    if "contained" in measurements:
        raw, _ = measurements["contained"]
        species = (raw.chemical_basis,) if raw.chemical_basis != "TREO" else base.treo_members
        return replace(base, quantity_kind="CONTAINED_OXIDE_MASS", chemical_species=species)
    return replace(base, quantity_kind="CONTAINED_OXIDE_MASS", value_state="NOT_DISCLOSED")


def _normalization(case, measured, base, context_evidence):
    results = {}
    for name, (raw, anchor) in measured.items():
        species = (raw.chemical_basis,) if raw.chemical_basis not in {"ORE", "TREO"} else ()
        context = replace(
            base, quantity_kind=KINDS[name], chemical_species=species,
            treo_members=base.treo_members if raw.chemical_basis == "TREO" else (),
            denominator_basis=raw.denominator_basis,
        )
        results[name] = normalize_measurement(
            raw.raw_value, raw.raw_unit, TARGET_UNITS[name], source_context=context,
            observation_id=raw.observation_id, source_sha256=case["source_sha256"],
            evidence_anchor_id=anchor.anchor_id, context_evidence=context_evidence,
        )
    return results


def _execute_case(case, editions, store, documents, cutoff, timezone) -> dict:
    edition = _select_edition(case, editions)
    proof = validate_edition_proofs(store, edition, ZoneInfo(timezone))
    temporal = evaluate_temporal(
        source_sha256=case["source_sha256"], cutoff=cutoff, effective_date=case["effective_date"],
        publication_date=edition["publication_date"], available_date=edition["available_date"],
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proof,
    )
    original = store.read(case["source_sha256"])
    last_page = max(entry["page"] for entry in (*case["cells"], *case["context_anchors"]))
    document = documents.pdf(original, case["source_sha256"], last_page=last_page)
    pages = [" ".join(page.split()) for page in document.pages]
    if any(anchor["quote"] not in pages[anchor["page"] - 1] for anchor in case["context_anchors"]):
        raise ValueError(f"Required original context quote missing: {case['case_id']}")
    context_evidence = tuple(_context_ref(case, row) for row in case["context_anchors"])
    image_evidence = [
        {**image, "decoder_runtime": _verify_image(original, image["page"], image["slot"], image["sha256"])}
        for image in case.get("image_anchors", ())
    ]
    context_evidence += tuple(
        {"physical_page": image["page"], "locator": image["locator"], "source_sha256": case["source_sha256"],
         "evidence_anchor_id": digest(["independent-holdout-image-v1", case["source_sha256"],
                                       image["page"], image["slot"], image["sha256"]]),
         "image_sha256": image["sha256"]}
        for image in image_evidence
    )
    base = SemanticContext(
        quantity_kind="GRADE", project_id=case["project_id"],
        scope_id=digest([case["project_id"], case["scope"]]),
        statement_id=digest([case["source_sha256"], case["statement"]]),
        resource_class="MINERAL_RESOURCE", jorc_categories=tuple(case["jorc_categories"]),
        cutoff_grade=case["cutoff_grade"], cutoff_unit=case["cutoff_unit"],
        cutoff_analyte=case["cutoff_analyte"], effective_date=case["effective_date"],
        material=case["material"], denominator_basis="WHOLE_ORE",
        treo_members=tuple(case["treo_members"]), temporal_status=temporal.status,
        temporal_decision_id=temporal.decision_id, research_cutoff=cutoff,
    )
    measured = {}
    observations = []
    for row in case["cells"]:
        context = replace(base, quantity_kind=KINDS[row["name"]],
                          denominator_basis=base.denominator_basis if row["name"] == "grade" else None)
        if entry := _measure(case, row, pages, edition["source_key"], context):
            measured[row["name"]] = entry
        source_token = entry[0].raw_value if entry else None
        observations.append({
            "cell": row["name"], "expected_token": row["expected_token"], "actual_token": source_token,
            "match": source_token == row["expected_token"], "raw": entry[0].to_dict() if entry else None,
            "anchor": entry[1].to_dict() if entry else None,
        })

    normalized = _normalization(case, measured, base, context_evidence)
    shadow = None
    if {"ore", "grade"} <= normalized.keys():
        shadow = contained_mass_shadow(normalized["ore"], normalized["grade"], reported=normalized.get("contained"))
    common = {
        "observation_ids": tuple(raw.observation_id for raw, _ in measured.values()),
        "source_sha256": case["source_sha256"],
        "evidence_anchor_ids": tuple(anchor.anchor_id for _, anchor in measured.values()),
    }
    observed_r1 = assess_semantic_use(_formal_resource_context(base, measured), purpose="R1", **common)
    # The unchanged formal S1 path requires a comparable source-reported R1.
    # Capture verified chemistry separately without constructing a formal S1 input.
    s1_annotation = case["expected"].get("s1_chemistry_annotation")
    expected = case["expected"]
    output = {
        "case_id": case["case_id"], "prior_case_id": case["prior_case_id"],
        "project_id": case["project_id"], "company": case["company"], "report_family": case["report_family"],
        "source_sha256": case["source_sha256"], "source_url": edition["url"],
        "source_receipt_sha256": edition["receipt_sha256"], "temporal": temporal.to_dict(),
        "context_evidence": context_evidence, "reviewed_image_tables": image_evidence,
        "observations": observations,
        "normalization": {key: value.to_dict() for key, value in normalized.items()},
        "r1_shadow": shadow.to_dict() if shadow else None, "r1_reported_semantic": observed_r1.to_dict(),
        "s1_formal": {"status": "NOT_RUN", "reason": "NO_COMPARABLE_REPORTED_R1_HOLDOUT"},
        "s1_independent_chemistry_annotation": s1_annotation,
    }
    shadow_compatible = shadow is not None and shadow.status in COMPARABLE
    checks = [all(row["match"] for row in observations), shadow_compatible == expected["r1_shadow_compatible"]]
    checks.extend((
        (observed_r1.status in COMPARABLE) == expected["r1_formal_compatible"],
        observed_r1.status == expected["r1_reported_semantic_status"],
        {key for key, value in normalized.items() if value.status == "NORMALIZED"} == set(expected["normalized_cells"]),
    ))
    if expected.get("reject_chemical_species"):
        checks.append("chemical_species" in observed_r1.incompatible_dimensions)
    if expected.get("r1_shadow_value_kt") is not None:
        checks.append(shadow_compatible and Decimal(shadow.value) == Decimal(expected["r1_shadow_value_kt"])
                      and list(map(Decimal, shadow.derivation["rounding_interval_kt"])) ==
                      list(map(Decimal, expected["r1_shadow_rounding_kt"])))
    if "temporal_reason" in expected:
        checks.append(temporal.reason == expected["temporal_reason"])
    output.update(status="PASS" if all(checks) else "FAIL", expected=expected)
    return output


def _check_partition(cases: tuple[dict, ...]) -> None:
    design = tuple(json.loads(DESIGN.read_bytes())["cases"])
    for key in ("project_id", "source_sha256", "company", "report_family"):
        test_values = [row[key] for row in cases]
        if len(set(test_values)) != len(test_values) or set(test_values) & {row[key] for row in design}:
            raise ValueError(f"Holdout leaks into design or repeats its sampling unit: {key}")
    bound_ids = {
        binding["project"]["entity_id"] for path in PROFILE.glob("resource_bindings_v*.json")
        for binding in json.loads(path.read_bytes())["bindings"]
    }
    if bound_ids & {row["project_id"] for row in cases}:
        raise ValueError("Holdout project occurs among previously configured resource binding projects")


def evaluate(manifest_path: Path, store_path: Path) -> dict:
    labels_bytes = ANNOTATIONS.read_bytes()
    labels = json.loads(labels_bytes)
    manifest_bytes = manifest_path.read_bytes()
    if sha256(manifest_bytes).hexdigest() != labels["source_manifest_sha256"]:
        raise ValueError("Holdout source manifest differs from fixed original scope")
    manifest = json.loads(manifest_bytes)
    if (len({row["sha256"] for row in manifest["editions"]}) != labels["unique_source_sha256s"]
            or manifest["cutoff"] != labels["cutoff"] or manifest["timezone"] != labels["timezone"]):
        raise ValueError("Holdout manifest counts or cutoff differ from fixed scope")
    cases = tuple(labels["cases"])
    _check_partition(cases)
    documents = DocumentCache()
    store = ContentStore(store_path)
    results = [
        _execute_case(case, manifest["editions"], store, documents, labels["cutoff"], labels["timezone"])
        for case in cases
    ]
    observations = [(True, row["match"]) for item in results for row in item["observations"]]
    normalization = [
        (row["cell"] in item["expected"]["normalized_cells"],
         item["normalization"].get(row["cell"], {}).get("status") == "NORMALIZED")
        for item in results for row in item["observations"]
    ]
    shadow = [(case["expected"]["r1_shadow_compatible"], item["r1_shadow"] is not None
               and item["r1_shadow"]["status"] in COMPARABLE) for case, item in zip(cases, results)]
    r1 = [(case["expected"]["r1_formal_compatible"], item["r1_reported_semantic"]["status"] in COMPARABLE)
          for case, item in zip(cases, results)]
    return {
        "schema": "semantic-resource-independent-holdout-evaluation-v1",
        "status": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
        "labels_sha256": sha256(labels_bytes).hexdigest(),
        "source_manifest_sha256": labels["source_manifest_sha256"], "cutoff": labels["cutoff"],
        "timezone": labels["timezone"], "unique_source_sha256s": labels["unique_source_sha256s"],
        "observation_expected_token_replay": confusion(observations),
        "observation_scoped_normalization_eligibility": confusion(normalization),
        "autonomous_observation_detection": {"status": "UNMEASURED", "reason": "SCOPED_OPERATOR_LOCATORS"},
        "r1_shadow_use_compatibility": confusion(shadow),
        "r1_reported_use_compatibility": confusion(r1),
        "s1_formal_use_compatibility": confusion([]),
        "case_results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = evaluate(args.manifest, args.store)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(canonical_json(receipt) + "\n")
    if receipt["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
