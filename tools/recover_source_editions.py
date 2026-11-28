"""Attempt bounded public source-edition recovery in a separate CAS and manifest."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from time import perf_counter

from au_ree_v3.contracts import canonical_json
from au_ree_v3.contracts.temporal import evaluate_temporal
from au_ree_v3.registry import load_profile
from au_ree_v3.sources import (
    SCHEMA as SOURCE_SCHEMA, ContentStore, IntegrityError, _capture, _run_requests, _write_manifest, fetch_public,
    validate_edition_proofs,
)


SCHEMA = "source-edition-recovery-targets-v1"
VERSION_SCHEMA = "public-source-version-targets-v1"
_PURPOSES = {SCHEMA: "EXPANDED_MANIFEST_HISTORICAL_EDITION_RECOVERY",
             VERSION_SCHEMA: "EXPANDED_MANIFEST_INDEPENDENT_SOURCE_VERSIONS"}
_HEX_SHA = re.compile(r"[0-9a-f]{64}")


def _read_inputs(profile_path, fixed_manifest, bindings):
    profile = load_profile(profile_path)
    fixed_bytes = fixed_manifest.read_bytes()
    fixed_sha = sha256(fixed_bytes).hexdigest()
    fixed = json.loads(fixed_bytes)
    spec = json.loads(bindings.read_bytes())
    if (fixed["schema"] != SOURCE_SCHEMA or spec["schema"] not in _PURPOSES or spec["fixed_manifest_sha256"] != fixed_sha
            or len({edition["sha256"] for edition in fixed["editions"]}) != 144
            or (fixed["profile_id"], fixed["cutoff"], fixed["timezone"])
               != (profile.profile_id, profile.research_cutoff.isoformat(), profile.timezone.key)):
        raise ValueError("Recovery bindings require the exact fixed research cohort")
    cases = spec["cases"]
    project_ids = {row["project_id"] for row in fixed["projects"]}
    if (not cases or len({item["case_id"] for item in cases}) != len(cases)
            or any(case["project_id"] not in project_ids or not case["routes"] for case in cases)):
        raise ValueError("Invalid public source edition case")
    if spec["schema"] == SCHEMA and any(not _HEX_SHA.fullmatch(case.get("expected_sha256") or "") for case in cases):
        raise ValueError("Historical edition recovery requires an exact expected SHA")
    if spec["schema"] == VERSION_SCHEMA and any(
        "expected_sha256" in case or not case.get("native_namespace") or not case.get("native_id")
        or case.get("access_basis") != "PUBLIC_ANONYMOUS" for case in cases
    ):
        raise ValueError("New source versions require a separate public native identifier")
    return profile, fixed, fixed_sha, spec["schema"], cases


def _requests(cases):
    by_url = {}
    for case in cases:
        for route in case["routes"]:
            url = route["url"]
            group = by_url.setdefault(url, {
                "source_key": "public-edition-recovery-" + sha256(url.encode()).hexdigest()[:16],
                "project_ids": set(),
                "references": set(),
                "historical_sha256": set(),
                "publisher": route["publisher"],
                "url": url,
                "kind": case["kind"],
            })
            if group["kind"] != case["kind"] or group["publisher"] != route["publisher"]:
                raise ValueError("A recovery route must have one source media type and publisher")
            group["project_ids"].add(case["project_id"])
            group["references"].add(case["case_id"])
            if "expected_sha256" in case:
                group["historical_sha256"].add(case["expected_sha256"])
    return [dict(item, project_ids=sorted(item["project_ids"]), references=sorted(item["references"]),
                 historical_sha256=sorted(item["historical_sha256"]), source_keys=[item["source_key"]])
            for item in by_url.values()]


def _assess(case, by_url, original_hashes):
    attempts = []
    for route in case["routes"]:
        event, edition = by_url[route["url"]]
        expected = case.get("expected_sha256")
        temporal = edition["temporal_decision"] if edition else None
        if not edition:
            disposition = "SOURCE_UNAVAILABLE"
        elif expected and edition["sha256"] != expected:
            disposition = "DISTINCT_EDITION_ONLY"
        elif temporal["status"] == "ELIGIBLE":
            if expected:
                disposition = "RECOVERED_EXACT"
            else:
                disposition = "ALREADY_IN_FIXED_COHORT" if edition["sha256"] in original_hashes else "NEW_PUBLIC_EDITION"
        elif temporal["reason"] in {"PUBLICATION_AFTER_CUTOFF", "PRE_CUTOFF_PUBLIC_EVIDENCE_MISSING"}:
            disposition = "SOURCE_AFTER_CUTOFF"
        else:
            disposition = "EVIDENCE_INSUFFICIENT"
        attempts.append({
            "url": route["url"], "final_url": event["final_url"], "acquisition": event["status"],
            "http_status": event["http_status"], "reason": event.get("reason"), "mime": event["response_mime"],
            "response_bytes": event.get("bytes"), "acquired_at": event["acquired_at"],
            "candidate_edition_sha256": edition["sha256"] if edition else None,
            "capture_sha256": edition["receipt_sha256"] if edition else None,
            "temporal_decision": temporal, "disposition": disposition,
        })
    statuses = {attempt["disposition"] for attempt in attempts}
    verdict = next(state for state in (
        "RECOVERED_EXACT", "NEW_PUBLIC_EDITION", "ALREADY_IN_FIXED_COHORT", "SOURCE_AFTER_CUTOFF",
        "EVIDENCE_INSUFFICIENT", "SOURCE_UNAVAILABLE",
    ) if state in statuses or state == "SOURCE_UNAVAILABLE")
    return {key: case[key] for key in ("case_id", "project_id", "expected_sha256",
                                      "native_namespace", "native_id", "access_basis") if key in case} | {
        "result": verdict, "attempts": attempts, "formal_display_facts_added": 0,
    }


def _native_route_bindings(rows):
    return {"native_route_bindings": [
        {key: row[key] for key in ("case_id", "project_id", "native_namespace", "native_id", "access_basis")} | {
            "scope_status": "PROJECT_AND_STATEMENT_REVIEW_REQUIRED",
            "capture_sha256": attempt["capture_sha256"], "source_sha256": attempt["candidate_edition_sha256"],
        }
        for row in rows for attempt in row["attempts"] if attempt["capture_sha256"]
    ]}


def recover_public_editions(*, profile_path, fixed_manifest, bindings, store, out, fetcher=fetch_public):
    out, store = Path(out), Path(store)
    if (out.resolve().is_relative_to(store.resolve()) or store.resolve().is_relative_to(out.resolve())
            or any(path.resolve().is_relative_to(root.resolve())
                   for path in map(Path, (profile_path, fixed_manifest, bindings)) for root in (out, store))
            or out.exists() and any(out.iterdir()) or store.exists() and any(store.iterdir())):
        raise ValueError("Recovery requires empty, separate output and CAS directories outside the locked inputs")
    profile, fixed, fixed_sha, schema, cases = _read_inputs(profile_path, fixed_manifest, bindings)
    original_hashes = {row["sha256"] for row in fixed["editions"]}
    if any(case.get("expected_sha256") in original_hashes for case in cases):
        raise ValueError("Edition recovery cannot replace an edition in the fixed cohort")
    plans = _requests(cases)
    started = perf_counter()
    content = ContentStore(store)
    by_url, prior, by_sha = {}, defaultdict(list), defaultdict(list)
    events, editions = [], []
    responses = _run_requests(plans, "GET", 4, 24, 12 * 1024 * 1024, fetcher)
    for plan, response in zip(plans, responses, strict=True):
        event, edition = _capture(plan, response, content, profile, prior, by_sha)
        by_url[plan["url"]] = event, edition
        events.append(event)
        if edition:
            if not validate_edition_proofs(content, edition, profile.timezone):
                raise ValueError("Captured edition has no immutable public proof")
            editions.append(edition)
    rows = [_assess(case, by_url, original_hashes) for case in cases]
    native = _native_route_bindings(rows) if schema == VERSION_SCHEMA else {}
    _write_manifest(profile, tuple(row["project_id"] for row in fixed["projects"]), events, editions,
                    content, None, out, purpose=_PURPOSES[schema], base_manifest_sha256=fixed_sha, **native)
    summary = {
        "schema": schema, "scope": "EXPANDED_MANIFEST", "status": "PASS", "fixed_manifest_sha256": fixed_sha,
        "fixed_manifest_new_sources": 0, "formal_display_facts_added": 0, "formal_mcda_criteria_added": 0,
        "expansion_manifest_sha256": sha256((out / "source_manifest.json").read_bytes()).hexdigest(),
        "edition_bindings_sha256": sha256(Path(bindings).read_bytes()).hexdigest(),
        "elapsed_seconds": round(perf_counter() - started, 6), "requested_public_urls": len(plans),
        "capture_statuses": dict(Counter(row["status"] for row in events)),
        "new_exact_source_versions": len({row["sha256"] for row in editions} - original_hashes),
        "exact_requested_editions_recovered": sum(row["result"] == "RECOVERED_EXACT" for row in rows),
        "distinct_public_editions_observed": len({row["sha256"] for row in editions}),
        "cases": rows,
    }
    (out / "source_edition_recovery.json").write_text(canonical_json(summary) + "\n")
    return summary


def verify_recovered_editions(*, profile_path, fixed_manifest, bindings, store, out):
    """Reopen the acquisition manifest, route results, and exact proofs without network."""
    profile, fixed, fixed_sha, schema, cases = _read_inputs(profile_path, fixed_manifest, bindings)
    path = Path(out) / "source_manifest.json"
    manifest_bytes = path.read_bytes()
    digest = sha256(manifest_bytes).hexdigest()
    content = ContentStore(Path(store))
    content.read(digest)
    expanded = json.loads(manifest_bytes)
    result = json.loads((Path(out) / "source_edition_recovery.json").read_bytes())
    if (expanded.get("purpose") != _PURPOSES[schema] or expanded.get("base_manifest_sha256") != fixed_sha
            or result["schema"] != schema
            or result["expansion_manifest_sha256"] != digest or result["fixed_manifest_sha256"] != fixed_sha
            or result["edition_bindings_sha256"] != sha256(Path(bindings).read_bytes()).hexdigest()
            or any(expanded[key] != fixed[key] for key in ("schema", "profile_id", "cutoff", "timezone"))):
        raise IntegrityError("Recovery manifest, result, and fixed profile disagree")
    editions = {edition["receipt_sha256"]: edition for edition in expanded["editions"]}
    by_url = {}
    for event in expanded["events"]:
        url = event["requested_url"]
        edition = editions.pop(event.get("receipt_sha256"), None)
        if url in by_url or (event["status"] == "PUBLIC_CAPTURED") != (edition is not None):
            raise IntegrityError("Recovery route events are ambiguous")
        if edition:
            current = json.loads(content.read(edition["receipt_sha256"]))
            proofs = validate_edition_proofs(content, edition, profile.timezone)
            temporal = evaluate_temporal(
                source_sha256=edition["sha256"], cutoff=expanded["cutoff"], observation_role="DOCUMENT",
                acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proofs,
            )
            if (not proofs or current != {key: val for key, val in event.items() if key != "receipt_sha256"}
                    or canonical_json(temporal.to_dict()) != canonical_json(edition["temporal_decision"])):
                raise IntegrityError("Recovery route captured bytes or temporal proof disagrees")
        by_url[url] = event, edition
    original_hashes = {row["sha256"] for row in fixed["editions"]}
    rows = [_assess(case, by_url, original_hashes) for case in cases]
    native = _native_route_bindings(rows) if schema == VERSION_SCHEMA else {}
    if (editions or any(expanded.get(key) != value for key, value in native.items()) or set(by_url) != {plan["url"] for plan in _requests(cases)}
            or canonical_json(rows) != canonical_json(result["cases"])):
        raise IntegrityError("Recovery findings differ from their immutable capture journal")
    return result


def verify_public_version_context(result, store, reviewed):
    """Bind independent candidate assessments to the captured native source pages."""
    from au_ree_v3.development import bind_native_quote
    from au_ree_v3.documents import DocumentCache

    spec = json.loads(reviewed.read_bytes())
    if (spec["schema"] != "public-source-version-native-review-v1" or result["schema"] != VERSION_SCHEMA
            or spec["expansion_manifest_sha256"] != result["expansion_manifest_sha256"]):
        raise IntegrityError("Native review must name the exact independent source expansion")
    cases = {case["case_id"]: case for case in result["cases"]}
    if len(spec["cases"]) != len(cases) or {item["case_id"] for item in spec["cases"]} != cases.keys():
        raise IntegrityError("One native review is required per distinct source family")
    records = []
    content, cache = ContentStore(store), DocumentCache()
    decoded_pages = 0
    acquisition = json.loads(content.read(result["expansion_manifest_sha256"]))
    source_keys = {edition["receipt_sha256"]: edition["source_key"] for edition in acquisition["editions"]}
    for assessment in spec["cases"]:
        case = cases[assessment["case_id"]]
        eligible = defaultdict(list)
        for attempt in case["attempts"]:
            if attempt["disposition"] == "NEW_PUBLIC_EDITION":
                eligible[attempt["candidate_edition_sha256"]].append(attempt)
        if set(assessment["source_editions"]) != eligible.keys() or not assessment["blockers"]:
            raise IntegrityError("Report assessment requires all exact editions and explicit science blockers")
        edition_records = []
        anchors = assessment["anchors"]
        pages = max(anchor["page"] for anchor in anchors)
        for digest, attempts in sorted(eligible.items()):
            document = cache.pdf(content.read(digest), digest, pages)
            decoded_pages += len(document.pages)
            primary = attempts[0]["capture_sha256"]
            evidence = [bind_native_quote(document, source_keys[primary], anchor["page"], anchor["quote"],
                                          anchor["role"]).to_dict() for anchor in anchors]
            edition_records.append({"sha256": digest, "primary_capture_sha256": primary,
                                    "capture_sha256s": sorted(row["capture_sha256"] for row in attempts),
                                    "anchors": evidence})
        context = {key: value for key, value in assessment.items() if key not in {"source_editions", "anchors"}}
        records.append({**context, "project_id": case["project_id"], "edition_records": edition_records,
                        "state": "EVIDENCE_INSUFFICIENT", "formal_selection": False})
    return {
        "schema": spec["schema"], "status": "PASS", "scope": "EXPANDED_MANIFEST_CANDIDATE_ONLY",
        "expansion_manifest_sha256": spec["expansion_manifest_sha256"],
        "native_review_sha256": sha256(reviewed.read_bytes()).hexdigest(), "cases": records,
        "source_families_reviewed": len(records), "pdf_decodes": sum(len(case["edition_records"]) for case in records),
        "pdf_pages_decoded": decoded_pages, "new_independent_verified_observations": 0,
        "new_selected_scientific_values": 0, "new_comparable_project_criterion_keys": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("profile-path", "fixed-manifest", "bindings", "store", "out"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--verify", action="store_true", help="Verify the existing acquisition without network")
    parser.add_argument("--native-review", type=Path, help="Pin separate native evidence assessment for offline replay")
    parser.add_argument("--review-output", type=Path, help="Write the read-only native evidence receipt here")
    args = vars(parser.parse_args())
    reviewed, assessment_out = args.pop("native_review"), args.pop("review_output")
    verify = args.pop("verify")
    if reviewed or assessment_out:
        if not (verify and reviewed and assessment_out and not assessment_out.exists()
                and not assessment_out.resolve().is_relative_to(args["store"].resolve())):
            parser.error("Native assessment requires offline verification and a separate new receipt path")
    operation = verify_recovered_editions if verify else recover_public_editions
    result = operation(**args)
    if reviewed:
        native = verify_public_version_context(result, args["store"], reviewed)
        assessment_out.write_text(canonical_json(native) + "\n")
    print(canonical_json({key: result[key] for key in (
        "status", "scope", "exact_requested_editions_recovered", "new_exact_source_versions",
        "requested_public_urls", "elapsed_seconds", "expansion_manifest_sha256",
    )}))


if __name__ == "__main__":
    main()
