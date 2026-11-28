"""Compare automatic resource-table extraction using pinned originals and held annotations."""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import re
from resource import RUSAGE_CHILDREN, RUSAGE_SELF, getrusage
import subprocess
from time import perf_counter
from zoneinfo import ZoneInfo

from au_ree_v3.documents import DocumentCache, DocumentError
from au_ree_v3.migration.baseline import BASELINES, PinnedRepository
from au_ree_v3.native_table_discovery import discover_page_rows
from au_ree_v3.normalization import KNOWN_OXIDE_SPECIES
from au_ree_v3.observations import digest
from au_ree_v3.resource_table_binding import bind_page_row
from au_ree_v3.sources import ContentStore, IntegrityError, validate_edition_proofs

if __package__:
    from .historical_claim_rows import _NUMBER, _claim_hints, _fold, _marker_pattern, locate_claim_row
else:
    from historical_claim_rows import _NUMBER, _claim_hints, _fold, _marker_pattern, locate_claim_row


SCHEMA = "resource-table-structure-comparison-v1"
PACKAGES = {"pymupdf": "PyMuPDF", "pdfplumber": "pdfplumber", "table-transformer": "onnxruntime"}
OLD_FAILURE = "MARKERS_NOT_BOUND_TO_NUMERIC_TABLE_ROW"
NATIVE_ROLE = {
    "ore_mt": "ore_mt", "reported_resource_ore_mt": "ore_mt", "grade": "grade",
    "reported_resource_treo_ppm": "grade", "reported_resource_treo_grade_ppm": "grade",
    "reported_resource_treo_grade_pct": "grade", "reported_resource_grade_pct": "grade",
    "contained_treo_kt": "contained_treo_kt", "reported_resource_contained_kt": "contained_treo_kt",
    "reported_resource_treo_ex_ceo2_ppm": "grade_ex_ceo2",
}
OXIDES = re.compile(r"reported_(?:resource_)?(" + "|".join(sorted(species.casefold() for species in KNOWN_OXIDE_SPECIES)) + ")_ppm")
CLAIM_OXIDES = {f"reported_resource_{name}_ppm": f"{oxide}_ppm" for name, oxide in
                 (("pr", "pr6o11"), ("nd", "nd2o3"), ("tb", "tb4o7"), ("dy", "dy2o3"))}


@dataclass(frozen=True, slots=True)
class Prediction:
    role: str | None
    unit: str
    literal: str
    locator: str
    header: str | None
    bbox: tuple | None = None
    start_column: int | None = None
    chemistry: str | None = None
    header_bbox: tuple | None = None
    unit_witness: str | None = None
    unit_bbox: tuple | None = None

    def support(self):
        fields = ("role", "unit", "literal", "locator", "header", "bbox", "chemistry",
                  "header_bbox", "unit_witness", "unit_bbox")
        return {key: getattr(self, key) for key in fields if getattr(self, key) is not None}


@dataclass(frozen=True, slots=True)
class LocatedRow:
    text: str
    locator: str
    predictions: tuple[Prediction, ...]
    line: int | None = None
    bbox: tuple | None = None
    unbound: tuple[Prediction, ...] = ()
    printed_tokens: tuple[tuple[str, tuple], ...] = ()


def _pinned(path, expected):
    payload = Path(path).read_bytes()
    if sha256(payload).hexdigest() != expected:
        raise ValueError(f"SHA-256 differs for pinned {Path(path).name}")
    return json.loads(payload)


def _original_body(source, store, history):
    try:
        return store.read(source)
    except FileNotFoundError:
        path = history / source
        if not path.is_file():
            path = history / "assets" / source
        body = path.read_bytes()
        if sha256(body).hexdigest() != source:
            raise IntegrityError("Historical original SHA differs")
        return body


def _issuer(name):
    words = re.findall(r"[a-z0-9]+", (name or "").casefold())
    while words and words[-1] in {"limited", "ltd", "nl", "inc", "plc", "pty", "corp", "corporation"}:
        words.pop()
    normalized = " ".join(words)
    return normalized if normalized not in {"asx", "australian securities exchange", "london stock exchange"} else ""


def _source_partitions(claim_groups, holdouts, registry, manifest):
    """Freeze connected SHA, Project, family and known publisher groups before inference."""
    groups = defaultdict(set)
    keys = defaultdict(set)
    for source, items in claim_groups.items():
        for row, _ in items:
            project = row["project_id"]
            keys[source].update({("project", project), ("issuer", _issuer(registry[project].get("developer")))})
    for source, entries in holdouts.items():
        for item, _ in entries:
            keys[source].update({("project", item["project_id"]), ("issuer", _issuer(item.get("issuer"))),
                                 ("family", item.get("report_family") or item.get("family"))})
    for edition in manifest["editions"]:
        if edition["sha256"] in keys:
            keys[edition["sha256"]].add(("issuer", _issuer(edition.get("publisher"))))
    for source, categories in keys.items():
        for key in categories:
            if key[1]:
                groups[key].add(source)

    by_source = {}
    for source in sorted(keys):
        if source in by_source:
            continue
        connected, remaining = set(), {source}
        while remaining:
            current = remaining.pop()
            connected.add(current)
            for key in keys[current]:
                remaining.update(groups[key] - connected)
        identity = digest(["resource-table-structure-claims-v1", sorted(connected)])
        group = ("FIXED_HOLDOUT_EXCLUDED" if connected & holdouts.keys() else
                 "HISTORICAL_HELD" if int(identity[:2], 16) < 64 else "DEVELOPMENT")
        for member in connected:
            by_source[member] = {"group_sha256": identity, "partition": group}
    return by_source


def _probe_groups(inputs):
    groups = defaultdict(list)
    seen = set()
    for path, pin in inputs:
        panel = _pinned(path, pin)
        for case in panel["cases"]:
            for probe in case["probes"]:
                key = case["source_sha256"], probe["physical_page"], probe["row"]["line"]
                if key in seen:
                    raise ValueError("Repeated row across independently pinned panels")
                seen.add(key)
                groups[key[0]].append((case, probe))
    return groups


def _verify_holdout_captures(holdouts, manifest, store):
    editions = {edition["receipt_sha256"]: edition for edition in manifest["editions"]}
    timezone = ZoneInfo(manifest["timezone"])
    seen = set()
    for source, entries in holdouts.items():
        for item, _ in entries:
            pin = item.get("capture_receipt_sha256") or item["capture_sha256"]
            edition = editions[pin]
            if ((source, item["edition_id"], "pdf") != (edition["sha256"], edition["edition_id"], edition["kind"])
                    or item["project_id"] not in edition["project_ids"]
                    or edition["temporal_decision"]["status"] != "ELIGIBLE"):
                raise ValueError("Fixed holdout capture differs from exact source manifest")
            if pin not in seen and not validate_edition_proofs(store, edition, timezone):
                raise ValueError("Fixed holdout capture proof unavailable")
            seen.add(pin)


def _as_native(document, page, row, cells):
    predictions = tuple(
        Prediction(cell.variable, cell.unit, cell.value.text, cell.value.locator,
                   cell.header.text if cell.header else None, start_column=cell.value.start_column,
                   chemistry=cell.chemical_basis, unit_witness=cell.unit_label.text if cell.unit_label else None)
        for cell in cells if cell.variable and cell.unit and cell.value.physical_page == page)
    return LocatedRow(document.locate(page, line=row).text, f"PDF page {page}, line {row}", predictions, line=row)


def _poppler_rows(document, page):
    return tuple(_as_native(document, page, row.row_line, row.cells) for row in discover_page_rows(document, page=page))


def _coordinate_rows(reader, page):
    def measured(cell):
        return Prediction(cell.role, cell.unit, cell.text, cell.locator,
                          cell.header, tuple(cell.bbox), chemistry=cell.chemical_basis,
                          header_bbox=cell.header_bbox, unit_witness=cell.unit_text, unit_bbox=cell.unit_bbox)

    return tuple(LocatedRow(
        row.text, row.locator, tuple(measured(cell) for cell in row.cells if cell.role and cell.unit),
        bbox=tuple(row.bbox), unbound=tuple(measured(cell) for cell in row.cells if not cell.role and cell.unit and cell.header),
        printed_tokens=tuple((cell.text, tuple(cell.bbox)) for cell in row.cells),
    ) for row in reader.page(page))


def _annotation_row_matches(row, probe):
    if row.line is not None:
        return row.line == probe["row"]["line"]
    return _fold(row.text) == _fold(probe["row"]["positioned_text"])


def _verify_probe(document, probe):
    page, row = probe["physical_page"], probe["row"]
    for anchor in (row, probe["caption"], *probe["headers"]):
        if document.locate(page, line=anchor["line"]).text != anchor["positioned_text"]:
            raise ValueError("Frozen annotation text differs from exact original")
    for field in probe["expected_fields"]:
        span = document.locate(page, line=row["line"], column=field["start_column"], end_column=field["end_column"])
        if span.text != field["literal"]:
            raise ValueError("Frozen annotation number differs from exact original")


def _native_column(text, row, cell):
    """Crosswalk the exact printed occurrence, including duplicated literals."""
    if cell.start_column is not None:
        return cell.start_column
    positions = [match.start() + 1 for match in _NUMBER.finditer(text) if match[0] == cell.literal]
    if len(positions) == 1:
        return positions[0]
    boxes = sorted({bbox for literal, bbox in row.printed_tokens if literal == cell.literal})
    return positions[boxes.index(cell.bbox)] if len(positions) == len(boxes) and cell.bbox in boxes else None


def _probe_result(probe, rows, guided=()):
    original = probe["row"]["positioned_text"]
    matches = [row for row in rows if _annotation_row_matches(row, probe)]
    annotated = {(item["variable"], item["unit"], item["literal"], item["start_column"]): item
                 for item in probe["expected_fields"]}
    named = {key[0] for key in annotated}
    forbidden = {(item["variable"], item["literal"]) for item in probe["forbidden_bindings"]}

    def score(row):
        expected = set(annotated)
        seen = set()
        outside = ambiguous = 0
        if row:
            for cell in row.predictions:
                if probe["target_resource_row"] and cell.role not in named and (cell.role, cell.literal) not in forbidden:
                    outside += 1
                    continue
                position = _native_column(original, row, cell)
                if position is None:
                    ambiguous += 1
                    continue
                seen.add((cell.role, cell.unit, cell.literal, position))
        return {
            "TP": len(seen & expected), "FP": len(seen - expected), "FN": len(expected - seen),
            "unscored_fields": outside, "ambiguous_numeric_positions": ambiguous,
        }

    selected = matches[0] if len(matches) == 1 else None
    selected_guided = guided[0] if len(guided) == 1 else None
    return {
        "row_label": probe["target_resource_row"],
        "automatic_row_match": "UNIQUE" if selected else "AMBIGUOUS" if matches else "MISSING",
        "automatic": score(selected), "guided": score(selected_guided),
        "locator": selected.locator if selected else None,
    }


def _guided_poppler(document, probe):
    page = probe["physical_page"]
    line, caption = probe["row"]["line"], probe["caption"]["line"]
    try:
        fields = bind_page_row(document, page=page, row_line=line, caption_line=caption)
    except DocumentError:
        return ()
    return (_as_native(document, page, line, fields),)


def _same_row_matches(page_text, markers, token, rows):
    """Score source-only rows against the same historical witness criterion as Poppler."""
    page = _fold(page_text)
    markers = tuple(dict.fromkeys(_fold(marker) for marker in markers if marker.strip()))
    patterns = [(marker, _marker_pattern(marker)) for marker in markers]
    if not patterns or not all(pattern.search(page) for _, pattern in patterns):
        return (), "PAGE_MARKERS_NOT_FOUND"
    numeric = [pattern for marker, pattern in patterns if _NUMBER.search(marker)]
    if not numeric:
        return (), "NUMERIC_ROW_MARKER_MISSING"
    token_pattern = _marker_pattern(_fold(token)) if token else None
    matches = []
    for row in rows:
        folded = _fold(row.text)
        witnesses = [pattern for pattern in numeric if pattern.search(folded)]
        specific = any(len(_NUMBER.findall(marker)) >= 2 and pattern.search(folded) for marker, pattern in patterns)
        if (specific or len(witnesses) >= 2) and (token_pattern is None or token_pattern.search(folded)):
            matches.append(row)
    if not matches:
        return (), OLD_FAILURE
    if len(matches) != 1:
        return (), "AMBIGUOUS_MARKED_TABLE_ROW"
    return tuple(matches), None


def _claim_field(claim):
    variable = claim["variable"]
    match = OXIDES.fullmatch(variable)
    expected = NATIVE_ROLE.get(variable) or CLAIM_OXIDES.get(variable) or (match[1] + "_ppm" if match else None)
    binding = claim.get("cell_binding") or {}
    unit = claim.get("original_unit") or binding.get("original_unit")
    token = claim.get("original_token")
    if (claim.get("transform") or claim.get("normalization") or claim.get("species_conversion")
            or binding.get("mode") != "native_cell"):
        return None
    if expected and expected.endswith("_ppm") and (claim.get("basis") or "").casefold() != expected.removesuffix("_ppm"):
        return None
    if expected and unit and isinstance(token, str) and _NUMBER.fullmatch(token):
        return expected, unit.strip(), token, binding
    return None


def _matching_cells(row, field):
    if field is None:
        return {"status": "UNSCORABLE_NO_NATIVE_ROLE_UNIT_TOKEN"}
    role, unit, token, binding = field
    matches = [prediction for prediction in (*row.predictions, *row.unbound) if prediction.literal == token]
    if not matches:
        return {"status": "NO_TYPED_LITERAL"}
    if box := binding.get("column_bbox"):
        matches = [cell for cell in matches if cell.bbox is None or box[0] <= (cell.bbox[0] + cell.bbox[2]) / 2 <= box[2]]
    if not matches:
        return {"status": "ORIGINAL_COLUMN_NOT_REPRODUCED"}
    if len(matches) != 1 or (matches[0].bbox is None and
                             sum(match[0] == token for match in _NUMBER.finditer(row.text)) > 1):
        return {"status": "AMBIGUOUS_NUMERIC_COLUMN"}
    cell = matches[0]
    if cell.role is None:
        return {"status": "HEADER_WITHOUT_BOUND_ROLE", "cell": cell.support()}
    if (role, unit) != (cell.role, cell.unit):
        return {"status": "TYPED_ROLE_OR_UNIT_DIFFERS", "cell": cell.support()}
    return {"status": "EXACT_ROLE_UNIT_LITERAL", "cell": cell.support()}


def _claim_result(document, method, row, claim, automatic, structural):
    page = row["physical_page"]
    native = document.locate(page).text
    token, markers = claim.get("original_token"), claim["page_markers"]
    result = {}
    if method == "poppler":
        guided, reason = locate_claim_row(native, markers, token)
        if guided:
            result["guided_row_locator"] = f"PDF page {page}, line {guided}"
    else:
        guided, reason = _same_row_matches(native, markers, token, structural)
        if guided:
            result["guided_row_locator"] = guided[0].locator
    result["guided_structure"] = reason or "UNIQUE_MARKER_ROW"
    if method != "poppler" and guided:
        result["guided_field"] = _matching_cells(guided[0], _claim_field(claim))
    if matching := _same_row_matches(native, markers, token, automatic)[0]:
        result["automatic_row_locator"] = matching[0].locator
        result["automatic"] = _matching_cells(matching[0], _claim_field(claim))
    else:
        result["automatic"] = {"status": "NO_UNIQUE_AUTOMATIC_MARKER_ROW"}
    return result


def _run_extractor(method, body, document, pages, model_runtime):
    if method == "poppler":
        return {page: _poppler_rows(document, page) for page in pages}
    if method == "table-transformer":
        from au_ree_v3.table_transformer import open_transformer_geometry

        handle = open_transformer_geometry(body, model_runtime)
    else:
        from au_ree_v3.table_geometry import open_table_geometry

        handle = open_table_geometry(body, method)
    with handle as reader:
        return {page: _coordinate_rows(reader, page) for page in pages if page <= reader.page_count}


def _reference_document(body, source, last_page):
    def decode(limit):
        return DocumentCache().pdf(body, source, last_page=limit)

    try:
        return decode(last_page)
    except DocumentError as error:
        if str(error) != "PHYSICAL_PAGE_MISSING":
            raise
        return decode(None)


def _process_source(source, claims, holdouts, args, store, model_runtime, counters):
    pages = {row["physical_page"] for row, _ in claims} | {p["physical_page"] for _, p in holdouts}
    originals_started = perf_counter()
    try:
        body = _original_body(source, store, args.historical_assets)
        document = _reference_document(body, source, max(pages))
    except (FileNotFoundError, OSError, DocumentError, IntegrityError) as error:
        if holdouts:
            raise ValueError(f"Fixed holdout original unavailable: {source}") from error
        reason = ("EXACT_ORIGINAL_MISSING" if isinstance(error, FileNotFoundError) else
                  "EXACT_ORIGINAL_SHA_MISMATCH" if isinstance(error, IntegrityError) else
                  str(error) if isinstance(error, DocumentError) else "EXACT_ORIGINAL_UNREADABLE")
        return [(row, {"guided_structure": reason, "automatic": {"status": "UNSCORABLE_ORIGINAL"}})
                for row, _ in claims], []
    counters["original_verification_seconds"] += perf_counter() - originals_started
    present = sorted(page for page in pages if page <= len(document.pages))
    started = perf_counter()
    discovered = _run_extractor(args.method, body, document, present, model_runtime)
    counters["automatic_extract_seconds"] += perf_counter() - started
    counters["same_shas"] += 1
    counters["poppler_verification_pages_decoded"] += len(document.pages)
    counters["evaluated_pages"] += len(present)

    eligible = {}
    for page, rows in discovered.items():
        eligible[page] = tuple(row for row in rows if row.predictions)
    counters["numeric_rows_located_before_scoring"] += sum(map(len, discovered.values()))
    counters["rows_with_bound_roles_before_scoring"] += sum(map(len, eligible.values()))

    claim_rows = []
    for row, claim in claims:
        page = row["physical_page"]
        result = (_claim_result(document, args.method, row, claim, eligible[page], discovered[page])
                  if page in discovered else {"guided_structure": "PHYSICAL_PAGE_MISSING",
                                              "automatic": {"status": "UNSCORABLE_ORIGINAL"}})
        claim_rows.append((row, result))
    probes = []
    for item, probe in holdouts:
        page, line = probe["physical_page"], probe["row"]["line"]
        _verify_probe(document, probe)
        if args.method == "poppler":
            guided = _guided_poppler(document, probe)
        else:
            guided = tuple(row for row in discovered[page] if _annotation_row_matches(row, probe))
        result = _probe_result(probe, eligible[page], guided)
        scoped = [row for row in discovered[page] if _annotation_row_matches(row, probe)]
        result["source_only_structure_row_match"] = "UNIQUE" if len(scoped) == 1 else "AMBIGUOUS" if scoped else "MISSING"
        probes.append({"source_sha256": source, "project_id": item["project_id"], "physical_page": page,
                       "native_annotation_line": line, **result})
    return claim_rows, probes


def _rates(counts):
    return {**counts, **{key: counts[key] for key in ("TP", "FP", "FN")},
            "precision": counts["TP"] / (counts["TP"] + counts["FP"]) if counts["TP"] + counts["FP"] else None,
            "recall": counts["TP"] / (counts["TP"] + counts["FN"]) if counts["TP"] + counts["FN"] else None}


def _partition_summary(claims):
    confirmed = [row for row in claims if row["automatic"]["status"] == "EXACT_ROLE_UNIT_LITERAL"
                 and row["automatic"]["cell"].get("header") and row["automatic"]["cell"].get("unit_witness")]
    unbound = [row for row in claims if row.get("guided_field", {}).get("status") == "HEADER_WITHOUT_BOUND_ROLE"]
    return {
        "claims": len(claims), "source_editions": len({c["source_sha256"] for c in claims}),
        "groups": len({c["group_sha256"] for c in claims if c["group_sha256"]}),
        "guided_structure": dict(Counter(c["guided_structure"] for c in claims)),
        "automatic": dict(Counter(c["automatic"]["status"] for c in claims)),
        "confirmed_header_witness_positions": len({(row["source_sha256"], row["automatic"]["cell"]["locator"]) for row in confirmed}),
        "confirmed_header_roles": dict(Counter(row["automatic"]["cell"]["role"] for row in confirmed)),
        "unbound_header_witness_positions": len({(row["source_sha256"], row["guided_field"]["cell"]["locator"]) for row in unbound}),
    }


def _summary(args, claims, probes, partitions, counters, runtime):
    work = {key: value for key, value in counters.items() if not key.endswith("_seconds")}
    out = {
        "schema": SCHEMA, "method": args.method, "method_package_version": runtime, "code_commit": args.code_commit,
        "scope": "FIXED_PANEL_ONLY" if args.panel_only else "FIXED_PANEL_AND_FROZEN_V2_CLAIMS",
        "frozen_v2_ref": BASELINES["refs"]["science"], "input_sha256": {
            "v2_claims": args.claims_sha256, "source_manifest": args.manifest[1], "project_registry": args.registry[1],
            "previous_claim_run": args.previous[1], "fixed_panels": [sha for _, sha in args.holdout],
            "group_split": digest(partitions),
        },
        "workload": work, "historical_claims": len(claims), "fixed_annotations": len(probes),
        "fixed_expected_cells": sum(probe["automatic"]["TP"] + probe["automatic"]["FN"] for probe in probes),
        "historical_partitions": {name: _partition_summary([row for row in claims if row["partition"] == name])
                                  for name in sorted({row["partition"] for row in claims})},
        "fixed_scoring": {name: _rates(sum((Counter(p[name]) for p in probes), Counter(TP=0, FP=0, FN=0)))
                          for name in ("automatic", "guided")},
        "fixed_row_coverage": dict(Counter((("POSITIVE_" if p["row_label"] else "NEGATIVE_") + p["automatic_row_match"])
                                          for p in probes)),
        "fixed_structure_row_coverage": dict(Counter(
            ("POSITIVE_" if p["row_label"] else "NEGATIVE_") + p["source_only_structure_row_match"] for p in probes)),
        "previous_marker_failures": dict(Counter(c["guided_structure"] for c in claims
                                                  if c["previous_status"] == OLD_FAILURE)),
        "scoring_scope": ("Exact original rows and nominated/forbidden roles only. Predictions outside this scope are "
                          "unscored. Historical weak labels are separate from fixed manually verified annotations."),
        "timing_seconds": {key.removesuffix("_seconds"): value for key, value in counters.items() if key.endswith("_seconds")},
        "peak_rss_kib": {"process": getrusage(RUSAGE_SELF).ru_maxrss, "largest_child": getrusage(RUSAGE_CHILDREN).ru_maxrss},
        "claims": claims, "fixed_probes": probes,
    }
    return out


def evaluate(args):
    started = perf_counter()
    manifest = _pinned(*args.manifest)
    registry = {project["project_id"]: project for project in _pinned(*args.registry)["projects"]}
    previous = _pinned(*args.previous)["evidence"]["historical_claims"]["claims"]
    previous = {(row["project_id"], row["claim_index"]): row for row in previous}
    holdouts = _probe_groups(args.holdout)
    claim_file = PinnedRepository(args.v2_root).read("science", "data/claims.json")
    if claim_file is None or sha256(claim_file).hexdigest() != args.claims_sha256:
        raise ValueError("Pinned historical claims differ")
    claim_groups = defaultdict(list)
    notes = []
    for row, claim, reason in _claim_hints(json.loads(claim_file), registry):
        old = previous[row["project_id"], row["claim_index"]]
        if any(old[key] != row[key] for key in ("source_sha256", "physical_page", "variable")):
            raise ValueError("Pinned previous claim disposition does not match historical source")
        row["previous_status"] = old["status"]
        if reason:
            notes.append((row, {"guided_structure": reason, "automatic": {"status": "UNSCORABLE_INPUT"}}))
        else:
            claim_groups[row["source_sha256"]].append((row, claim))
    split = _source_partitions(claim_groups, holdouts, registry, manifest)
    store = ContentStore(args.store)
    _verify_holdout_captures(holdouts, manifest, store)
    runtime = (version(PACKAGES[args.method]) if args.method in PACKAGES else
               subprocess.run(["pdftotext", "-v"], check=True, capture_output=True, text=True).stderr.splitlines()[0])
    model = None
    if args.method == "table-transformer":
        from au_ree_v3.table_transformer import load_transformer_models

        model = load_transformer_models(*args.transformer_models)
    counters, probes = Counter(), []
    if args.panel_only:
        notes = []
    for source in sorted(holdouts.keys() | (set() if args.panel_only else claim_groups.keys())):
        records, held = _process_source(source, () if args.panel_only else claim_groups[source],
                                        holdouts[source], args, store, model, counters)
        notes.extend(records)
        probes.extend(held)
    claims = sorted(
        ({**row, **result, **split.get(row["source_sha256"], {"group_sha256": None, "partition": "INELIGIBLE_INPUT"})}
         for row, result in notes), key=lambda row: (row["project_id"], row["claim_index"]),
    )
    if not args.panel_only and len(claims) != len(previous):
        raise ValueError("Comparison dropped or repeated frozen claims")
    counters["all_seconds"] = perf_counter() - started
    result = _summary(args, claims, probes, split, counters, runtime)
    if model is not None:
        result["models"] = model.provenance
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", required=True, choices=("poppler", *PACKAGES))
    for name in ("store", "historical-assets", "v2-root", "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    for name in ("manifest", "registry", "previous"):
        parser.add_argument(f"--{name}", required=True, nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--claims-sha256", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--holdout", required=True, nargs=2, action="append", metavar=("PATH", "SHA256"))
    parser.add_argument("--panel-only", action="store_true")
    parser.add_argument("--transformer-models", nargs=2, type=Path, metavar=("DETECTOR", "STRUCTURE"))
    args = parser.parse_args()
    if (args.method == "table-transformer") != bool(args.transformer_models):
        parser.error("--transformer-models is required for the Table Transformer backend only")
    payload = evaluate(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
