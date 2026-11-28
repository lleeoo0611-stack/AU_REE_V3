"""Compare weakly trained resource column roles on frozen original-PDF witnesses."""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version
import json
from math import log, sqrt
from pathlib import Path
import re
from resource import RUSAGE_SELF, getrusage
from time import perf_counter
from unicodedata import normalize

from au_ree_v3.migration.baseline import BASELINES, PinnedRepository
from au_ree_v3.normalization import KNOWN_OXIDE_SPECIES
from au_ree_v3.observations import digest
from au_ree_v3.sources import ContentStore
from au_ree_v3.table_geometry import (
    _caption, _join, _lexical_binding, _line_groups, _mupdf_fragments, _numbers,
    _pieces, _tier_fragments, _windows, open_table_geometry,
)

if __package__:
    from .compare_resource_table_structure import (
        CLAIM_OXIDES, NATIVE_ROLE, OXIDES, _annotation_row_matches, _coordinate_rows,
        _original_body, _pinned, _probe_groups, _reference_document,
        _same_row_matches, _source_partitions, _verify_holdout_captures, _verify_probe,
    )
    from .historical_claim_rows import _NUMBER, _claim_hints, _fold
else:
    from compare_resource_table_structure import (
        CLAIM_OXIDES, NATIVE_ROLE, OXIDES, _annotation_row_matches, _coordinate_rows,
        _original_body, _pinned, _probe_groups, _reference_document,
        _same_row_matches, _source_partitions, _verify_holdout_captures, _verify_probe,
    )
    from historical_claim_rows import _NUMBER, _claim_hints, _fold

SCHEMA = "resource-column-role-evaluation-v1"
ORACLE_SCHEMA = "resource-column-oracle-v1"
MODEL = "character-tfidf-and-position-nearest-example-v1"
_TEXT_WEIGHT, _UNIT_WEIGHT, _POSITION_WEIGHT = 0.75, 0.10, 0.15
_MIN_SCORE = 0.25
_OXIDE_ROLES = frozenset(f"{oxide.casefold()}_ppm" for oxide in KNOWN_OXIDE_SPECIES)
_OTHER_DIRECT = {
    "uranium_ppm", "thorium_ppm", "uranium_oxide_ppm", "thorium_oxide_ppm",
    "reported_resource_u3o8_ppm", "reported_resource_tho2_ppm",
    "reported_technical_flotation_recovery_pct", "reported_technical_product_grade_ppm",
    "reported_technical_product_grade_pct",
}
_WORDS = re.compile(r"[\w%/+-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Features:
    header: str
    unit: str
    position: float
    x: float
    lexical: str | None
    witnesses: tuple[tuple[str, tuple], ...]


@dataclass(frozen=True, slots=True)
class Example:
    source: str
    page: int
    box: tuple
    group: str
    role: str
    features: Features | None

    @property
    def position_key(self):
        return self.source, self.page, self.box


def _claim_role(claim):
    if any(claim.get(key) for key in ("transform", "normalization", "species_conversion")):
        return None
    variable = claim["variable"]
    basis = (claim.get("basis") or "").casefold()
    if match := OXIDES.fullmatch(variable):
        return match[1] + "_ppm" if basis == match[1] else None
    if role := CLAIM_OXIDES.get(variable):
        return role if basis == role.removesuffix("_ppm") else None
    return NATIVE_ROLE.get(variable) or ("OTHER" if variable in _OTHER_DIRECT else None)


def _decimal_token(token):
    if isinstance(token, str) and _NUMBER.fullmatch(token):
        return Decimal(token.replace(",", ""))
    return None


def _target_token(claim):
    if (token := claim.get("original_token")) is not None:
        if (numeric := _decimal_token(token)) is not None:
            return numeric, "ORIGINAL_TOKEN"
        return None, "INVALID_EXACT_ORIGINAL_TOKEN"
    legacy_value = claim.get("value")
    if type(legacy_value) not in (int, float):
        return None, "NO_FINITE_DIRECT_VALUE"
    numeric = Decimal(str(legacy_value))
    if not numeric.is_finite():
        return None, "NO_FINITE_DIRECT_VALUE"
    markers = (token for marker in claim["page_markers"] for token in _NUMBER.findall(_fold(marker)))
    return ((numeric, "EXACT_NUMERIC_MARKER") if any(_decimal_token(token) == numeric for token in markers)
            else (None, "NO_EXACT_VALUE_MARKER"))


def _unique_claim_cell(row, claim):
    target, provenance = _target_token(claim)
    if target is None:
        return None, provenance
    matches = [(literal, box) for literal, box in row.printed_tokens if _decimal_token(literal) == target]
    if len(matches) != 1:
        status = "AMBIGUOUS_VALUE_OCCURRENCE" if matches else "EXACT_VALUE_OCCURRENCE_MISSING"
        return None, status
    binding = claim.get("cell_binding") or {}
    box = binding.get("column_bbox")
    literal, occurrence = matches[0]
    if box and not box[0] <= (occurrence[0] + occurrence[2]) / 2 <= box[2]:
        return None, "FROZEN_COLUMN_HYPOTHESIS_DIFFERS"
    return (literal, occurrence), provenance


class GeometryFeatures:
    def __init__(self, page):
        self.width = page.rect.width
        self.lines = tuple(_pieces(line) for line in _line_groups(tuple(_mupdf_fragments(page))))
        self.fragments = tuple(_join(line) for line in self.lines)
        self.by_box = {(piece.bbox, match["number"]): (piece, match, numbers, index)
                       for line in self.lines if (numbers := _numbers(line))
                       for index, (piece, match) in enumerate(numbers)}

    def read(self, row_box, cell_box, literal):
        record = self.by_box.get((tuple(cell_box), literal))
        if record is None:
            return None
        piece, match, numbers, index = record
        caption = _caption(self.fragments, row_box)
        tiers = _windows(self.lines, row_box, caption)
        fragments = _tier_fragments(tiers, piece, numbers)
        binding = _lexical_binding(fragments, match)
        if binding:
            header, _, unit, recognized = binding
            heading, role = header.text, recognized[0] if recognized else None
        else:
            heading, unit, role = " ".join(part.text for part in fragments), "", None
        return Features(
            heading, unit, index / max(1, len(numbers) - 1),
            (piece.bbox[0] + piece.bbox[2]) / (2 * self.width), role,
            tuple((part.text, part.bbox) for part in fragments),
        )


def _weak_claim(original, row, claim, structural, layout):
    if not (role := _claim_role(claim)):
        return {"status": "UNSUPPORTED_OR_TRANSFORMED_ROLE"}, None
    page = row["physical_page"]
    matches, reason = _same_row_matches(original.locate(page).text, claim["page_markers"],
                                       claim.get("original_token"), structural)
    if not matches or matches[0].locator != row["guided_row_locator"]:
        return {"status": "FROZEN_ROW_NOT_REPRODUCED", "reason": reason or "LOCATOR_DIFFERS"}, None
    match, provenance = _unique_claim_cell(matches[0], claim)
    if match is None:
        return {"status": provenance}, None
    literal, cell_box = match
    features = layout.read(matches[0].bbox, cell_box, literal)
    if features is None:
        return {"status": "COLUMN_POSITION_NOT_REPRODUCED"}, None
    item = Example(row["source_sha256"], page, cell_box, row["group_sha256"], role, features)
    return {"status": "WEAK_COLUMN", "role": role, "literal": literal, "bbox": cell_box,
            "row_locator": matches[0].locator, "value_provenance": provenance,
            "header_witness": bool(features.witnesses), "lexical_role": features.lexical,
            "printed_unit": features.unit, "feature_sha256": digest(_feature_digest(features))}, item


def _feature_digest(features):
    return [features.header, features.unit, features.position, features.x, features.witnesses]


def _ngrams(features):
    text = " ".join(_WORDS.findall(normalize("NFKC", features.header).casefold()))
    padded = "^" + text + "$"
    return Counter(padded[i:i + size] for size in range(2, 5) for i in range(len(padded) - size + 1)) if text else Counter()


class RoleModel:
    """Character TF-IDF and relative column position with deterministic 1-NN."""

    def __init__(self, examples):
        term_counts = [_ngrams(example.features) for example in examples]
        occurrences = Counter(term for terms in term_counts for term in terms)
        total = len(examples)
        self.idf = {term: log((total + 1) / (count + 1)) + 1 for term, count in occurrences.items()}
        self.training = [(example, self._vector(terms)) for example, terms in zip(examples, term_counts, strict=True)]

    def _vector(self, terms):
        vector = {term: (1 + log(count)) * self.idf[term] for term, count in terms.items() if term in self.idf}
        length = sqrt(sum(value * value for value in vector.values()))
        return {term: value / length for term, value in vector.items()} if length else {}

    def predict(self, features):
        if features is None:
            return None
        vector = self._vector(_ngrams(features))
        if not vector:
            return None
        best_by_role = {}
        for example, representation in self.training:
            trained = example.features
            similarity = sum(value * representation.get(term, 0) for term, value in vector.items())
            units_agree = bool(features.unit and trained.unit == features.unit)
            position = 1 - (abs(features.position - trained.position) + abs(features.x - trained.x)) / 2
            score = _TEXT_WEIGHT * similarity + _UNIT_WEIGHT * units_agree + _POSITION_WEIGHT * position
            best_by_role[example.role] = max(score, best_by_role.get(example.role, 0))
        order = sorted(best_by_role.items(), key=lambda pair: (-pair[1], pair[0]))
        if not order or order[0][1] < _MIN_SCORE:
            return None
        if len(order) > 1 and order[0][1] - order[1][1] < 1e-12:
            return None
        return order[0][0]


def _deduplicate_development(examples):
    linked = defaultdict(list)
    for index, example in examples:
        linked[example.position_key].append((index, example))
    resolved, statuses = [], {}
    for position in sorted(linked):
        entries = linked[position]
        roles = {example.role for _, example in entries}
        for offset, (index, example) in enumerate(entries):
            if len(roles) > 1:
                statuses[index] = "CONFLICTING_WEAK_ROLES"
            elif offset:
                statuses[index] = "DUPLICATE_POSITION"
            else:
                statuses[index] = "TRAINING_POSITION"
                resolved.append(example)
    return resolved, statuses


def _scores(examples, predict):
    counts = defaultdict(lambda: Counter(TP=0, FP=0, FN=0))
    rows = []
    for example in examples:
        label = example.role
        guess = predict(example.features)
        if guess == label:
            counts[label]["TP"] += 1
        else:
            counts[label]["FN"] += 1
            if guess is not None:
                counts[guess]["FP"] += 1
        rows.append({"source_sha256": example.source, "physical_page": example.page, "bbox": example.box,
                     "role": label, "predicted": guess, "header_witness": bool(example.features and example.features.witnesses)})
    totals = sum(counts.values(), Counter(TP=0, FP=0, FN=0))
    supported = sum((value for role, value in counts.items() if role != "OTHER"), Counter(TP=0, FP=0, FN=0))
    per_role = {role: _rates(value) for role, value in sorted(counts.items())}
    active_f1 = [item["f1"] for item in per_role.values() if item["f1"] is not None]
    supported_f1 = [item["f1"] for role, item in per_role.items() if role != "OTHER" and item["f1"] is not None]
    return {
        "per_role": per_role,
        "overall": _rates(totals), "abstained": sum(row["predicted"] is None for row in rows),
        "macro_f1": sum(active_f1) / len(active_f1) if active_f1 else None,
        "supported_resource_roles": _rates(supported),
        "supported_resource_macro_f1": sum(supported_f1) / len(supported_f1) if supported_f1 else None,
        "positions": rows,
    }


def _rates(counts):
    tp, fp, fn = (counts[key] for key in ("TP", "FP", "FN"))
    return {"TP": tp, "FP": fp, "FN": fn, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def _benchmark_joins(args, manifest, registry, fixed):
    payload = PinnedRepository(args.v2_root).read("science", "data/claims.json")
    if payload is None or sha256(payload).hexdigest() != args.claims_sha256:
        raise ValueError("Frozen V2 claims.json hash differs")
    benchmark = _pinned(*args.benchmark)
    if (benchmark.get("schema"), benchmark.get("method"), benchmark.get("frozen_v2_ref")) != (
        "resource-table-structure-comparison-v1", "pymupdf", BASELINES["refs"]["science"],
    ):
        raise ValueError("Expected the frozen PyMuPDF comparison receipt")
    old_pins = benchmark["input_sha256"]
    if (old_pins["v2_claims"], old_pins["source_manifest"], old_pins["project_registry"], old_pins["fixed_panels"]) != (
        args.claims_sha256, args.manifest[1], args.registry[1], [pin for _, pin in args.holdout],
    ):
        raise ValueError("The frozen comparison uses different source or split inputs")
    rows = {(row["project_id"], row["claim_index"]): row for row in benchmark["claims"]}
    claim_groups, matched = defaultdict(list), defaultdict(list)
    for row, claim, reason in _claim_hints(json.loads(payload), registry):
        previous = rows.pop((row["project_id"], row["claim_index"]))
        if any(row[key] != previous[key] for key in ("source_sha256", "physical_page", "variable")):
            raise ValueError("Historical claim identity differs from the frozen comparison")
        source = row["source_sha256"]
        if not reason:
            claim_groups[source].append((row, claim))
        if previous["guided_structure"] == "UNIQUE_MARKER_ROW":
            if reason:
                raise ValueError("Frozen unique marker claim lacks valid input")
            matched[source].append((previous, claim))
    if rows:
        raise ValueError("The frozen comparison contains unknown claims")
    partition = _source_partitions(claim_groups, fixed, registry, manifest)
    if digest(partition) != old_pins["group_split"]:
        raise ValueError("Issuer/Report family/Project split differs from frozen comparison")
    for source, items in matched.items():
        for previous, _ in items:
            if any(previous[k] != partition[source][k] for k in ("group_sha256", "partition")):
                raise ValueError("Stored marker row is inconsistent with its frozen split")
    if sum(map(len, matched.values())) != 215 or benchmark["historical_claims"] != 691:
        raise ValueError("The benchmark does not cover the frozen 691 claims / 215 unique marker rows")
    return matched, partition


def _geometry_features(reader, page):
    return GeometryFeatures(reader.document[page - 1])


def _reviewed_role(role):
    return (role == "OTHER" or role in NATIVE_ROLE.values() or role in CLAIM_OXIDES.values()
            or role in _OXIDE_ROLES)


def _additional_groups(args, partition):
    if not args.oracle:
        return {}
    payload = _pinned(*args.oracle)
    if payload.get("schema") != ORACLE_SCHEMA:
        raise ValueError("Unexpected independent original-PDF oracle schema")
    by_source, used = defaultdict(list), set()
    for row in payload["rows"]:
        source = row["source_sha256"]
        group = partition.get(source)
        if not group or group["partition"] == "DEVELOPMENT":
            raise ValueError("Independent row overlaps a development or untracked source group")
        if (row.get("group_sha256"), row.get("partition")) != (group["group_sha256"], group["partition"]):
            raise ValueError("Independent row does not retain its frozen group identity")
        if not row["cells"] or type(row["physical_page"]) is not int or row["physical_page"] < 1:
            raise ValueError("Independent oracle row has no physical page or cells")
        for cell in row["cells"]:
            identity = source, row["physical_page"], tuple(cell["bbox"])
            if identity in used or not _reviewed_role(cell["role"]):
                raise ValueError("Independent original-PDF oracle duplicates a position or defines an unknown role")
            used.add(identity)
        by_source[source].append(row)
    return by_source


def _fixed_examples(document, structural, layout, probe, source, group):
    _verify_probe(document, probe)
    candidates = [row for row in structural if _annotation_row_matches(row, probe)]
    supported = candidates[0] if len(candidates) == 1 else None
    page = probe["physical_page"]
    for field in probe["expected_fields"]:
        features, box = None, (probe["row"]["line"], field["start_column"], field["end_column"])
        if supported:
            cells = [position for literal, position in supported.printed_tokens if literal == field["literal"]]
            if len(cells) == 1:
                box = cells[0]
                features = layout.read(supported.bbox, box, field["literal"])
            else:
                start = probe["row"]["positioned_text"]
                originals = [m.start() + 1 for m in _NUMBER.finditer(start) if m[0] == field["literal"]]
                if len(cells) == len(originals) and field["start_column"] in originals:
                    box = sorted(cells)[originals.index(field["start_column"])]
                    features = layout.read(supported.bbox, box, field["literal"])
        yield Example(source, page, box, group, field["variable"], features)


def _inside(outer, inner):
    return (outer[0] - 1 <= inner[0] < inner[2] <= outer[2] + 1
            and outer[1] - 1 <= inner[1] < inner[3] <= outer[3] + 1)


def _reviewed_examples(rows, layout, row, source, group):
    box = tuple(row["row_bbox"])
    if box not in {candidate.bbox for candidate in rows}:
        raise ValueError("Independent oracle row does not replay against raw source geometry")
    for cell in row["cells"]:
        cell_box = tuple(cell["bbox"])
        if not _inside(box, cell_box):
            raise ValueError("Independent oracle token is outside its named source row")
        features = layout.read(box, cell_box, cell["literal"])
        if not features:
            raise ValueError("Independent oracle literal and bounding box cannot be reread")
        yield Example(source, row["physical_page"], cell_box, group, cell["role"], features)


def _read_page(reader, original, page, claims, fixed, extra, partition, source):
    layout = _geometry_features(reader, page)
    structural = _coordinate_rows(reader, page)
    records, examples = [], []
    for previous, claim in claims:
        outcome, example = _weak_claim(original, previous, claim, structural, layout)
        row = {key: previous[key] for key in ("project_id", "claim_index", "source_sha256", "physical_page",
                                            "partition", "group_sha256", "variable", "guided_row_locator")}
        records.append({**row, **outcome})
        if example is not None:
            examples.append((row["project_id"], row["claim_index"], example))
    fixed_examples = [e for _, probe in fixed
                      for e in _fixed_examples(original, structural, layout, probe, source, partition[source]["group_sha256"])]
    extra_examples = [e for row in extra
                      for e in _reviewed_examples(structural, layout, row, source, partition[source]["group_sha256"])]
    return records, examples, fixed_examples, extra_examples


def _read_sources(args, matched, fixed, oracle, partition):
    records, weak, independent, original = [], [], [], []
    store = ContentStore(args.store)
    for source in sorted(matched.keys() | fixed.keys() | oracle.keys()):
        by_page = defaultdict(lambda: ([], [], []))
        for item in matched.get(source, ()):
            by_page[item[0]["physical_page"]][0].append(item)
        for item in fixed.get(source, ()):
            by_page[item[1]["physical_page"]][1].append(item)
        for item in oracle.get(source, ()):
            by_page[item["physical_page"]][2].append(item)
        body = _original_body(source, store, args.historical_assets)
        document = _reference_document(body, source, max(by_page))
        with open_table_geometry(body, "pymupdf") as reader:
            for page, (claims, probes, extra) in sorted(by_page.items()):
                loaded = _read_page(reader, document, page, claims, probes, extra, partition, source)
                for dst, batch in zip((records, weak, original, independent), loaded, strict=True):
                    dst.extend(batch)
    return records, weak, original, independent


def _retain_weak(records, weak):
    by_identity = {(r["project_id"], r["claim_index"]): r for r in records}
    index = {i: (project, ordinal) for i, (project, ordinal, _) in enumerate(weak)}
    eligible = [(i, example) for i, (_, _, example) in enumerate(weak)
                if by_identity[index[i]]["partition"] == "DEVELOPMENT"]
    trained, dispositions = _deduplicate_development(eligible)
    for i in index:
        row = by_identity[index[i]]
        row["training_disposition"] = dispositions.get(i, "EXCLUDED_FROZEN_GROUP")
    return trained


def evaluate(args):
    started = perf_counter()
    manifest = _pinned(*args.manifest)
    registry = {p["project_id"]: p for p in _pinned(*args.registry)["projects"]}
    fixed = _probe_groups(args.holdout)
    matched, partition = _benchmark_joins(args, manifest, registry, fixed)
    _verify_holdout_captures(fixed, manifest, ContentStore(args.store))
    oracle = _additional_groups(args, partition)
    records, weak, fixed_examples, independent = _read_sources(args, matched, fixed, oracle, partition)
    trained = _retain_weak(records, weak)
    if not trained:
        raise ValueError("No exact-original weak development columns are available")
    if {e.group for e in trained} & {e.group for e in (*fixed_examples, *independent)}:
        raise ValueError("Connected held source groups leaked into development training")
    if len(fixed_examples) != 22 or sum(map(len, fixed.values())) != 23:
        raise ValueError("Frozen 23-row / 22-value control changed")
    inputs = {
        "comparison": args.benchmark[1], "v2_claims": args.claims_sha256, "registry": args.registry[1],
        "source_manifest": args.manifest[1], "frozen_split": digest(partition),
        "fixed_panels": [h[1] for h in args.holdout], "independent_oracle": args.oracle[1] if args.oracle else None,
    }
    build_seconds = perf_counter() - started
    model = RoleModel(trained)
    training_seconds = perf_counter() - started - build_seconds
    results = {}
    for key, examples, rows in (
        ("FIXED_REGRESSION", fixed_examples, sum(map(len, fixed.values()))),
        ("INDEPENDENT_ORIGINAL_PDF", independent, sum(map(len, oracle.values()))),
    ):
        results[key] = {
            "scored_rows": rows, "annotated_positions": len(examples),
            "source_editions": len({e.source for e in examples}),
            "connected_groups": len({e.group for e in examples}),
            "rules": _scores(examples, lambda feature: feature.lexical if feature else None),
            "tfidf_geometry": _scores(examples, model.predict),
        }
    return {
        "schema": SCHEMA, "model": MODEL, "input_sha256": inputs, "code_commit": args.code_commit,
        "model_parameters": {"character_ngram_lengths": [2, 3, 4], "requires_shared_header_terms": True,
                             "tfidf_weight": _TEXT_WEIGHT,
                             "same_unit_weight": _UNIT_WEIGHT, "relative_position_weight": _POSITION_WEIGHT,
                             "minimum_similarity": _MIN_SCORE},
        "pymupdf_version": version("PyMuPDF"), "frozen_v2_ref": BASELINES["refs"]["science"],
        "scientific_use": "SHADOW_ONLY",
        "historical_rows": sorted(records, key=lambda r: (r["project_id"], r["claim_index"])),
        "historical_dispositions": dict(Counter(r["status"] for r in records)),
        "training_dispositions": dict(Counter(r["training_disposition"] for r in records if "training_disposition" in r)),
        "training": {
            "unique_positions": len(trained), "source_editions": len({e.source for e in trained}),
            "connected_groups": len({e.group for e in trained}), "roles": dict(Counter(e.role for e in trained)),
            "header_witnesses": sum(bool(e.features.witnesses) for e in trained),
            "corpus_sha256": digest([(e.position_key, e.role, _feature_digest(e.features)) for e in trained]),
        },
        "results": results,
        "timing_seconds": {"decode_and_bind": build_seconds, "fit": training_seconds,
                           "end_to_end": perf_counter() - started},
        "peak_rss_kib": getrusage(RUSAGE_SELF).ru_maxrss,
    }


def main():
    parser = ArgumentParser(description=__doc__)
    for name in ("store", "historical-assets", "v2-root", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("manifest", "registry", "benchmark"):
        parser.add_argument(f"--{name}", nargs=2, metavar=("PATH", "SHA256"), required=True)
    parser.add_argument("--holdout", nargs=2, metavar=("PATH", "SHA256"), action="append", required=True)
    parser.add_argument("--oracle", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--claims-sha256", required=True)
    parser.add_argument("--code-commit", required=True)
    args = parser.parse_args()
    receipt = evaluate(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
