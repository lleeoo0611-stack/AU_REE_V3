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
    SCHEMA as SOURCE_SCHEMA, ContentStore, IntegrityError, _capture, _kind, _public_url, _run_requests, _write_manifest, fetch_public,
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
    priors = tuple(spec.get("prior_manifest_sha256", ()))
    if len(set(priors)) != len(priors) or any(not _HEX_SHA.fullmatch(digest) for digest in priors):
        raise ValueError("Prior acquisitions require unique exact manifest fingerprints")
    return profile, fixed, fixed_sha, spec["schema"], cases, priors


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


def _prior_sources(prior_inputs, expected, profile):
    recovered = {}
    identity = SOURCE_SCHEMA, profile.profile_id, profile.research_cutoff.isoformat(), profile.timezone.key
    for manifest_path, store in prior_inputs:
        body = Path(manifest_path).read_bytes()
        digest = sha256(body).hexdigest()
        if digest not in expected or digest in recovered:
            raise IntegrityError("Prior capture manifest is missing its exact acquisition pin")
        content = ContentStore(Path(store))
        content.read(digest)
        manifest = json.loads(body)
        if tuple(manifest[key] for key in ("schema", "profile_id", "cutoff", "timezone")) != identity:
            raise IntegrityError("Prior capture belongs to another research profile")
        recovered[digest] = body, manifest, content
    if recovered.keys() != set(expected):
        raise IntegrityError("All pinned prior capture manifests and stores are required")
    return recovered


def _restore_proofs(sources, digest, content, profile, by_sha):
    """Copy proof objects for fetched identical bytes while keeping actual capture clocks."""
    lineage = []
    visited = set()
    cutoff = profile.research_cutoff.isoformat()
    for manifest_digest, (_, manifest, store) in sorted(sources.items()):
        for edition in manifest["editions"]:
            if edition["sha256"] != digest:
                continue
            proofs = validate_edition_proofs(store, edition, profile.timezone)
            for proof in proofs:
                capture = proof.reference_sha256
                if proof.public_on > cutoff or capture in visited:
                    continue
                content.put(store.read(digest))
                receipt = json.loads(store.read(capture))
                if content.register_capture(receipt) != capture:
                    raise IntegrityError("Prior public capture changed while restoring exact bytes")
                by_sha[digest].append((capture, receipt))
                lineage.append({"manifest_sha256": manifest_digest, "source_sha256": digest,
                                "capture_sha256": capture, "public_on": proof.public_on})
                visited.add(capture)
    return sorted(lineage, key=lambda row: (row["source_sha256"], row["capture_sha256"]))


def _verify_restored_proofs(content, expanded, result, expected, profile):
    if not expected:
        return
    manifests = {digest: json.loads(content.read(digest)) for digest in expected}
    lineage = expanded["restored_prior_proofs"]
    if result["restored_prior_proofs"] != lineage or expanded["prior_manifest_sha256"] != list(expected):
        raise IntegrityError("Prior proof lineage disagrees across recovery receipts")
    used = {(edition["sha256"], proof["reference_sha256"])
            for edition in expanded["editions"] for proof in edition["temporal_evidence"]}
    local_captures = {edition["receipt_sha256"] for edition in expanded["editions"]}
    external = {(digest, capture) for digest, capture in used if capture not in local_captures}
    if external != {(row["source_sha256"], row["capture_sha256"]) for row in lineage}:
        raise IntegrityError("Restored proof lineage does not cover the external capture references")
    for row in lineage:
        digest, capture = row["source_sha256"], row["capture_sha256"]
        manifest = manifests[row["manifest_sha256"]]
        if not any(edition["sha256"] == digest and any(
            t["reference_sha256"] == capture and t["public_on"] == row["public_on"]
            for t in edition["temporal_evidence"]
        ) for edition in manifest["editions"]):
            raise IntegrityError("Restored public capture is absent from its pinned manifest")
        if (digest, capture) not in used or row["public_on"] > profile.research_cutoff.isoformat():
            raise IntegrityError("Restored proof lacks evidence of exact publication under the cutoff")


def recover_public_editions(*, profile_path, fixed_manifest, bindings, store, out, fetcher=fetch_public, prior_inputs=()):
    out, store = Path(out), Path(store)
    if (out.resolve().is_relative_to(store.resolve()) or store.resolve().is_relative_to(out.resolve())
            or any(path.resolve().is_relative_to(root.resolve())
                   for path in map(Path, (profile_path, fixed_manifest, bindings)) for root in (out, store))
            or out.exists() and any(out.iterdir()) or store.exists() and any(store.iterdir())):
        raise ValueError("Recovery requires empty, separate output and CAS directories outside the locked inputs")
    profile, fixed, fixed_sha, schema, cases, prior_pins = _read_inputs(profile_path, fixed_manifest, bindings)
    original_hashes = {row["sha256"] for row in fixed["editions"]}
    if any(case.get("expected_sha256") in original_hashes for case in cases):
        raise ValueError("Edition recovery cannot replace an edition in the fixed cohort")
    plans = _requests(cases)
    started = perf_counter()
    content = ContentStore(store)
    by_url, prior, by_sha = {}, defaultdict(list), defaultdict(list)
    events, editions = [], []
    prior_sources = _prior_sources(prior_inputs, prior_pins, profile)
    for body, _, _ in prior_sources.values():
        content.put(body)
    previous_matches, restored = set(), []
    responses = _run_requests(plans, "GET", 4, 24, 12 * 1024 * 1024, fetcher)
    for plan, response in zip(plans, responses, strict=True):
        if (prior_sources and response.status == "FETCHED" and response.http_status == 200
                and response.payload is not None and _public_url(plan["url"]) and _public_url(response.final_url)
                and _kind(plan["url"], plan["kind"], response.payload, response.response_mime)):
            digest = sha256(response.payload).hexdigest()
            if digest not in previous_matches:
                restored.extend(_restore_proofs(prior_sources, digest, content, profile, by_sha))
                previous_matches.add(digest)
        event, edition = _capture(plan, response, content, profile, prior, by_sha)
        by_url[plan["url"]] = event, edition
        events.append(event)
        if edition:
            if not validate_edition_proofs(content, edition, profile.timezone):
                raise ValueError("Captured edition has no immutable public proof")
            editions.append(edition)
    rows = [_assess(case, by_url, original_hashes) for case in cases]
    native = _native_route_bindings(rows) if schema == VERSION_SCHEMA else {}
    historical_context = {"prior_manifest_sha256": list(prior_pins), "restored_prior_proofs": restored} if prior_sources else {}
    _write_manifest(profile, tuple(row["project_id"] for row in fixed["projects"]), events, editions,
                    content, None, out, purpose=_PURPOSES[schema], base_manifest_sha256=fixed_sha,
                    **native, **historical_context)
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
        "cases": rows, **historical_context,
    }
    (out / "source_edition_recovery.json").write_text(canonical_json(summary) + "\n")
    return summary


def verify_recovered_editions(*, profile_path, fixed_manifest, bindings, store, out):
    """Reopen the acquisition manifest, route results, and exact proofs without network."""
    profile, fixed, fixed_sha, schema, cases, prior_pins = _read_inputs(profile_path, fixed_manifest, bindings)
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
    _verify_restored_proofs(content, expanded, result, prior_pins, profile)
    return result


def verify_public_version_context(result, store, reviewed):
    """Bind independent candidate assessments to the captured native source pages."""
    from au_ree_v3.development import bind_native_quote
    from au_ree_v3.documents import DocumentCache

    spec = json.loads(reviewed.read_bytes())
    if (spec["schema"] != "public-source-version-native-review-v1" or result["schema"] not in _PURPOSES
            or spec["expansion_manifest_sha256"] != result["expansion_manifest_sha256"]):
        raise IntegrityError("Native review must name the exact independent source expansion")
    cases = {case["case_id"]: case for case in result["cases"]}
    reviewed_cases = {item["case_id"] for item in spec["cases"]}
    if not reviewed_cases or len(reviewed_cases) != len(spec["cases"]) or not reviewed_cases <= cases.keys():
        raise IntegrityError("Native review must refer to distinct acquired source cases")
    records = []
    content, cache = ContentStore(store), DocumentCache()
    decoded_pages = 0
    acquisition = json.loads(content.read(result["expansion_manifest_sha256"]))
    source_keys = {edition["receipt_sha256"]: edition["source_key"] for edition in acquisition["editions"]}
    for assessment in spec["cases"]:
        case = cases[assessment["case_id"]]
        chosen = defaultdict(list)
        dispositions = set(assessment.get("capture_dispositions", ("NEW_PUBLIC_EDITION",)))
        if not dispositions or not dispositions <= {"NEW_PUBLIC_EDITION", "RECOVERED_EXACT", "SOURCE_AFTER_CUTOFF"}:
            raise IntegrityError("Unknown native review source disposition")
        for attempt in case["attempts"]:
            if attempt["disposition"] in dispositions:
                chosen[attempt["candidate_edition_sha256"]].append(attempt)
        if not chosen or set(assessment["source_editions"]) != chosen.keys() or not assessment["blockers"]:
            raise IntegrityError("Report assessment requires all requested exact editions and explicit science blockers")
        edition_records = []
        anchors = assessment["anchors"]
        pages = max(anchor["page"] for anchor in anchors)
        for digest, attempts in sorted(chosen.items()):
            document = cache.pdf(content.read(digest), digest, pages)
            decoded_pages += len(document.pages)
            primary = attempts[0]["capture_sha256"]
            evidence = [bind_native_quote(document, source_keys[primary], anchor["page"], anchor["quote"],
                                          anchor["role"]).to_dict() for anchor in anchors]
            edition_records.append({"sha256": digest, "primary_capture_sha256": primary,
                                    "capture_sha256s": sorted(row["capture_sha256"] for row in attempts),
                                    "anchors": evidence})
        context = {key: value for key, value in assessment.items()
                   if key not in {"source_editions", "anchors", "review_state"}}
        default_state = "CANDIDATE" if "SOURCE_AFTER_CUTOFF" in dispositions else "EVIDENCE_INSUFFICIENT"
        state = assessment.get("review_state", default_state)
        if state not in {"CANDIDATE", "EVIDENCE_INSUFFICIENT", "SCIENTIFIC_CONFLICT"}:
            raise IntegrityError("Native source review cannot authorize formal use")
        records.append({**context, "project_id": case["project_id"], "edition_records": edition_records,
                        "state": state, "formal_selection": False})
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
    parser.add_argument("--prior", nargs=2, action="append", type=Path, default=[], metavar=("MANIFEST", "CAS"))
    parser.add_argument("--native-review", type=Path, help="Pin separate native evidence assessment for offline replay")
    parser.add_argument("--review-output", type=Path, help="Write the read-only native evidence receipt here")
    args = vars(parser.parse_args())
    reviewed, assessment_out = args.pop("native_review"), args.pop("review_output")
    verify = args.pop("verify")
    prior_inputs = args.pop("prior")
    if prior_inputs and verify:
        parser.error("Offline verification reads the already restored immutable proof objects")
    if reviewed or assessment_out:
        if not (verify and reviewed and assessment_out and not assessment_out.exists()
                and not assessment_out.resolve().is_relative_to(args["store"].resolve())):
            parser.error("Native assessment requires offline verification and a separate new receipt path")
    operation = verify_recovered_editions if verify else recover_public_editions
    result = operation(**args) if verify else operation(**args, prior_inputs=prior_inputs)
    if reviewed:
        native = verify_public_version_context(result, args["store"], reviewed)
        assessment_out.write_text(canonical_json(native) + "\n")
    print(canonical_json({key: result[key] for key in (
        "status", "scope", "exact_requested_editions_recovered", "new_exact_source_versions",
        "requested_public_urls", "elapsed_seconds", "expansion_manifest_sha256",
    )}))


if __name__ == "__main__":
    main()
