"""Independently replay the Census display package joins and original PDF quotes."""

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
from unicodedata import normalize
from urllib.parse import urlsplit

from verify_display_research_equivalence import FIXED_SOURCE, read, read_census, require, sha_file, stable_hash

FILES = {"census_display.json", "census_display_extended.csv", "census_display_facts.jsonl"}
RECEIPT = "display_package_receipt.json"
PAGE = re.compile(r"\bpage\s+(\d+)\b", re.I)


def source_index(path, cas, name):
    return {row["receipt_sha256"]: (row, cas, name) for row in read(path)["editions"]}


def one_capture(fact, sources, *, review=False):
    key = fact["source_capture_sha256"]
    require(key in sources, "A display record has no pinned source capture")
    edition, store, universe = sources[key]
    require(fact["source_sha256"] == edition["sha256"] and fact["project_id"] in edition["project_ids"],
            "A display record changed edition or Project")
    if fact["source_url"] not in (edition["url"], edition["final_url"]):
        capture_url = fact["context"].get("source_capture_url")
        require(review and capture_url in (edition["url"], edition["final_url"]), "A display source URL changed")
        expected, actual = urlsplit(capture_url), urlsplit(fact["source_url"])
        require((actual.scheme, actual.netloc, actual.path, actual.query, actual.fragment)
                == (expected.scheme, expected.netloc, expected.path.removesuffix("/query"), "", ""),
                "Public source endpoint does not identify the captured official service")
    require(edition["temporal_decision"]["status"] == "ELIGIBLE", "Ineligible source capture in display evidence")
    return edition, store, universe


def words(text):
    return " ".join(normalize("NFKC", text).split())


def native_quote_checks(facts, sources):
    from au_ree_v3.documents import DocumentCache

    listed = defaultdict(list)
    for identity, fact in facts.items():
        edition, store, universe = one_capture(fact, sources)
        require(universe == "FIXED_144", "Expanded official evidence cannot enter fixed display facts")
        require(fact["context"]["as_reported_at"] == fact["publication_date"], "Display report date differs")
        require(fact["context"].get("temporal_status", "ELIGIBLE") == "ELIGIBLE", "Ineligible display assertion")
        require(edition["kind"] == "pdf", "This audit only approves fixed issuer PDF display facts")
        match = PAGE.search(fact["locator"])
        require(match, f"Display fact does not identify an exact physical page: {identity}")
        page = int(match[1])
        recorded_page = fact["context"].get("physical_page")
        require(recorded_page is None or int(recorded_page) == page, f"Display evidence page conflict: {identity}")
        listed[edition["sha256"], store].append((identity, page, fact))
    cache = DocumentCache()
    for (digest, store), matches in listed.items():
        filename = store / "objects" / digest
        require(sha_file(filename) == digest, "Missing or changed fixed PDF")
        document = cache.pdf(filename.read_bytes(), digest, last_page=max(page for _, page, _ in matches))
        for identity, page, fact in matches:
            original = document.pages[page - 1]
            context = fact["context"]
            if context.get("view") == "NATIVE_PDF_LAYOUT_REGION":
                first, last, column, start, end = (int(context[key]) for key in (
                    "start_line", "end_line", "start_column", "start", "end",
                ))
                lines = original.splitlines()
                require(1 <= first <= last <= len(lines) and column > 0, "Invalid display locator region")
                folded = " ".join(" ".join(line[column - 1:] for line in lines[first - 1:last]).split())
                original = folded[start:end]
            require(words(fact["quote"]) in words(original),
                    f"Display quote does not replay on the archived source page: {identity}")
            require(words(fact["raw_token"]) in words(fact["quote"]),
                    f"Display token is absent from its linked original quote: {identity}")
    return len(listed)


def check_review(rows, index):
    expanded = Counter()
    for fact in rows:
        edition, store, universe = one_capture(fact, index, review=True)
        require(fact["state"] in {"CANDIDATE", "SHADOW", "SCIENTIFIC_CONFLICT", "EVIDENCE_INSUFFICIENT"},
                "Review-only evidence has an approved role")
        require(sha_file(store / "objects" / edition["sha256"]) == edition["sha256"], "Review original mismatch")
        require(sha_file(store / "objects" / edition["receipt_sha256"]) == edition["receipt_sha256"], "Review receipt mismatch")
        source = store / "objects" / edition["sha256"]
        if universe == "EXPANDED_REVIEW_ONLY":
            _, _, locator = fact["locator"].partition(": ")
            key, _, value = locator.partition("=")
            features = read(source)["features"]
            matches = []
            for feature in features:
                properties = feature.get("properties", feature.get("attributes", {}))
                native = {name.casefold(): item for name, item in properties.items()}
                if str(native.get(key.casefold())) == value:
                    matches.append(properties)
            require(len(matches) == 1 and fact["quote"] in matches[0].values(),
                    "Official review locator or reported feature name differs from the original")
        else:
            from au_ree_v3.documents import DocumentCache

            page = PAGE.search(fact["locator"])
            require(edition["kind"] == "pdf" and page, "Fixed review quote cannot be reopened")
            ordinal = int(page[1])
            document = DocumentCache().pdf(source.read_bytes(), edition["sha256"], last_page=ordinal)
            require(words(fact["quote"]) in words(document.pages[ordinal - 1]), "Fixed review quote differs")
        expanded[universe] += 1
    return dict(expanded)


def read_fact_rows(path):
    facts = {}
    for encoded in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(encoded)
        identity = row.pop("fact_id")
        require(stable_hash(row) == identity and identity not in facts, "Display fact source identity changed or duplicated")
        facts[identity] = row
    return facts


def matrix_checks(document, csv_file, core_header, projects, facts):
    fields = document["columns"]
    ids = [field["field_id"] for field in fields]
    require(len(ids) == len(set(ids)) and not set(ids) & set(core_header), "Extension repeated a core column or field")
    require(all(field["label_zh"] not in core_header and field["purpose"] == "CENSUS_DISPLAY_ONLY"
                and field["display_label"] == "SOURCE_REPORTED_DISPLAY" and field["version"] == "v2" for field in fields),
            "Display field promotes or redefines a frozen 51-column value")
    require([row["id"] for row in document["projects"]] == projects, "Display changed the immutable 93 Project ordering")
    require(all(len(row["cells"]) == len(fields) for row in document["projects"]), "Display lost a Project×field cell")
    with csv_file.open(encoding="utf-8", newline="") as stream:
        header, *values = list(csv.reader(stream))
    require(header == [core_header[0], *ids] and len(values) == len(projects), "Extended CSV layout differs")
    coverage = {field: {"present": 0, "unknown": 0, "not_applicable": 0} for field in ids}
    uses = Counter()
    for project, csv_row in zip(document["projects"], values, strict=True):
        require(csv_row[0] == project["id"] and len(csv_row) == len(fields) + 1, "Extended row has wrong identity or width")
        for cell, field, csv_value in zip(project["cells"], fields, csv_row[1:], strict=True):
            field_id = field["field_id"]
            if cell["state"] == "PRESENT":
                links = cell["fact_ids"]
                require(links and len(set(links)) == len(links), "Present cell lacks unique source records")
                matches = [facts[identity] for identity in links]
                require(all(row["project_id"] == project["id"] and row["field_id"] == field_id
                            and row["data_role"] in field["evidence_roles"] for row in matches),
                        "Display data borrowed from a different Project, quantity, or role")
                require(cell["value"] == csv_value == " | ".join(row["value"] for row in matches), "Display narrowed or changed reported text")
                coverage[field_id]["present"] += 1
                uses.update(links)
            else:
                require(cell["state"] == "UNKNOWN" and cell["value"] is None and not cell["fact_ids"] and not csv_value,
                        "Unverified evidence was written to a confirmed display cell")
                coverage[field_id]["unknown"] += 1
    require(coverage == document["coverage"], "Declared per-field coverage differs from independently counted cells")
    require(set(uses) == set(facts) and all(count == 1 for count in uses.values()), "Sidecar has hidden or duplicated data facts")
    return coverage


def audit(package, repeat, core_build, contract, manifest, cas, expanded_manifest=None, expanded_cas=None):
    require(package.resolve() != repeat.resolve(), "Sidecar must replay into a distinct directory")
    require(sha_file(manifest) == FIXED_SOURCE, "Fixed original manifest was replaced")
    receipt = read(package / RECEIPT)
    require(receipt["status"] == "PASS" and receipt["source_manifest_sha256"] == FIXED_SOURCE
            and receipt["source_editions"] == 144 and receipt["verified_source_captures"] == 148,
            "Display receipt does not pin the approved fixed source universe")
    require(receipt["build_receipt_sha256"] == sha_file(core_build / "build_receipt.json"), "Display package uses another research build")
    require(receipt["input_files"]["columns"] == sha_file(contract), "Display package uses another contract")
    require(set(receipt["files"]) == FILES, "Unexpected display artifact set")
    for name in FILES | {RECEIPT}:
        observed = sha_file(package / name)
        require(sha_file(repeat / name) == observed, f"Display cold replay bytes differ for {name}")
        if name in FILES:
            require(receipt["files"][name] == observed, f"Display receipt fingerprint differs for {name}")
    document = read(package / "census_display.json")
    contract_values = read(contract)
    require(document["schema_version"] == "web-census-display-v2" and document["release_id"] == receipt["release_id"],
            "Display document is not bound to its own candidate identifier")
    require(document["columns"] == contract_values["columns"]
            and document["contract_sha256"] == receipt["contract_sha256"] == stable_hash(contract_values),
            "Public display contract differs from the versioned definition")
    header, projects, _ = read_census(core_build)
    facts = read_fact_rows(package / "census_display_facts.jsonl")
    require(facts == document["facts"], "Public evidence and machine-readable extension facts differ")
    coverage = matrix_checks(document, package / "census_display_extended.csv", header, projects, facts)
    require(coverage == receipt["coverage"] and len(facts) == receipt["fact_records"], "Display receipt count differs")
    fixed = source_index(manifest, cas, "FIXED_144")
    if expanded_manifest is not None:
        require(expanded_cas is not None, "Expanded source verification requires the separate CAS")
        require(sha_file(expanded_manifest) == receipt["input_files"]["expanded_review_manifest"], "Review manifest differs")
        expanded = source_index(expanded_manifest, expanded_cas, "EXPANDED_REVIEW_ONLY")
        require(not set(fixed) & set(expanded), "Expanded captures overlap the frozen manifest")
    else:
        expanded = {}
    pdfs = native_quote_checks(facts, fixed)
    review = check_review(document["review_only"], {**fixed, **expanded})
    require(sum(review.values()) == receipt["review_only_records"], "Display review receipt count differs")
    unique_projects = {fact["project_id"] for fact in facts.values()}
    unique_subjects = {(fact["project_id"], fact["subject_id"], fact["source_sha256"]) for fact in facts.values()}
    require(len(unique_projects) == receipt["distinct_projects"] and len(unique_subjects) == receipt["distinct_original_subjects"]
            and pdfs == receipt["distinct_original_editions"], "Distinct original assertions differ from receipt")
    return {"status": "PASS", "release_id": receipt["release_id"], "extension_fields": len(coverage),
            "extension_denominator": 93 * len(coverage), "extension_present_cells": sum(item["present"] for item in coverage.values()),
            "extension_project_ids": sorted(unique_projects), "distinct_original_subjects": len(unique_subjects),
            "coverage": coverage, "source_roles": dict(Counter(fact["data_role"] for fact in facts.values())),
            "native_fact_quotes_replayed": len(facts), "native_source_pdfs_reopened": pdfs,
            "replayed_display_artifacts_including_receipt": 4, "nonfact_review_sources": review,
            "files_sha256": receipt["files"], "source_manifest_sha256": FIXED_SOURCE}


def verify_public_bundle(directory, core_build, package, release_id):
    manifest = read(directory / "manifest.json")
    require(manifest["release_id"] == release_id and manifest["release_status"] == "RESEARCH_CANDIDATE"
            and manifest["authorization_sha256"] is None, "Display preview would promote the candidate to formal")
    require(manifest["project_count"] == 93 and manifest["column_count"] == 51,
            "Public display modified the stable core grid")
    artifacts = manifest["artifacts"]
    require({"census_display.json", "census_display_extended.csv"} <= artifacts.keys(),
            "Public extension package is missing one of its assets")
    require("census_display_facts.jsonl" not in artifacts, "Machine archival facts leaked into the public bundle")
    for name, entry in artifacts.items():
        require(Path(name).name == name and entry["path"] == name, "Unsupported public artifact path")
        require(sha_file(directory / name) == entry["sha256"], f"Public artifact SHA differs for {name}")
    for name in {"census_display.json", "census_display_extended.csv"}:
        require((directory / name).read_bytes() == (package / name).read_bytes(), "Public extension changed verified bytes")
    require((directory / "census.csv").read_bytes() == (core_build / "census/census.csv").read_bytes(),
            "Display extension changed the core Census export")
    return {"status": "PASS", "manifest_sha256": sha_file(directory / "manifest.json"), "assets": len(artifacts)}


def verify_frozen_history(record, original):
    freeze = read(record)
    prior_release = freeze["stage3_history"]["release_id"]
    historical = original / "preview/data/releases" / prior_release
    require(sha_file(original / "archive/releases" / f"{prior_release}.json") == prior_release,
            "Original Stage 3 candidate archive was changed")
    for name, sha in freeze["historical_preview_payloads"].items():
        require(sha_file(historical / name) == sha, f"Prior immutable preview asset changed: {name}")
    for value, expected in freeze["protected_pointer_paths"].items():
        path = Path(value)
        current = sha_file(path) if path.is_file() else None
        require(current == expected, f"Previously frozen pointer changed: {path}")
    return {"status": "PASS", "prior_release_id": prior_release,
            "immutable_preview_assets": len(freeze["historical_preview_payloads"]),
            "protected_pointer_paths": len(freeze["protected_pointer_paths"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package", "repeat", "core-build", "contract", "manifest", "cas", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expanded-manifest", type=Path)
    parser.add_argument("--expanded-cas", type=Path)
    parser.add_argument("--public-bundle", type=Path)
    parser.add_argument("--history", type=Path, nargs=2)
    args = parser.parse_args()
    report = audit(args.package, args.repeat, args.core_build, args.contract, args.manifest, args.cas,
                   args.expanded_manifest, args.expanded_cas)
    if args.public_bundle:
        report["public_bundle"] = verify_public_bundle(args.public_bundle, args.core_build, args.package, report["release_id"])
    if args.history:
        report["immutable_history"] = verify_frozen_history(*args.history)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "out": str(args.out)}))


if __name__ == "__main__":
    main()
