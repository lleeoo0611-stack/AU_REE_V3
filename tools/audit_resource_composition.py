"""Independently audit named S1 source roles and compare archived MCDA selections."""

import argparse
from contextlib import closing
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path

from au_ree_v3.census_resource_display import _original_phrase
from au_ree_v3.contracts import canonical_json
from au_ree_v3.documents import DocumentCache
from au_ree_v3.fresh_research import _verified_edition
from au_ree_v3.registry import load_profile
from au_ree_v3.sources import ContentStore
from verify_scientific_yield import db, governed, require


ORACLE = Path(__file__).resolve().parents[1] / "tests/fixtures/resource_composition_review_v1.json"
COHORT = ("AU-0098e17a63e7c05c213f", "AU-9bd8e5ed8408cd84d876")


def _pinned_json(path, expected):
    data = path.read_bytes()
    require(sha256(data).hexdigest() == expected, f"Exact review input changed: {path.name}")
    return json.loads(data)


def _source_witnesses(profile, sources, expected, projects):
    documents = []
    cache = DocumentCache()
    editions = {group: {item["receipt_sha256"]: item for item in manifest["editions"]}
                for group, (manifest, _) in sources.items()}
    events = {group: {item["receipt_sha256"]: item for item in manifest["events"] if item["status"] == "PUBLIC_CAPTURED"}
              for group, (manifest, _) in sources.items()}
    for item in expected:
        scope, capture, digest = item["source_set"], item["capture_sha256"], item["source_sha256"]
        edition = editions[scope][capture]
        content = sources[scope][1]
        require(edition["sha256"] == digest and projects[item["case_id"]] in edition["project_ids"],
                "Exact reviewed source does not belong to its case")
        temporal = _verified_edition(profile, edition, events[scope][capture], content)
        require(temporal.status == "ELIGIBLE", "Reviewed original lacks public evidence by cutoff")
        last_page = max(page for page, _, _ in item["witnesses"])
        pdf = cache.pdf(content.read(digest), digest, last_page)
        anchors = []
        for page, role, quote in item["witnesses"]:
            original = _original_phrase(pdf, page, quote)
            require(original is not None, f"Missing independent native {role} anchor in {digest} p{page}")
            anchors.append({"page": original.page, "locator": original.locator, "role": role, "quote": original.text})
        documents.append({key: value for key, value in item.items() if key != "witnesses"} | {
            "url": edition["url"], "native_pages_decoded": last_page,
            "temporal_decision_id": temporal.decision_id, "earliest_proven_public_date": temporal.earliest_proven_public_date,
            "witnesses": anchors,
        })
    return documents


def nominal_interval(token, unit):
    """Use the printed last digit independently of the production normalization."""
    point = Decimal(token.replace(",", ""))
    half_step = Decimal("0.5").scaleb(point.as_tuple().exponent)
    scale = {"ppm": Decimal(1), "kg/t": Decimal(1000)}[unit]
    return (point - half_step) * scale, (point + half_step) * scale


def _wolverine_precision(documents, expected):
    witnesses = {anchor["role"]: (doc["source_sha256"], anchor)
                 for doc in documents if doc["case_id"] == "wolverine-mre-original-and-restatements"
                 for anchor in doc["witnesses"]}
    findings = {}
    for species, rows in expected.items():
        readings = []
        for role, token, unit in rows:
            digest, anchor = witnesses[role]
            require(token in anchor["quote"].split(), f"Independent {species} token missing from native row")
            readings.append({"source_sha256": digest, **anchor, "raw_token": token, "native_unit": unit,
                             "nominal_ppm": [format(n, "f") for n in nominal_interval(token, unit)]})
        findings[species] = readings
    for species, count in (("Dy2O3", 2), ("Y2O3", 3)):
        values = [tuple(map(Decimal, item["nominal_ppm"])) for item in findings[species][:count]]
        require(all(max(left[0], right[0]) > min(left[1], right[1])
                    for index, left in enumerate(values) for right in values[index + 1:]),
                f"Independent nominal conflict for {species} did not reproduce")
    return findings


def _confirm_lineage(criterion, accepted, release_id):
    for link in criterion["lineage"]:
        row = accepted[link["observation_id"]]
        require((link["selection_id"], link["source_sha256"], link["scope_id"],
                 link["anchor_id"], link["temporal_decision"]["decision_id"], link["release_id"]) ==
                (row["accepted"]["acceptance_id"], row["raw"]["source_sha"], row["raw"]["scope_id"],
                 row["anchor"]["anchor_id"], row["decision"]["decision_id"], release_id)
                and row["decision"]["status"] == "ELIGIBLE", "MCDA lineage differs from exact read-only SQLite")


def _confirm_s1_ratio(criterion, tokens, denominator):
    denominator = Decimal(denominator)
    numerator = sum(map(Decimal, tokens.values()))
    with localcontext() as calculation:
        calculation.prec = 34
        share = numerator / denominator * 100
    require(criterion["state"] == "COMPARABLE" and criterion["derivation"]["component_tokens"] == tokens and
            Decimal(criterion["derivation"]["denominator_ppm"]) == denominator and
            Decimal(criterion["accepted_value"]) == share, "Independent archived S1 ratio does not reproduce")


def _case_comparisons(mcda, accepted, cases, control):
    observed = []
    for case in cases:
        cells = mcda["criteria_by_project"][case["project_id"]]
        r1, s1 = cells["R1"], cells["S1"]
        r1_context = r1["scientific_context"]
        require(r1["state"] == "COMPARABLE" and Decimal(r1["accepted_value"]) == Decimal(case["r1_value_kt"])
                and r1_context["effective_date"] == case["effective_date"]
                and [r1_context[key] for key in ("cutoff_grade", "cutoff_unit", "cutoff_analyte")] == case["cutoff"],
                "Archived R1 scope/date/quantity changed")
        require(s1["state"] == case["s1_state"], "Named S1 result no longer matches the frozen case")
        _confirm_lineage(r1, accepted, mcda["release_id"])
        _confirm_lineage(s1, accepted, mcda["release_id"])
        if s1["state"] == "COMPARABLE":
            _confirm_s1_ratio(s1, case["archived_s1_tokens"], case["archived_denominator_ppm"])
        else:
            require(s1["accepted_value"] is None and not s1["lineage"], "An unsupported S1 received an input")
        observed.append({**case, "r1_context": r1_context, "r1_lineage": r1["lineage"],
                         "s1_value_pct": s1["accepted_value"], "s1_lineage": s1["lineage"]})
    require(tuple(mcda["panels"]["issuer_resource_composition"]["eligible_projects"]) == COHORT,
            "Frozen Wudinna and Wolverine comparison cohort changed")
    wudinna = mcda["criteria_by_project"][COHORT[0]]["S1"]
    _confirm_lineage(wudinna, accepted, mcda["release_id"])
    _confirm_s1_ratio(wudinna, control["tokens_ppm"], control["denominator_ppm"])
    ppm_evidence = wudinna["derivation"]["reported_unit_evidence"]
    require((ppm_evidence["source_sha256"], ppm_evidence["physical_page"], ppm_evidence["image_sha256"]) ==
            (control["source_sha256"], control["unit_evidence_page"], control["unit_evidence_image_sha256"]),
            "Frozen Wudinna ppm proof differs from the independent control")
    return observed, {"project_id": COHORT[0], "archived_s1_pct": wudinna["accepted_value"], **control}


def audit(*, profile, fixed, assets, expanded, expanded_assets, archive):
    oracle = json.loads(ORACLE.read_bytes())
    build = archive / "build"
    archive_id = oracle["archive_release_id"]
    release = _pinned_json(archive / "archive/releases" / f"{archive_id}.json", archive_id)
    require(release["source_manifest_hash"] == oracle["fixed_manifest_sha256"] and
            release["mcda_inputs_hash"] == oracle["mcda_inputs_sha256"], "Archived manifest disagrees with the exact S1 inputs")
    mcda = _pinned_json(build / "decision/mcda_inputs.json", oracle["mcda_inputs_sha256"])
    require(mcda["policy_sha256"] == oracle["policy_sha256"], "S1 policy differs from the fixed baseline")
    settings = load_profile(profile)
    cases = oracle["cases"]
    captures = {key: (_pinned_json(manifest, oracle[f"{key}_manifest_sha256"]), ContentStore(store))
                for key, manifest, store in (("fixed", fixed, assets), ("expanded", expanded, expanded_assets))}
    evidence = _source_witnesses(settings, captures, oracle["documents"],
                                 {case["case_id"]: case["project_id"] for case in cases})
    with closing(db(build / "observations/research.sqlite")) as connection:
        accepted = {row["raw"]["observation_id"]: row for row in governed(connection).values()}
        observations, control = _case_comparisons(mcda, accepted, cases, oracle["wudinna_archived_control"])
    return {"status": "PASS", "schema": oracle["schema"], "review_scope": "NAMED_ORIGINALS_NOT_GENERALIZATION",
            **{key: val for key, val in oracle.items() if key.endswith("_sha256") or key == "archive_release_id"},
            "hypothesis_status": "HYPOTHESIS_NOT_SUPPORTED", "new_comparable_s1_keys": 0,
            "new_complete_panel_projects": 0, "generalized_precision_and_recall": "UNMEASURED",
            "documents": evidence, "reviewed_pdf_count": len(evidence),
            "native_pages_decoded": sum(item["native_pages_decoded"] for item in evidence),
            "cases": observations, "wudinna_control": control,
            "wolverine_precision": _wolverine_precision(evidence, oracle["wolverine_interval_readings"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("profile", "fixed", "assets", "expanded", "expanded-assets", "archive", "out"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop("out")
    result = audit(**args)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(canonical_json(result) + "\n")
    print(canonical_json({metric: result[metric] for metric in (
        "status", "new_comparable_s1_keys", "new_complete_panel_projects", "reviewed_pdf_count", "native_pages_decoded",
    )}))


if __name__ == "__main__":
    main()
