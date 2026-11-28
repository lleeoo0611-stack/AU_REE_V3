"""Verify a sealed R1/S1 baseline and build a criterion-level evidence gap ledger."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import replace
from decimal import Decimal, localcontext
import json
from pathlib import Path
from shutil import copyfile
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from unicodedata import normalize

from au_ree_v3.comparability import evaluate_comparability, resource_semantic_context
from au_ree_v3.contracts import ResearchRelease, canonical_json
from au_ree_v3.documents import DocumentCache
from au_ree_v3.fresh_decision import POLICY_SHA256, load_policy
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.observations import OXIDE_VARIABLES
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.research_delta import ResearchBuild
from au_ree_v3.research_inputs import checked_research_inputs
from au_ree_v3.semantics import assess_semantic_use
from au_ree_v3.sources import ContentStore
from au_ree_v3.store import Store
from tools.census_gap_inventory import FIXED_SOURCES, SOURCE_SHA, checked, fingerprint, git_blob, read_jsonl, require
from tools.verify_census_stage3_release import check_store, verify_closure, verify_replay

ROOT = Path(__file__).resolve().parents[1]
MAIN = "c8bce8a893358a63bf2c587e44e0bc3f84523db7"
CODE = "33587f23eda8243ad7713f2a5524f93ac9b2ce65"
CANDIDATE = "bdb9e868e5cb2a514e608231db35cdb27c3e77b2fc9427f7dbb444409f511c78"
PRIOR = "docs/research/census_scientific_yield_verification.json"
FOCUS = "docs/research/criterion_original_evidence_v1.json"
STAGE5 = "work/census-stage5/final"
PROFILE = "config/research_profiles/au_ree_census_display_v1.json"
CRITERIA = ("R1", "S1")
RESOURCE = {"ore_mt", "grade", "contained_treo_kt"}
OXIDES = {**OXIDE_VARIABLES, "MREO": "mreo_ppm"}
MINIMUM = {
    "R1": "同一時點與資源聲明的核准 JORC 級別、cutoff、全礦礦量、TREO 品位和報告含量，含原文精度。",
    "S1": "可比較 R1 的同一聲明，以及四種磁性氧化物或明示同四種的合計，分母須為已驗證的全礦 TREO。",
}
FALSIFICATION = {
    "R1": "同版原件未報含量或同範圍分級，或含量不在礦量與品位的原報精度區間。",
    "S1": "全礦分母、四種成分、版本或聲明無法核對，或無法排除精度、來源及物料衝突。",
}


def inputs(workspace, replays):
    prior = json.loads(git_blob(ROOT, MAIN, PRIOR))
    archive_dir = workspace / STAGE5
    archive = json.loads(checked(archive_dir / "archive/releases" / f"{CANDIDATE}.json", CANDIDATE))
    build_dir = archive_dir / "build"
    build = ResearchBuild.load(build_dir)
    require(prior["candidate_release_id"] == CANDIDATE and
            prior["research_code_commit"] == archive["code_commit"] == build.receipt["code_commit"] == CODE
            and archive["build_receipt_sha256"] == build.receipt_sha
            and archive["input_artifacts"] == prior["research_input_artifacts"] == build.receipt["input_artifacts"],
            "Stage 5 research, input or code identity differs")
    outputs = archive["output_hashes"]
    require(len(outputs) == 8 and outputs == prior["science_output_sha256"] == build.receipt["scientific_artifacts"],
            "Stage 5 science output hashes differ")
    require(len(replays) == len({path.resolve() for path in replays}) == 2, "Need distinct readback directories")
    for name, run in zip(("cas", "replica-cas"), replays, strict=True):
        verify_closure(archive_dir / name, {CANDIDATE, *archive["required_object_sha256"]})
        verify_replay(run, CANDIDATE, outputs)
        for path, digest in outputs.items():
            checked(run / "rebuilt" / path, digest)
    profile = load_profile(ROOT / PROFILE)
    registry = load_registry(profile.registry_path)
    cas = ContentStore(archive_dir / "cas")
    manifest = json.loads(checked(workspace / FIXED_SOURCES, SOURCE_SHA))
    source_proof = verify_source_manifest(profile, manifest, registry, cas)
    require(source_proof["status"] == "PASS" and len(source_proof["verified_sha256"]) == 144 and
            len(source_proof["eligible_sha256"]) == 144 and len(source_proof["verified_capture_sha256"]) == 148
            and source_proof["projects"] == 93,
            "Fixed original and capture set differs")
    require(build.decision_inputs["policy_sha256"] == POLICY_SHA256 and build.decision_inputs["policy"] == load_policy()
            and set(build.census_projects) == {p.project_id for p in registry.projects}, "Study policy or cohort differs")
    pins = prior["receipts"]
    inherited = {}
    for name in ("scoped_work_queue", "original_statement_holds", "independent_new_science_comparison",
                 "separate_recovered_source_editions", "independent_display_package"):
        ref = pins[name]
        inherited[name] = checked(workspace / ref["path"], ref["sha256"])
    display = json.loads(inherited["independent_display_package"])
    require(display["status"] == "PASS" and
            (display["extension_fields"], display["extension_present_cells"], display["extension_denominator"]) == (9, 40, 837),
            "Frozen independent display coverage differs")
    comparator = json.loads(inherited["independent_new_science_comparison"])
    require(comparator["status"] == "PASS" and comparator["mcda"]["comparable_after"] == 6
            and not comparator["mcda"]["changed_panels"] and comparator["mcda"]["policy_sha256"] == POLICY_SHA256,
            "Independent Stage 5 comparison did not verify fixed cohort and decisions")
    return build, build_dir, manifest, cas, registry, inherited, {
        "frozen_main": MAIN, "candidate_code": CODE, "candidate_id": CANDIDATE,
        "observation_release_id": build.receipt["observation_release_id"], "source_manifest_sha256": SOURCE_SHA,
        "input_artifact_sha256": archive["input_artifacts"], "science_output_sha256": outputs,
        "decision_policy_sha256": POLICY_SHA256, "source_count": 144, "capture_count": 148,
        "sealed_display_recheck": {"source": "STAGE5_INDEPENDENT_RECEIPT", "extension_fields": 9,
                                  "extension_present_cells": 40, "extension_denominator": 837},
        "received_recipes": {name: pins[name] for name in inherited},
        "cold_science": {"status": "PASS", "runs": 2, "objects_each": len(archive["required_object_sha256"]) + 1,
                         "artifacts_each": 8, "fault_domain": "SHARED_LOCAL_FILESYSTEM"},
        "sqlite": check_store(build_dir),
        "F": {**build.receipt["gates"], "F4": "PASS", "F5": archive["replica_inventory"]["status"]},
        "historical_O2": prior["gates"]["historical_O2"],
    }


def assess(comparison, criterion):
    variable = "grade" if criterion == "S1" else "contained_treo_kt"
    base = next(item for item in comparison.inputs if item.raw.variable == variable)
    context = resource_semantic_context(base)
    transforms = ()
    if criterion == "S1":
        context = replace(context, quantity_kind="OXIDE_COMPOSITION", chemical_species=tuple(OXIDE_VARIABLES),
                          denominator_basis="TREO_WITHIN_WHOLE_ORE",
                          treo_members=tuple(comparison.context["treo_denominator"]["chemical_species"]))
        transforms = ({"kind": "DERIVATION", "formula_version": comparison.derivation["method"], **comparison.derivation},)
    return assess_semantic_use(
        context, purpose=criterion, source_sha256=base.source.sha256,
        observation_ids=tuple(row.raw.observation_id for row in comparison.inputs),
        evidence_anchor_ids=tuple(row.anchor.anchor_id for row in comparison.inputs), transforms=transforms,
    ).to_dict()


def store_readback(build, directory):
    path = directory / "observations/research.sqlite"
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as archive:
        persisted = archive.execute("SELECT COUNT(*) FROM semantic_assessments").fetchone()[0]
        scoped_rows = archive.execute(
            "SELECT o.payload, v.payload, t.payload FROM raw_observations AS o "
            "JOIN candidates USING(raw_record_id) JOIN validations AS v USING(candidate_id) "
            "JOIN temporal_decisions AS t USING(validation_id) ORDER BY o.observation_id").fetchall()
    review = defaultdict(list)
    archive_rows = {row["observation_id"]: row for row in read_jsonl((directory / "observations/observations.jsonl").read_bytes())}
    for raw, validation, temporal in (tuple(map(json.loads, row)) for row in scoped_rows):
        existing = archive_rows[raw["observation_id"]]
        require((existing["project_id"], existing["source_sha256"], existing["scope_id"], existing["raw_value"],
                 existing["raw_unit"], existing["outcome"]) ==
                (raw["project_id"], raw["source_sha"], raw["scope_id"], raw["raw_value"], raw["raw_unit"], validation["outcome"]),
                "Stored raw observation and archive observation differ")
        review[raw["project_id"]].append({
            "observation_id": raw["observation_id"], "source_sha256": raw["source_sha"], "scope_id": raw["scope_id"],
            "statement_id": raw["statement_id"], "anchor_id": raw["anchor_id"], "variable": raw["variable"],
            "validation_reason": validation.get("reason") or existing["reason"],
            "validation_outcome": validation["outcome"], "temporal_status": temporal["status"],
            "selection_status": existing["selection_status"],
        })
    with TemporaryDirectory(prefix="criterion-readback-") as temp:
        snapshot = copyfile(path, Path(temp) / "snapshot.sqlite")
        extraction = json.loads((directory / "observations/extraction.json").read_bytes())
        with Store(snapshot, trusted_temporal_evidence_ids=extraction["trusted_temporal_evidence_ids"]) as store:
            release = store.get(ResearchRelease, build.receipt["observation_release_id"])
            all_present = checked_research_inputs(store, release, purpose="PUBLICATION")
            formal = checked_research_inputs(store, release, purpose="MCDA")
    validated = sum(row["outcome"] == "VALIDATED" for row in archive_rows.values())
    require(len(archive_rows) == len(scoped_rows) == 53 and validated == 37 and len(all_present) == 31 and persisted == 0,
            "Stage 5 archive numerical or SemanticAssessment inventory differs")
    require({item.raw.observation_id for item in all_present} == build.observations.keys(),
            "Frozen selected observations differ from the Store")
    return formal, review, {"persisted_semantic_assessments": persisted,
                            "raw_observations": len(scoped_rows), "numeric_validated": validated,
                            "numeric_accepted": len(all_present),
                            "mcda_eligible_purpose_rows": len(formal)}


def fold(text):
    return " ".join(normalize("NFKC", text).split())


def match_quote(document, page, text):
    if not text or type(page) is not int or not 1 <= page <= len(document.pages):
        return False
    return fold(text) in fold(document.pages[page - 1])


def s1_check(comparison, original_body):
    proof = comparison.derivation
    used = {item.raw.observation_id: item for item in comparison.inputs}
    originals = [used[identity] for identity in proof["input_observation_ids"]]
    numerator, grade = originals[:-1], originals[-1]
    groups = {name: next(item.raw.raw_value for item in numerator if item.raw.variable == OXIDES[name])
              for name in proof["component_tokens"]}
    require(groups == proof["component_tokens"] and grade.raw.raw_value == proof["treo_token"]
            and grade.raw.raw_unit == proof["treo_unit"], "S1 arithmetic inputs differ from exact Store tokens")
    with localcontext() as arithmetic:
        arithmetic.prec = 34
        total = sum(map(Decimal, groups.values()))
        denominator = Decimal(grade.raw.raw_value) * (10000 if grade.raw.raw_unit == "%" else 1)
        percent = total / denominator * 100
    require(percent == Decimal(comparison.value), "Independent S1 arithmetic differs")
    if "reported_unit_evidence" in proof and proof["reported_unit_evidence"].get("image_sha256"):
        pin = proof["reported_unit_evidence"]
        with TemporaryDirectory(prefix="criterion-image-") as temp:
            pdf = Path(temp) / "original.pdf"
            pdf.write_bytes(original_body)
            page = str(pin["physical_page"])
            output = Path(temp) / "image"
            subprocess.run(["pdfimages", "-f", page, "-l", page, str(pdf), str(output)],
                           check=True, capture_output=True, timeout=30)
            checked(Path(temp) / f"image-{pin['image_slot']:03d}.ppm", pin["image_sha256"])
    return {"numerator_ppm": str(total), "denominator_ppm": str(denominator), "share_pct": str(percent)}


def companion_precisions(fixture):
    """Compare native print intervals before any derived consequence is considered."""
    reviewed = []
    for case in fixture:
        for conflict in case.get("precision_conflicts", ()):
            ranges = []
            for token, unit in conflict["native_tokens"]:
                scale = {"kg/t": Decimal(1000), "ppm": Decimal(1)}[unit]
                value = Decimal(token.replace(",", ""))
                half = Decimal(5).scaleb(value.as_tuple().exponent - 1)
                ranges.append(((value - half) * scale, (value + half) * scale))
            left, right = ranges
            require(max(left[0], right[0]) > min(left[1], right[1]), "Nominal precision intervals no longer conflict")
            reviewed.append({"project_id": case["project_id"], "species": conflict["species"],
                             "native_tokens": conflict["native_tokens"], "status": "SCIENTIFIC_CONFLICT",
                             "nominal_intervals_ppm": [[str(value) for value in interval] for interval in ranges]})
    return reviewed


def eligible_editions(manifest):
    result = defaultdict(lambda: defaultdict(list))
    for row in manifest["editions"]:
        if row["temporal_decision"]["status"] != "ELIGIBLE":
            continue
        for project in row["project_ids"]:
            result[project][row["sha256"]].append({"url": row["url"], "edition_id": row["edition_id"],
                                                  "capture_sha256": row["receipt_sha256"]})
    return result


def check_originals(comparisons, cases, editions, cas):
    uses = defaultdict(list)
    for project, group in comparisons.items():
        for criterion in CRITERIA:
            for item in group[criterion].inputs if group[criterion].comparable else ():
                uses[project, item.source.sha256].append((item.anchor.page, item.anchor.quote))
    for case in cases:
        for row in case["originals"]:
            uses[case["project_id"], row["source_sha256"]].append((row["physical_page"], row["quote"]))
    cache = DocumentCache()
    pins, page_counts = {}, {}
    for (project, sha), anchors in sorted(uses.items()):
        require(sha in editions[project], "Original witness not an eligible edition for its exact Project")
        if sha not in pins:
            pins[sha] = cas.read(sha)
        pages = max(page for page, _ in anchors)
        document = cache.pdf(pins[sha], sha, last_page=pages)
        page_counts[sha] = len(document.pages)
        require(all(match_quote(document, page, text) for page, text in anchors), f"Original quote mismatch for {sha}")
    return pins, {"unique_originals": len(pins), "native_page_extent": sum(page_counts.values()),
                  "unique_page_quotes": sum(len(set(anchors)) for anchors in uses.values())}


def independent_comparison(build, live, originals):
    reviewed = {}
    for project in build.census_projects:
        for criterion in CRITERIA:
            actual, frozen = live[project][criterion], build.decision_inputs["criteria_by_project"][project][criterion]
            check = {
                "state": "COMPARABLE" if actual.comparable else "UNKNOWN", "reason": actual.reason,
                "accepted_value": actual.value, "unit": actual.unit, "source_token": actual.source_token,
                "lineage": [item.lineage() for item in actual.inputs], "scientific_context": actual.context,
                "derivation": actual.derivation,
            }
            require(canonical_json(check) == canonical_json({key: frozen[key] for key in check}),
                    f"Frozen and rerun formal criterion disagree: {project}/{criterion}")
            if not actual.comparable:
                continue
            independent = s1_check(actual, originals[actual.inputs[0].source.sha256]) if criterion == "S1" else None
            reviewed[project, criterion] = {"record": assess(actual, criterion), "independent_s1": independent}
    return reviewed


def fallback_gaps(criterion, established, observed, source, suggestions):
    tags = set()
    if not source:
        tags.add("SOURCE_UNAVAILABLE")
    if any("EXACT_EDITION_REQUIRED" in row["kinds"] for row in suggestions):
        tags.add("EDITION_MISMATCH")
    if criterion == "S1" and not established:
        tags.add("PREREQUISITE_R1")
    if criterion == "S1":
        tags.update(("COMPONENTS", "CHEMICAL_DENOMINATOR"))
    if not observed and any(row["raw_locator"] for row in suggestions):
        tags.add("NATIVE_LOCATOR_REVIEW")
    if not observed and not any(row["raw_locator"] for row in suggestions):
        tags.add("NATIVE_LOCATOR")
    for row in observed:
        reason = row["validation_reason"]
        if row["temporal_status"] != "ELIGIBLE":
            tags.add("TIME_ELIGIBILITY")
        if "DATE" in reason:
            tags.add("ESTIMATE_DATE")
        if "CHEMICAL" in reason:
            tags.add("CHEMICAL_DENOMINATOR")
        if "CONFLICT" in reason:
            tags.add("PRECISION_OR_QUANTITY_CONFLICT")
        if row["selection_status"] == "SHADOW_OUTSIDE_REPORTING_SCOPE":
            tags.add("STATEMENT_SCOPE")
    if criterion == "R1" and any(row["selection_status"] == "SELECTED" for row in observed):
        tags.add("APPROVED_USE_UNWIRED")
    return tags or {"SAME_STATEMENT_BUNDLE"}


def ledger(build, registry, scoped, editions, cases, reviewed, inherited):
    work = defaultdict(list)
    for row in read_jsonl(inherited["scoped_work_queue"]):
        for criterion in row["mcda_relevance"]:
            if criterion in CRITERIA:
                work[row["project_id"], criterion].append(row)
    holds = defaultdict(list)
    for row in json.loads(inherited["original_statement_holds"])["cases"]:
        holds[row["project_id"]].append({"source_sha256": row["source_sha256"], "reason": row["reason"],
                                         "status": row["status"], "locators": [item["locator"] for item in row["evidence"]]})
    known = {(row["project_id"], row["criterion"]): row for row in cases}
    names = {row.project_id: row.name for row in registry.projects}
    matrix = build.decision_inputs["criteria_by_project"]
    for project in build.census_projects:
        for criterion in CRITERIA:
            key = project, criterion
            existing, focus = matrix[project][criterion], known.get(key)
            originals, tasks = editions[project], work[key]
            observations = [row for row in scoped.get(project, [])
                            if row["variable"] in (RESOURCE if criterion == "R1" else RESOURCE | set(OXIDES.values()))]
            complete = existing["state"] == "COMPARABLE"
            r1 = matrix[project]["R1"]["state"] == "COMPARABLE"
            tags = set() if complete else fallback_gaps(criterion, r1, observations, originals, tasks)
            if focus:
                tags.update(focus["gaps"])
            if holds[project] and not complete:
                tags.add("REVIEWED_SOURCE_CONFLICT")
            yield {
                "project_id": project, "project_name": names[project], "criterion": criterion,
                "baseline_state": existing["state"], "baseline_reason": existing["reason"],
                "baseline_value": existing["accepted_value"], "baseline_unit": existing["unit"],
                "baseline_comparable_lineage": existing["lineage"], "diagnostic_semantic_assessment":
                    reviewed[key]["record"] if complete else None,
                "independent_s1_recheck": reviewed[key]["independent_s1"] if complete else None,
                "evidence_gap_dimensions": sorted(tags), "minimum_original": focus["minimum_original"] if focus else MINIMUM[criterion],
                "research_use": f"EXISTING_MCDA_{criterion}", "falsification": focus["falsification"] if focus else FALSIFICATION[criterion],
                "next_action": focus["next_action"] if focus else (
                    "按既有原件及選值回歸。" if complete else "從已核案別的原件定位同一聲明與欄位，再執行既有用途驗證。"),
                "focus_priority": focus["priority"] if focus else None,
                "focus_role": focus["role"] if focus else None,
                "focus_original_witnesses": focus["originals"] if focus else [],
                "source_eligible_exact_sha256": sorted(originals),
                "source_candidates": [{"source_sha256": sha, "captures": sorted(value, key=lambda item: item["edition_id"])}
                                      for sha, value in sorted(originals.items())],
                "stage5_priority_keys": [row["queue_id"] for row in tasks],
                "store_scope_review": observations, "source_holds": holds[project],
            }


def run(workspace, replays, out, focus_path=ROOT / FOCUS):
    build, directory, manifest, cas, registry, inherited, result = inputs(workspace, replays)
    focus_bytes = focus_path.read_bytes()
    focus = json.loads(focus_bytes)
    annotated = [(item["project_id"], item["criterion"]) for item in focus["cases"]]
    require(focus["schema"] == "criterion-original-evidence-v1" and focus["source_manifest_sha256"] == SOURCE_SHA
            and focus["cutoff"] == manifest["cutoff"] and len(annotated) == len(set(annotated))
            and all(project in build.census_projects and criterion in CRITERIA for project, criterion in annotated),
            "Original reviewer fixture differs from the locked source universe")
    formal, review, store = store_readback(build, directory)
    comparisons = evaluate_comparability(build.census_projects, formal, policy=load_policy())
    editions = eligible_editions(manifest)
    originals, readback = check_originals(comparisons, focus["cases"], editions, cas)
    frozen = independent_comparison(build, comparisons, originals)
    rows = list(ledger(build, registry, review, editions, focus["cases"], frozen, inherited))
    keys = Counter(row["criterion"] for row in rows if row["baseline_state"] == "COMPARABLE")
    require(len(rows) == len({(row["project_id"], row["criterion"]) for row in rows}) == 186 and
            keys == {"R1": 4, "S1": 2} and len(build.census) == 4743, "Baseline criterion axis or frozen coverage differs")
    require(build.decision_results["panels"]["issuer_resource_composition"]["eligible_count"] == 2 and
            build.decision_results["panels"]["six_domain"]["eligible_count"] == 0, "Baseline panels differ")
    out.mkdir(parents=True, exist_ok=False)
    ledger_path = out / "criterion_evidence_gap_ledger.jsonl"
    ledger_path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")
    result.update({
        "schema": "census-criterion-evidence-gap-v1", "status": "PASS", "S6-0": "PASS",
        "research_cutoff": manifest["cutoff"], "timezone": manifest["timezone"],
        "grid": {"projects": 93, "criteria": list(CRITERIA), "criterion_keys": len(rows), "core_census_keys": len(build.census)},
        "fixed_formal_keys": [[row["project_id"], row["criterion"]] for row in rows if row["baseline_state"] == "COMPARABLE"],
        "complete_r1_s1_projects": 2, "complete_six_domain_projects": 0,
        "baseline_dispositions": {criterion: dict(Counter(row["baseline_reason"] for row in rows if row["criterion"] == criterion))
                                  for criterion in CRITERIA},
        "gap_dimensions": dict(Counter(tag for row in rows if row["baseline_state"] == "UNKNOWN" for tag in row["evidence_gap_dimensions"])),
        "store_readback": store, "diagnostic_assessment": {
            "rows": len(frozen), "status_counts": dict(Counter(item["record"]["status"] for item in frozen.values())),
            "scope": "Read-only audit of current inputs; no change to frozen comparator or persisted selection"},
        "original_evidence_readback": readback, "original_fixture_sha256": fingerprint(focus_bytes),
        "companion_precision_conflicts": companion_precisions(focus["cases"]),
        "ledger_sha256": fingerprint(ledger_path.read_bytes()),
        "priority_keys": [[row["project_id"], row["criterion"]] for row in sorted(focus["cases"], key=lambda row: row["priority"])],
        "new_scientific_observations": 0, "new_comparable_keys": 0,
    })
    (out / "criterion_evidence_gap_receipt.json").write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--replays", required=True, nargs=2, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.workspace, args.replays, args.out)
    print(canonical_json({"status": result["status"], "S6-0": result["S6-0"], "grid": result["grid"]}))


if __name__ == "__main__":
    main()
