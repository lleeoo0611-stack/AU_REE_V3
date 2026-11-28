"""Compare candidate retrieval under one fixed budget and sealed exact-source page panel."""

import argparse
from collections import Counter
from contextlib import closing
from hashlib import file_digest, sha256
import json
import math
from pathlib import Path
import re
import resource
import sqlite3
import sys
from time import perf_counter
from zoneinfo import ZoneInfo

from au_ree_v3.candidate_search import search_candidates
from au_ree_v3.documents import DocumentCache
from au_ree_v3.sources import ContentStore, validate_edition_proofs


def _measures(counts):
    tp, fp, fn = (counts[key] for key in ("TP", "FP", "FN"))
    return {**{key: counts[key] for key in ("TP", "FP", "FN", "TN")},
            "precision": tp / (tp + fp) if tp + fp else "UNMEASURED",
            "recall": tp / (tp + fn) if tp + fn else "UNMEASURED"}


def _scope_hits(case, result, limit):
    pages = {}
    for row in result["matches"]:
        if row["source_sha256"] == case["source_sha256"] and row["page"] is not None:
            pages.setdefault(row["page"], row)
    return dict(tuple(pages.items())[:limit])


def _page_scores(case, pages):
    expected = {row["page"] for row in case["positive_pages"]}
    observed = set(pages)
    total = len(observed | expected)
    if not (observed | expected) <= set(range(1, case["pdf_pages"] + 1)):
        raise ValueError("Physical page exceeds the annotation's decoded original")
    return Counter(TP=len(observed & expected), FP=len(observed - expected),
                   FN=len(expected - observed), TN=case["pdf_pages"] - total)


def _anchor_key(row):
    return row["species"], row["line"], row["column"], row["end_column"], row["token"]


def _anchored(case, hits, document):
    expected = {(row["page"], *_anchor_key(field)) for row in case["positive_pages"] for field in row["fields"]}
    anchors = set()
    for page, report in hits.items():
        for header in (report, *report["additional_headers"]):
            quote_line = header["line_start"]
            if document.locate(page, line=quote_line).text != header["original_text"]:
                raise ValueError("Header locator does not reproduce the original")
            for row in (*header["printed_unit_lines"], header["nearby_numeric_row"]):
                if document.locate(page, line=row["line"]).text != row["text"]:
                    raise ValueError("Nearby row does not reproduce the original")
            for field in header["oxide_header_anchors"]:
                if document.locate(page, line=field["line"], column=field["column"],
                                   end_column=field["end_column"]).text != field["token"]:
                    raise ValueError("Oxide header token does not reproduce the original")
                anchors.add((page, *_anchor_key(field)))
    return Counter(TP=len(expected & anchors), FP=len(anchors - expected), FN=len(expected - anchors))


def _measure(args, panel, strategy):
    start = perf_counter()
    first_cpu = resource.getrusage(resource.RUSAGE_SELF)
    rows = []
    for case in panel["cases"]:
        result = search_candidates(args.index, "S1", args.out / "private" / strategy / case["source_sha256"],
                                   project_id=case["project_id"], limit=200, strategy=strategy)
        if (result["source_manifest_sha256"] != panel["source_manifest_sha256"]
                or result["scientific_admissions"] != 0):
            raise ValueError("Search result does not bind the sealed input or exceeded its candidate purpose")
        if len(result["matches"]) == 200:
            raise ValueError("Project result reached its cap before the common page budget")
        rows.append((case, _scope_hits(case, result, panel["page_budget_per_source"])))
    wall = perf_counter() - start
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (usage.ru_utime + usage.ru_stime) - (first_cpu.ru_utime + first_cpu.ru_stime)
    costs = {"wall_seconds": wall, "cpu_seconds": cpu, "peak_process_rss_kib": usage.ru_maxrss}

    pages, fields = Counter(), Counter()
    cache = DocumentCache()
    store = ContentStore(args.store)
    for case, hits in rows:
        pages.update(_page_scores(case, hits))
        if strategy == "oxide-table":
            digest = case["source_sha256"]
            document = cache.pdf(store.read(digest), digest, last_page=max(hits)) if hits else None
            fields.update(_anchored(case, hits, document))
    return {
        "page_metrics": _measures(pages),
        "printed_component_anchor_metrics": (
            {**_measures(fields), "TN": "UNMEASURED"} if strategy == "oxide-table" else "UNMEASURED"),
        "query_cost": costs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("index", "store", "panel", "out"):
        parser.add_argument(f"--{field}", required=True, type=Path)
    parser.add_argument("--panel-sha256", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--strategy", choices=("bm25", "oxide-table"), required=True)
    args = parser.parse_args()
    data = args.panel.read_bytes()
    if sha256(data).hexdigest() != args.panel_sha256:
        raise ValueError("Pre-frozen annotation SHA differs")
    panel = json.loads(data)
    result = _measure(args, panel, args.strategy)
    receipt = {
        "schema": "oxide-table-retrieval-panel-evaluation-v1",
        "status": "PASS",
        "code_commit": args.code_commit,
        "strategy": args.strategy,
        "annotation_sha256": args.panel_sha256,
        "source_manifest_sha256": panel["source_manifest_sha256"],
        "page_budget_per_source": panel["page_budget_per_source"],
        "report_families": len(panel["cases"]),
        "physical_pages": sum(item["pdf_pages"] for item in panel["cases"]),
        "formal_admission": "UNMEASURED",
        **result,
    }
    with (args.out / f"{args.strategy}_metrics.json").open("x") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")


_NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9])\d+(?:[,.]\d+)*(?![A-Za-z0-9])")
_RESOURCE_HEADER = re.compile(r"\b(?:mineral resources?|resource estimate|MRE)\b", re.I)
_REE_HEADER = re.compile(r"\b(?:TREO|REO|REE|rare earths?|Nd2O3)\b", re.I)
_EXCLUDED_HEADER = re.compile(r"\b(?:ore reserves?|exploration targets?|drill(?:ing)? intercepts?|rock chips?|scandium)\b", re.I)
_RESOURCE_FIELDS = {"ore_mt", "grade", "contained_treo_kt"}


def _checked_json(path, pin):
    payload = Path(path).read_bytes()
    if sha256(payload).hexdigest() != pin:
        raise ValueError("Frozen input SHA differs for " + Path(path).name)
    return json.loads(payload)


def _fold(text):
    return " ".join(text.split())


def _insert_row(db, split, origin, family, issuer, project, source, page, line, label, context, row):
    if type(label) is not bool:
        raise ValueError("An original row requires an explicit reviewed label")
    try:
        db.execute("INSERT INTO nlp_rows VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (split, origin, family, issuer, project, source, page, line, label, context, row))
    except sqlite3.IntegrityError as error:
        raise ValueError("An exact original PDF row appears more than once in the labelled corpus") from error


def _archive_training(db, manifest, bindings, cached, assets, manifest_sha, bound_sha):
    from au_ree_v3.resource_context import bind_context
    from au_ree_v3.resource_sources import ResourceSourceReader, plan_source_editions

    joined = db.execute("""SELECT r.payload,a.payload,v.payload,t.payload FROM raw_observations r
        JOIN anchors a USING(anchor_id) JOIN candidates c USING(raw_record_id) JOIN validations v USING(candidate_id)
        JOIN validation_anchors e ON e.validation_id=v.validation_id AND e.anchor_id=a.anchor_id
        JOIN temporal_decisions t ON t.validation_id=v.validation_id AND t.raw_record_id=r.raw_record_id""")
    originals = {raw["observation_id"]: (raw, anchor, val, time) for raw, anchor, val, time in
                 (tuple(map(json.loads, row)) for row in joined)}
    plan = plan_source_editions(bindings, manifest, manifest_sha)
    reader = ResourceSourceReader(assets, plan, cached)
    rebound = set()
    used_rows = set()
    nonunique = set()
    for binding, edition, project in plan.matches:
        case, capture = reader.read(binding, edition, project)
        if capture is None or case["proof_status"] != "EXACT_CAPTURE_PROOF":
            raise ValueError("Archived training source could not be read with exact capture proof")
        statements = bind_context(binding, project, capture, case, cutoff=plan.cutoff, rules_sha=bound_sha)
        for cell in (cell for statement in statements for cell in statement.measurements):
            identity = cell.raw.observation_id
            raw, anchor, validation, decision = originals[identity]
            if (anchor["quote"], anchor["page"], raw["raw_value"], raw["raw_unit"]) != (
                cell.anchor.quote, cell.anchor.page, cell.raw.raw_value, cell.raw.raw_unit,
            ):
                raise ValueError("Archived observation or anchor differs from its original binding")
            rebound.add(identity)
            if (raw["variable"] not in _RESOURCE_FIELDS or validation["outcome"] != "VALIDATED"
                    or decision["status"] != "ELIGIBLE" or not anchor["table"] or anchor["table"] == "Narrative"):
                continue
            page, source = anchor["page"], capture.source.sha256
            lines = capture.pdf.pages[page-1].splitlines()
            matches = [i for i, line in enumerate(lines, 1) if _fold(line) == _fold(anchor["quote"])]
            if len(matches) != 1:
                nonunique.add((source, page, anchor["quote"]))
                continue
            line, = matches
            if (source, page, line) in used_rows or len(_NUMERIC_TOKEN.findall(lines[line-1])) < 2:
                continue
            used_rows.add((source, page, line))
            issuer = _issuer(edition.get("publisher", None) or "")
            _insert_row(db, "train", "archive", binding["case_id"], issuer, project.entity_id, source, page, line, True,
                        "\n".join(lines[max(0, line-12):line-1]), lines[line-1])
    if rebound != originals.keys():
        raise ValueError("Archived observations did not all bind to the identical source records")
    return {
        "joined_anchor_records": len(originals), "exact_native_rebinds": len(rebound),
        "pdf_editions": len(reader.documents), "physical_pages": sum(len(pdf.pages) for pdf in reader.documents.values()),
        "nonunique_native_quotes": len(nonunique),
    }


def _issuer(name):
    name = " ".join(re.findall(r"[a-z0-9]+", name.casefold()))
    return "" if name in {"asx", "australian securities exchange", "london stock exchange"} else name


def _row_panel(db, panel, split, origin, captures, cas, cache, timezone, documents):
    for item in panel["cases"]:
        edition = captures[item.get("capture_receipt_sha256") or item["capture_sha256"]]
        project, source = item["project_id"], item["source_sha256"]
        if ((edition["sha256"], edition["edition_id"], edition["kind"]) != (source, item["edition_id"], "pdf")
                or project not in edition["project_ids"] or edition["temporal_decision"]["status"] != "ELIGIBLE"
                or not validate_edition_proofs(cas, edition, timezone)):
            raise ValueError("Original annotation capture differs from verified edition/Project")
        oxide_panel = origin == "assay"
        probes = item["positive_pages"] if oxide_panel else item["probes"]
        if not probes:
            continue
        page_key = "page" if oxide_panel else "physical_page"
        view = cache.pdf(cas.read(source), source, max(probe[page_key] for probe in probes))
        documents[source] = view
        for probe in probes:
            page = probe[page_key]
            if oxide_panel:
                header, line = probe["header_line"], probe["example_row_line"]
                original = view.locate(page, line=line).text
                if original != probe["example_row_text"] or view.locate(page, line=header).text != probe["header_text"]:
                    raise ValueError("Original assay annotation differs from its native locator")
                context = "\n".join(view.pages[page-1].splitlines()[max(0, header-5):line-1])
                label = False
            else:
                row = probe["row"]
                line, original = row["line"], row["positioned_text"]
                anchors = [probe["caption"], *probe["headers"]]
                if any(view.locate(page, line=a["line"]).text != a["positioned_text"] for a in [row, *anchors]):
                    raise ValueError("Original resource row annotation differs from its native locator")
                context = "\n".join(a["positioned_text"] for a in anchors)
                label = probe["target_resource_row"]
            family = item.get("report_family") or item["family"]
            issuer = _issuer(item.get("issuer") or family.split("/")[0])
            _insert_row(db, split, origin, family, issuer, project, source, page, line, label, context, original)


def _features(row):
    return _NUMERIC_TOKEN.sub(" NUM ", _fold(row["context"]) + " | " + _fold(row["row_text"])).casefold()


def _ngram_counts(text):
    return Counter(text[start:start+size] for size in (3, 4, 5) for start in range(len(text)-size+1))


def _sigmoid(value):
    return 1 / (1 + math.exp(-value))


def _learn_probabilities(training, held):
    """Fit deterministic sparse L2 logistic regression using only training text."""
    train_counts = [_ngram_counts(_features(row)) for row in training]
    document_counts = Counter(feature for counts in train_counts for feature in counts)
    n = len(train_counts)
    idf = {feature: 1 + math.log((1 + n) / (1 + count)) for feature, count in document_counts.items() if count >= 2}
    targets = Counter(row["label"] for row in training)
    if set(targets) != {0, 1} or not idf:
        raise ValueError("Training requires reviewed positive and negative examples and original vocabulary")

    def encode(counts):
        vector = {feature: (1 + math.log(counts[feature])) * idf[feature] for feature in sorted(counts.keys() & idf.keys())}
        norm = math.sqrt(sum(value * value for value in vector.values()))
        return {feature: value / norm for feature, value in vector.items()}

    vectors = [(encode(counts), row["label"]) for counts, row in zip(train_counts, training, strict=True)]
    weights = dict.fromkeys(idf, 0.0)
    bias = 0.0
    step = 1 / (1 + n / 2)
    for _ in range(150):
        gradient = weights.copy()
        offset = bias
        for vector, label in vectors:
            prediction = _sigmoid(bias + sum(weights[key] * val for key, val in vector.items()))
            error = (prediction - label) * n / (2 * targets[label])
            offset += error
            for key, val in vector.items():
                gradient[key] += error * val
        weights = {key: weights[key] - step * delta for key, delta in gradient.items()}
        bias -= step * offset
    held_vectors = (encode(_ngram_counts(_features(row))) for row in held)
    return [_sigmoid(bias + sum(weights[key] * val for key, val in vector.items())) for vector in held_vectors], len(idf)


def _classification_metrics(rows, predictions):
    counts = Counter()
    for row, label in zip(rows, predictions, strict=True):
        counts[("T" if row["label"] == label else "F") + ("P" if label else "N")] += 1
    tp, fp, fn = (counts[key] for key in ("TP", "FP", "FN"))
    return {**_measures(counts), "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else "UNMEASURED"}


def _keyword_row(row):
    context = row["context"]
    return bool(_RESOURCE_HEADER.search(context) and _REE_HEADER.search(context) and not _EXCLUDED_HEADER.search(context)
                and len(_NUMERIC_TOKEN.findall(row["row_text"])) >= 2)


def _dataset(args, db):
    cache = DocumentCache()
    manifest = _checked_json(*args.manifest)
    bindings = _checked_json(*args.bindings)
    proof = _archive_training(db, manifest, bindings, cache, args.store, args.manifest[1], args.bindings[1])
    captures = {row["receipt_sha256"]: row for row in manifest["editions"]}
    cas, timezone, documents = ContentStore(args.store), ZoneInfo(manifest["timezone"]), {}
    panels = [("train", "annotations", args.train_rows), ("train", "assay", args.assay_negatives)]
    panels.extend(("held", f"holdout_{i}", item) for i, item in enumerate(args.holdout, 1))
    for split, origin, reference in panels:
        panel = _checked_json(*reference)
        _row_panel(db, panel, split, origin, captures, cas, cache, timezone, documents)
    query = "SELECT * FROM nlp_rows ORDER BY split,origin,family,source,page,line"
    baseline = db.execute(query).fetchall()
    if args.recovered_sqlite:
        proof["recovered_training"] = _recovered_training(db, baseline, args.recovered_sqlite, args.recovery_registry)
    return db.execute(query).fetchall(), proof, documents, baseline


def _recovery_row(payload, evidence, row_source, project_metadata):
    from au_ree_v3.native_table_work import COHORT_RECOVERY_SCHEMA

    raw = json.loads(payload)
    cell = json.loads(raw["original_payload"])
    anchor = json.loads(evidence)
    if raw["status"] != "CANDIDATE" or raw["transform_version"] != COHORT_RECOVERY_SCHEMA:
        raise ValueError("Recovery SQLite contains a different observation contract")
    if not cell["legacy_hint_ids"] or cell["variable"] not in _RESOURCE_FIELDS:
        return None
    if (cell["source_sha256"], cell["value_anchor"]["text"], cell["physical_page"], cell["value_anchor"]["locator"]) != (
        row_source, raw["raw_value"], anchor["page"], anchor["locator"]
    ):
        raise ValueError("Recovered observation differs from its original anchor")
    location = re.fullmatch(r"PDF page (\d+), line (\d+)", cell["row_anchor"]["locator"])
    if not location or int(location[1]) != cell["physical_page"]:
        raise ValueError("Recovered row has no exact native locator")
    issuer = _issuer(project_metadata.get("developer") or "")
    context = "\n".join(dict.fromkeys(a["text"] for a in (*cell["caption_anchors"], cell["header_anchor"])))
    if not issuer or not _RESOURCE_HEADER.search(context) or _EXCLUDED_HEADER.search(context):
        return None
    return {"origin": "recovered_native", "family": f"{issuer}/recovered_resource",
            "issuer": issuer, "project": raw["project_id"], "source": row_source, "page": int(location[1]),
            "line": int(location[2]), "context": context, "row_text": cell["row_anchor"]["text"]}


def _recovered_training(db, baseline, reference, registry_ref):
    from au_ree_v3.store import APPLICATION_ID

    archive, pin = reference
    inventory = {row["project_id"]: row for row in _checked_json(*registry_ref)["projects"]}
    held = [row for row in baseline if row["split"] == "held"]
    keys = ("issuer", "project", "source")
    groups = {key: {row[key] for row in held if row[key]} for key in keys}
    held_features = {_features(row) for row in held}
    seen = {(r["source"], r["page"], r["line"]) for r in baseline}
    path = Path(archive).resolve()
    with path.open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != pin:
            raise ValueError("Recovered SQLite SHA differs")
    query = """SELECT r.payload,a.payload,r.source_sha,r.project_id FROM raw_observations r
               JOIN anchors a ON a.anchor_id=r.anchor_id AND a.source_sha=r.source_sha AND a.source_key=r.source_key
               JOIN verified_sources s ON s.sha256=r.source_sha AND s.source_key=r.source_key
               ORDER BY r.source_sha,r.raw_record_id"""
    counts = Counter()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as recovered:
        if recovered.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("Recovered data is not a V3 SQLite Store")
        for payload, anchor, sha, project in recovered.execute(query):
            counts["raw_candidates_read"] += 1
            candidate = _recovery_row(payload, anchor, sha, inventory[project])
            if candidate is None:
                counts["no_reviewed_resource_row_label"] += 1
                continue
            key = (candidate["source"], candidate["page"], candidate["line"])
            if key in seen:
                counts["original_row_already_counted"] += 1
                continue
            if any(candidate[group] in groups[group] for group in keys) or _features(candidate) in held_features:
                counts["held_group_or_text_overlap_excluded"] += 1
                continue
            _insert_row(db, "train", candidate["origin"], candidate["family"], candidate["issuer"], candidate["project"],
                        sha, candidate["page"], candidate["line"], True, candidate["context"], candidate["row_text"])
            seen.add(key)
            counts["included_training_rows"] += 1
    return {"sqlite_sha256": pin, "registry_sha256": registry_ref[1], "counts": dict(counts)}


def _grouped_report(args):
    from au_ree_v3.native_table_discovery import discover_page_rows

    begin = perf_counter()
    archive, pin = args.archive_sqlite
    archive = Path(archive).resolve()
    with archive.open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != pin:
            raise ValueError("Archived SQLite SHA differs")
    with closing(sqlite3.connect(archive.as_uri() + "?mode=ro&immutable=1", uri=True)) as disk, closing(
        sqlite3.connect(":memory:")
    ) as db:
        disk.backup(db)
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("""CREATE TEMP TABLE nlp_rows(split TEXT, origin TEXT, family TEXT, issuer TEXT, project TEXT, source TEXT,
                      page INTEGER, line INTEGER, label INTEGER, context TEXT, row_text TEXT, UNIQUE(source,page,line))""")
        db.row_factory = sqlite3.Row
        rows, proof, documents, baseline = _dataset(args, db)
    training, held = ([row for row in rows if row["split"] == phase] for phase in ("train", "held"))
    for key in ("family", "issuer", "project", "source"):
        if ({row[key] for row in training} - {""}) & ({row[key] for row in held} - {""}):
            raise ValueError("Train/holdout leakage by " + key)
    if not training or {row["label"] for row in held} != {0, 1} or {_features(r) for r in training} & {_features(r) for r in held}:
        raise ValueError("Missing reviewed positive/negative rows or duplicate model inputs across the split")
    loaded = perf_counter()
    probabilities, vocabulary = _learn_probabilities(training, held)
    fitted = perf_counter()
    comparison = None
    if args.recovered_sqlite:
        original = [r for r in baseline if r["split"] == "train"]
        baseline_start = perf_counter()
        old_predictions, _ = _learn_probabilities(original, held)
        comparison = {"training_rows": len(original),
                      "model": _classification_metrics(held, [p >= .5 for p in old_predictions]),
                      "fit_predict_seconds": perf_counter() - baseline_start}
    locator = {(source, page): {entry.row_line for entry in discover_page_rows(documents[source], page=page)}
               for source, page in {(r["source"], r["page"]) for r in held}}
    metrics = {}
    for cohort in sorted({row["origin"] for row in held}) + ["all"]:
        panel = [(r, p) for r, p in zip(held, probabilities, strict=True) if cohort == "all" or r["origin"] == cohort]
        records, scores = zip(*panel, strict=True)
        metrics[cohort] = {
            "rows": len(records), "groups": len({r["family"] for r in records}),
            "model": _classification_metrics(records, [p >= .5 for p in scores]),
            "lexical_rule": _classification_metrics(records, [_keyword_row(r) for r in records]),
            "automatic_native_locator": _classification_metrics(
                records, [r["line"] in locator[r["source"], r["page"]] for r in records]),
            "model_log_loss": -sum(math.log(p if r["label"] else 1 - p)
                                   for r, p in panel) / len(panel),
        }
    model, base = (metrics["all"][key] for key in ("model", "lexical_rule"))
    qualifies = all(isinstance(model[key], float) and isinstance(base[key], float) and model[key] >= base[key]
                    for key in ("precision", "recall"))
    return {
        "schema": "grouped-resource-row-nlp-evaluation-v1", "status": "PASS", "code_commit": args.code_commit,
        "input_sha256": {"source_manifest": args.manifest[1], "sqlite": pin, "bindings": args.bindings[1],
                         "training_panels": [args.train_rows[1], args.assay_negatives[1]],
                         "holdout_panels": [row[1] for row in args.holdout]},
        "training": {"rows": len(training), "families": len({r["family"] for r in training}),
                     "positive": sum(row["label"] for row in training), "by_origin": dict(Counter(r["origin"] for r in training))},
        "evidence": proof,
        "split_checks": ["report_family", "known_issuer", "project", "exact_source_sha", "normalized_exact_feature_text"],
        "dataset_sha256": sha256(json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        "model": {"method": "stdlib character TF-IDF (3–5) class-balanced L2 logistic regression", "vocabulary": vocabulary,
                  "iterations": 150, "threshold": 0.5},
        "holdout": metrics, "formal_admission": "UNMEASURED", "scientific_admissions": 0,
        **({"without_recovered_sqlite": comparison} if comparison else {}),
        "candidate_use_gate": {"status": "PASS" if qualifies else "FAIL",
                               "reason": None if qualifies else "HYPOTHESIS_NOT_SUPPORTED",
                               "criterion": "Exact original and archive proof, holdout precision and recall at least the lexical baseline"},
        "scope": "Classification on located review rows. Automatic locator is reported separately. No model/row write or formal release use.",
        "timing_seconds": {"read_and_verify": loaded-begin, "fit_and_predict": fitted-loaded, "total": perf_counter()-begin},
    }


def grouped_nlp(argv):
    parser = argparse.ArgumentParser(description="Evaluate a memory-only resource-row classifier on frozen report families.")
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--code-commit", required=True)
    for argument in ("manifest", "bindings", "archive-sqlite", "train-rows", "assay-negatives"):
        parser.add_argument(f"--{argument}", required=True, nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--holdout", required=True, action="append", nargs=2, metavar=("PATH", "SHA256"))
    for argument in ("recovered-sqlite", "recovery-registry"):
        parser.add_argument(f"--{argument}", nargs=2, metavar=("PATH", "SHA256"))
    args = parser.parse_args(argv)
    if bool(args.recovered_sqlite) != bool(args.recovery_registry):
        parser.error("The recovered SQLite and its exact registry are required together")
    print(json.dumps(_grouped_report(args), sort_keys=True, indent=2))


if __name__ == "__main__":
    if sys.argv[1:2] == ["grouped-nlp"]:
        grouped_nlp(sys.argv[2:])
    else:
        main()
