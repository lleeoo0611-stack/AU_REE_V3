"""Build an offline original-column review packet and import bound annotations."""

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import csv
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import statistics
import unicodedata

from au_ree_v3.contracts import canonical_json
from au_ree_v3.documents import DocumentCache, DocumentError
from au_ree_v3.native_table_work import recovered_cohort_records
from au_ree_v3.normalization import KNOWN_OXIDE_SPECIES
from au_ree_v3.resource_table_binding import _candidate_tokens
from au_ree_v3.sources import ContentStore
from au_ree_v3.table_geometry import open_table_geometry

QUEUE = "resource-column-review-v1"
ANNOTATIONS = "resource-column-annotations-v1"
RECEIPT_SCHEMA = "historical-cohort-admission-research-receipt-v1"
PURPOSE = "COLUMN_ROLE_ADJUDICATION_ONLY"
_DIRECT_FIELDS = {"ore_mt": "ore_mt", "treo_grade_pct": "grade", "contained_treo_kt": "contained_treo_kt"}
_DIRECT_METHODS = {"REPORTED", "SAME_SOURCE_STATEMENT_SCOPE_DATE"}
_CONTEXT_FIELDS = {"project_name", "overview_note", "has_treo_resource"}
DISPOSITIONS = ("ASSIGNED", "UNCERTAIN", "MULTIPLE_CONTEXTS", "DERIVED_FIELD")
ROLES = ("ore_mt", "grade", "contained_treo_kt", *(f"{oxide.casefold()}_ppm" for oxide in sorted(KNOWN_OXIDE_SPECIES)),
         "OTHER_REPORTED_VARIABLE")
LABEL_FIELDS = ("card_id", "evidence_sha256", "disposition", "variable", "reviewer", "seconds")
_RENDER_SCALE = 1.6


def _digest(data):
    return sha256(data).hexdigest()


def _key(data):
    return _digest(canonical_json(data).encode("utf-8"))


def _pinned(path, expected):
    body = Path(path).read_bytes()
    if _digest(body) != expected:
        raise ValueError(f"Input SHA-256 differs from its pin: {Path(path).name}")
    return json.loads(body)


def _write(path, data):
    with Path(path).open("x", encoding="utf-8") as output:
        output.write(canonical_json(data) + "\n")


def _fold(text):
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _classification(cell):
    field = cell["column_id"]
    if field in _DIRECT_FIELDS and cell["historical_method"] in _DIRECT_METHODS:
        return "DIRECT_TARGET"
    return "CONTEXT_OR_DISCLOSURE" if field in _CONTEXT_FIELDS else "DERIVED_INPUTS"


def _target_cells(receipt):
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("Review requires the versioned historical cohort admission receipt")
    return [r for r in receipt["cells"] if r["historical_candidate_cell"]
            and r["formal"]["first_open_gate"] == "COLUMN_ROLE"
            and any(t["kind"] == "NUMERIC" for t in r["routes"])]


def _routes(cells, raw):
    uses, targets = defaultdict(list), []
    for row in cells:
        target = {k: row[k] for k in ("project_id", "column_id", "column", "historical_method")}
        target["baseline_column_role_reason"] = row["formal"]["reason"]
        target.update(kind=_classification(row), evidence_ids=[], same_row_direct_evidence_ids=[])
        linked_rows = set()
        for route in row["routes"]:
            if route["kind"] != "NUMERIC":
                continue
            native = raw.get(route["candidate_id"])
            if (native is None or native["project_id"] != row["project_id"]
                    or native["source_sha256"] != route["source_sha256"]
                    or native["value_anchor"]["locator"] not in route["locators"]):
                raise ValueError("Census route does not identify its original SQLite candidate")
            target["evidence_ids"].append(native["candidate_id"])
            linked_rows.add(_source_row(native))
        if target["kind"] == "DIRECT_TARGET":
            role = _DIRECT_FIELDS[row["column_id"]]
            target["same_row_direct_evidence_ids"] = sorted(
                candidate["candidate_id"] for candidate in raw.values()
                if candidate["variable"] == role and _source_row(candidate) in linked_rows
                and candidate["candidate_id"] not in target["evidence_ids"]
            )
        for identity in target["evidence_ids"] + target["same_row_direct_evidence_ids"]:
            uses[identity].append((row["project_id"], row["column_id"]))
        targets.append(target)
    return uses, targets


def _source_row(row):
    return row["project_id"], row["source_sha256"], row["row_anchor"]["locator"]


def _source_anchors(raw, text):
    value = raw["value_anchor"]
    page = raw["physical_page"]
    line = text.locate(page, line=value["line"])
    if line.text != raw["row_anchor"]["text"]:
        raise ValueError("Pinned review row differs from its original PDF layout")
    anchors = [value, raw.get("header_anchor"), raw.get("unit_anchor"), *raw.get("caption_anchors", ()),
               *raw.get("context_anchors", ()), *raw.get("header_tier_anchors", ())]
    if raw.get("parent_header_anchor"):
        anchors.append(raw["parent_header_anchor"])
    for anchor in filter(None, anchors):
        span = text.locate(anchor["physical_page"], line=anchor["line"], column=anchor["start_column"],
                           end_column=anchor["end_column"])
        if span.text != anchor["text"] or span.locator != anchor["locator"]:
            raise ValueError("Pinned review span differs from its original PDF layout")


def _position(raw, rows):
    """Align a printed occurrence by row, using its full number sequence when labels drift vertically."""
    anchor, row_text = raw["value_anchor"], raw["row_anchor"]["text"]
    tokens = _candidate_tokens(row_text, raw["physical_page"], anchor["line"])
    positions = tuple(i for i, token in enumerate(tokens) if (token.start_column, token.end_column, token.text)
                      == (anchor["start_column"], anchor["end_column"], anchor["text"]))
    exact = [row for row in rows if _fold(row.text) == _fold(row_text)]
    sequence = tuple(_fold(token.text) for token in tokens)
    pool = exact or [row for row in rows if tuple(_fold(c.text) for c in row.cells) == sequence]
    if len(pool) != 1 or len(positions) != 1:
        return None
    chosen = pool[0]
    witnessed = tuple(c for c in chosen.cells if _fold(c.text) == _fold(anchor["text"]))
    earlier = sum(_fold(token.text) == _fold(anchor["text"]) for token in tokens[:positions[0]])
    if len(witnessed) != sum(_fold(token.text) == _fold(anchor["text"]) for token in tokens):
        return None
    return chosen, witnessed[earlier]


def _column_key(raw, position):
    anchors = (raw.get("header_anchor"), raw.get("unit_anchor"), *raw.get("caption_anchors", ()),
               raw.get("parent_header_anchor"))
    return (raw["source_sha256"], raw["physical_page"], *(a["locator"] if a else "" for a in anchors),
            raw["value_anchor"]["locator"] if position is None or not raw.get("header_anchor") else "")


def _part(raw, position):
    fields = ("candidate_id", "project_id", "source_sha256", "source_url", "physical_page", "scope_status",
              "native_unit", "variable", "chemical_basis", "header_anchor", "unit_anchor", "caption_anchors",
              "row_anchor", "value_anchor", "context_anchors")
    record = {k: raw[k] for k in fields}
    record["header_tier_anchors"] = raw.get("header_tier_anchors", [])
    record["parent_header_anchor"] = raw.get("parent_header_anchor")
    record["position"] = {"row_bbox": position[0].bbox, "value_bbox": position[1].bbox,
                          "header_bbox": position[1].header_bbox} if position else None
    return record


def _stored_parts(inventory, store):
    by_source = defaultdict(list)
    for row in inventory:
        by_source[row["source_sha256"]].append(row)
    cache = DocumentCache()
    for source, group in sorted(by_source.items()):
        try:
            content = store.read(source)
        except FileNotFoundError:
            for original in group:
                yield original, None, None
            continue
        last_page = max(max((a["physical_page"] for a in (*row.get("caption_anchors", ()),
                            *row.get("context_anchors", ()), *row.get("header_tier_anchors", ()),
                            row.get("header_anchor"), row.get("unit_anchor"), row.get("parent_header_anchor")) if a),
                            default=row["physical_page"]) for row in group)
        pdf = cache.pdf(content, source, max(last_page, *(row["physical_page"] for row in group)))
        with open_table_geometry(content, "pymupdf") as geometry:
            for page in sorted({r["physical_page"] for r in group}):
                positioned = geometry.page(page)
                for row in (r for r in group if r["physical_page"] == page):
                    _source_anchors(row, pdf)
                    yield row, _position(row, positioned), geometry.document[page - 1]


def _near_caption(page, raw, upper):
    headings = tuple(x for x in raw["caption_anchors"] if x["physical_page"] == raw["physical_page"])
    if not headings:
        return None
    title = headings[0]["text"]
    short = " ".join(title.split()[:7])
    for phrase in dict.fromkeys((title, short)):
        matches = [rect for rect in page.search_for(phrase) if rect.y1 <= upper]
        if len(matches) == 1:
            return matches[0]
    return None


def _render_crop(directory, page, raw, position, rendered):
    rotation = page.rotation
    if rotation:
        # Native word coordinates are always unrotated. Render that same view
        # in memory and then restore the loaded page's declared presentation.
        page.set_rotation(0)
    try:
        return _render_unrotated(directory, page, raw, position, rendered)
    finally:
        if rotation:
            page.set_rotation(rotation)


def _render_unrotated(directory, page, raw, position, rendered):
    import pymupdf

    row, cell = position
    caption = _near_caption(page, raw, row.bbox[1])
    top = caption.y0 - 4 if caption is not None else row.bbox[1] - 78
    # Bound each local image and include nearby notes after the printed row.
    clip = pymupdf.Rect(page.rect.x0, max(page.rect.y0, min(row.bbox[1] - 34, max(row.bbox[1] - 225, top))),
                        page.rect.x1, min(page.rect.y1, row.bbox[3] + 53))
    key = _key([raw["source_sha256"], raw["physical_page"], tuple(round(edge, 2) for edge in clip)])
    filename = f"crops/{key}.png"
    if key not in rendered:
        pixmap = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(_RENDER_SCALE, _RENDER_SCALE), alpha=False)
        body = pixmap.tobytes("png")
        (directory / filename).write_bytes(body)
        rendered[key] = _digest(body)
    left, upper, right, lower = cell.bbox
    return {"path": filename, "sha256": rendered[key], "clip": tuple(clip),
            "highlight": [round(100 * n, 2) for n in ((left - clip.x0) / clip.width, (upper - clip.y0) / clip.height,
                                                       (right - left) / clip.width, (lower - upper) / clip.height)]}


def _build_cards(uses, targets, raw, store, directory):
    cards, by_candidate, rendered = {}, {}, {}
    for original, position, page in _stored_parts((raw[key] for key in sorted(uses)), store):
        identity = original["candidate_id"]
        if page is None:
            by_candidate[identity] = {"state": "SOURCE_UNAVAILABLE"}
            continue
        crop = _render_crop(directory, page, original, position, rendered) if position else None
        key = _key(_column_key(original, position))
        part = _part(original, position)
        part["crop"] = crop
        card = cards.setdefault(key, {"card_id": key, "source_sha256": part["source_sha256"],
                                      "physical_page": part["physical_page"], "witnesses": [], "linked_targets": []})
        card["witnesses"].append(part)
        card["linked_targets"].extend(key for key in uses[identity] if key not in card["linked_targets"])
        by_candidate[identity] = {"state": "CROP_AVAILABLE" if crop else "LAYOUT_TEXT_ONLY", "card_id": key}
    for target in targets:
        routes = target["evidence_ids"] + target["same_row_direct_evidence_ids"]
        target["evidence"] = [{**by_candidate[key], "candidate_id": key} for key in routes]
        target["state"] = ("REVIEW_ORIGINAL_COLUMN" if target["kind"] == "DIRECT_TARGET" else
                           "REQUIRES_DERIVATION_PROOF" if target["kind"] == "DERIVED_INPUTS" else
                           "REQUIRES_CONTEXT_PROOF")
    for card in cards.values():
        card["linked_targets"].sort()
        card["evidence_sha256"] = _key(card)
    return [cards[key] for key in sorted(cards)]


def _summary(cards, targets):
    positioned = {w["candidate_id"] for c in cards for w in c["witnesses"] if w["crop"]}
    return {
        "target_cells": len(targets),
        "target_classes": dict(Counter(t["kind"] for t in targets)),
        "original_route_references": sum(len(t["evidence_ids"]) for t in targets),
        "original_positions": len({key for t in targets for key in t["evidence_ids"]}),
        "same_row_direct_positions": len({key for t in targets for key in t["same_row_direct_evidence_ids"]}),
        "unique_cards": len(cards),
        "unique_crop_images": len({w["crop"]["sha256"] for c in cards for w in c["witnesses"] if w["crop"]}),
        "crops_located_positions": len(positioned),
        "cells_linked_to_cropped_positions": sum(any(e["candidate_id"] in positioned for e in t["evidence"]) for t in targets),
        "distinct_sources": len({c["source_sha256"] for c in cards}),
        "target_evidence_states": dict(Counter(e["state"] for t in targets for e in t["evidence"])),
    }


def prepare(funnel, funnel_pin, sqlite, store, out):
    receipt = _pinned(funnel, funnel_pin)
    body = sqlite.read_bytes()
    database_pin = _digest(body)
    if database_pin != receipt["input_sha256"]["candidate_sqlite"]:
        raise ValueError("Candidate SQLite differs from the admission receipt")
    raw = {r["candidate_id"]: r for r in recovered_cohort_records(body)}
    uses, targets = _routes(_target_cells(receipt), raw)
    out.mkdir(parents=True, exist_ok=False)
    (out / "crops").mkdir()
    cards = _build_cards(uses, targets, raw, ContentStore(store), out)
    queue = {"schema": QUEUE, "purpose": PURPOSE, "inputs": {"admission_sha256": funnel_pin,
             "candidate_sqlite_sha256": database_pin}, "cards": cards, "targets": targets, "summary": _summary(cards, targets)}
    _write(out / "queue.json", queue)
    (out / "index.html").write_text(_html(queue), encoding="utf-8")
    with (out / "labels.csv").open("x", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=LABEL_FIELDS, lineterminator="\n")
        writer.writeheader()
        for card in cards:
            writer.writerow({k: card[k] for k in ("card_id", "evidence_sha256")})
    return queue["summary"]


def _image(crop):
    if crop is None:
        return "<p>座標未能唯一對位，以下保留原文與定位。</p>"
    left, top, width, height = crop["highlight"]
    style = f"left:{left}%;top:{top}%;width:{width}%;height:{height}%"
    return (f'<div class="crop"><img loading="lazy" src="{escape(crop["path"])}" alt="原件表格局部">'
            f'<span class="highlight" aria-hidden="true" style="{style}"></span></div>')


def _witness_view(witness):
    anchors = [*witness["caption_anchors"], witness["parent_header_anchor"], *witness["header_tier_anchors"],
               witness["header_anchor"], witness["unit_anchor"], *witness["context_anchors"]]
    used = {}
    for anchor in filter(None, anchors):
        used.update({anchor["locator"]: anchor["text"]})
    notes = "".join(f'<li>{escape(text)} <small>{escape(locator)}</small></li>' for locator, text in used.items())
    value = escape(witness["value_anchor"]["text"])
    locator = escape(witness["value_anchor"]["locator"])
    return (f'<details><summary>{value} · {locator}</summary>{_image(witness["crop"])}'
            f'<pre>{escape(witness["row_anchor"]["text"])}</pre><ul>{notes}</ul></details>')


def _card_view(card):
    options = ('<option value="">待確認</option>'
               '<option value="ASSIGNED">可確認欄義</option>'
               '<option value="UNCERTAIN">不確定</option>'
               '<option value="MULTIPLE_CONTEXTS">多重語境</option>'
               '<option value="DERIVED_FIELD">衍生欄位</option>')
    roles = '<option value="">選擇欄位</option>' + "".join(f'<option>{escape(role)}</option>' for role in ROLES)
    targets = ", ".join(f'<a href="#{escape(project)}-{escape(column)}">{escape(project)} · {escape(column)}</a>'
                        for project, column in card["linked_targets"])
    witnesses = "".join(map(_witness_view, card["witnesses"]))
    first = card["witnesses"][0]
    header = escape((first["header_anchor"] or {}).get("text") or "未定位原件表頭")
    return (f'<article data-card="{card["card_id"]}" data-sha="{card["evidence_sha256"]}">'
            f'<h2>{header}</h2><small>PDF p{card["physical_page"]} · SHA-256 {card["source_sha256"]}</small>'
            f'<div class="inputs"><label>判定 <select data-disposition>{options}</select></label>'
            f'<label>量別 <select data-role disabled>{roles}</select></label>'
            '<label>秒數 <input data-seconds type="number" min="0" step="0.01" inputmode="decimal"></label></div>'
            f'{witnesses}<p>對應 Census 欄格 <span class="targets">{targets}</span></p></article>')


_STYLE = """
body {font:16px/1.5 system-ui,sans-serif; margin:2rem auto; max-width:1080px; padding:0 1rem;color:#182532;background:#f4f7f9;}
header {border-bottom:2px solid #c5d2dc} article {background:white;border:1px solid #c5d2dc;border-radius:.45rem;padding:1rem;margin:1rem 0}
h1,h2 {margin:.5rem 0} h2 {font-size:1.28rem} small {color:#475767;overflow-wrap:anywhere}
.inputs {display:flex;flex-wrap:wrap;gap:.8rem;margin:.55rem 0} input,select,button {font:inherit} [data-seconds] {width:5rem}
summary {cursor:pointer;padding:.4rem 0} details {border-top:1px solid #dce4e9} pre {overflow:auto;color:#314e5f}
.crop {position:relative;display:table;width:100%} .crop img {width:100%;display:block} .highlight {position:absolute;border:2px solid #c02d12;pointer-events:none}
a {color:#00598b} .targets {overflow-wrap:anywhere} td {padding:.25rem .6rem} [role=status] {margin-left:.7rem} [hidden] {display:none}
"""
_SCRIPT = r"""
const cards = Array.from(document.querySelectorAll("[data-card]"));
for (const card of cards) {
  const disposition = card.querySelector("[data-disposition]");
  const role = card.querySelector("[data-role]");
  disposition.onchange = () => {
    role.disabled = disposition.value !== "ASSIGNED";
    if (role.disabled) role.value = "";
  };
}
document.querySelector("#filter").oninput = ({target}) => {
  const query = target.value.trim().toLowerCase();
  for (const card of cards) card.hidden = !card.textContent.toLowerCase().includes(query);
};
const csvCell = value => '"' + value.replaceAll('"', '""') + '"';
document.querySelector("#save").onclick = () => {
  const reviewer = document.querySelector("#reviewer").value.trim();
  const status = document.querySelector("#status");
  const output = [["card_id", "evidence_sha256", "disposition", "variable", "reviewer", "seconds"]];
  for (const card of cards) {
    const disposition = card.querySelector("[data-disposition]").value;
    const variable = card.querySelector("[data-role]").value;
    const seconds = card.querySelector("[data-seconds]");
    if (!disposition) continue;
    if (!/^[\p{L}\p{N}_][\p{L}\p{N}_ .-]{0,79}$/u.test(reviewer)
        || (disposition === "ASSIGNED" && !variable) || !seconds.checkValidity()) {
      status.textContent = "請填寫標註者、指定量別與有效秒數。";
      return;
    }
    output.push([card.dataset.card, card.dataset.sha, disposition, variable, reviewer, seconds.value]);
  }
  if (output.length === 1) {
    status.textContent = "目前沒有已填判定。";
    return;
  }
  const csv = output.map(row => row.map(csvCell).join(",")).join("\n") + "\n";
  const url = URL.createObjectURL(new Blob([csv], {type:"text/csv;charset=utf-8"}));
  const download = Object.assign(document.createElement("a"), {href:url, download:"column-labels.csv"});
  download.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  status.textContent = "已匯出 " + (output.length - 1) + " 筆標註。";
};
"""


def _html(queue):
    cards = "".join(map(_card_view, queue["cards"]))
    targets = "".join(
        f'<tr id="{escape(target["project_id"])}-{escape(target["column_id"])}">'
        f'<td>{escape(target["project_id"])}</td><td>{escape(target["column"])}</td>'
        f'<td>{escape(target["state"])}</td><td>{len(target["evidence"])}</td></tr>' for target in queue["targets"]
    )
    summary = queue["summary"]
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>原件欄義確認表</title><style>{_STYLE}</style></head>
<body><header><h1>原件欄義確認表</h1><p>{summary["unique_cards"]} 個欄位包，涵蓋 {summary["target_cells"]} 個 Census 目標欄格。</p>
<p>展開可檢查表頭、列與原件定位。標註僅表示印刷欄義，科學用途由原研究流程另行審查。</p>
<div class="inputs"><label>篩選欄位或專案 <input id="filter" type="search"></label>
<label>標註者 <input id="reviewer" autocomplete="off"></label><button id="save">匯出 CSV</button><span id="status" role="status"></span></div>
</header><main>{cards}
<h2>目標欄格清冊</h2><table><thead><tr><th>Project</th><th>Census 欄位</th><th>證據用途</th><th>連結證據數</th></tr></thead>
<tbody>{targets}</tbody></table></main><script>{_SCRIPT}</script></body></html>
"""


def _read_card_index(queue):
    if queue.get("schema") != QUEUE or queue.get("purpose") != PURPOSE:
        raise ValueError("Labels must target a resource column review packet")
    cards = {}
    for card in queue["cards"]:
        key = card["card_id"]
        if not re.fullmatch(r"[0-9a-f]{64}", key) or key in cards or card["evidence_sha256"] != _key(
            {k: v for k, v in card.items() if k != "evidence_sha256"}
        ):
            raise ValueError("Invalid or duplicate review card evidence fingerprint")
        cards[key] = card
    return cards


def _annotation(row, cards, used):
    if set(row) != set(LABEL_FIELDS) or any(value is None for value in row.values()):
        raise ValueError("Malformed review label row")
    identity = row["card_id"]
    card = cards.get(identity)
    if card is None or card["evidence_sha256"] != row["evidence_sha256"] or identity in used:
        raise ValueError("Review label refers to a duplicate, unknown or changed evidence card")
    used.add(identity)
    disposition = row["disposition"]
    if not disposition and not any(row[k] for k in ("variable", "reviewer", "seconds")):
        return None
    if disposition not in DISPOSITIONS or not re.fullmatch(r"[\w][\w .-]{0,79}", row["reviewer"]):
        raise ValueError("Review label requires a disposition and reviewer identifier")
    role = row["variable"]
    if (disposition == "ASSIGNED" and role not in ROLES) or (disposition != "ASSIGNED" and role):
        raise ValueError("Assigned column role requires a supported original variable")
    seconds = row["seconds"]
    if seconds and not re.fullmatch(r"\d{1,7}(?:\.\d{1,3})?", seconds):
        raise ValueError("Annotation duration must be finite nonnegative seconds")
    return {**row, "source_sha256": card["source_sha256"], "physical_page": card["physical_page"],
            "witnesses": [w["candidate_id"] for w in card["witnesses"]],
            "affected_census_cells": card["linked_targets"], "purpose": PURPOSE}


def import_labels(queue, queue_pin, labels, out):
    packet = _pinned(queue, queue_pin)
    cards = _read_card_index(packet)
    used, entries = set(), []
    with labels.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != LABEL_FIELDS:
            raise ValueError("Labels CSV must use the exported fields")
        for row in reader:
            if entry := _annotation(row, cards, used):
                entries.append(entry)
    entries.sort(key=lambda item: item["card_id"])
    seconds = [float(e["seconds"]) for e in entries if e["seconds"]]
    result = {"schema": ANNOTATIONS, "purpose": PURPOSE, "queue_sha256": queue_pin,
              "labels_sha256": _digest(labels.read_bytes()), "annotations": entries,
              "counts": dict(Counter(e["disposition"] for e in entries)), "measured_durations": len(seconds),
              "median_seconds": statistics.median(seconds) if seconds else None}
    out.parent.mkdir(parents=True, exist_ok=True)
    _write(out, result)
    return {key: value for key, value in result.items() if key != "annotations"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="operation", required=True)
    build = operations.add_parser("prepare", help="Prepare the local review packet")
    build.add_argument("--funnel", nargs=2, metavar=("PATH", "SHA256"), required=True)
    build.add_argument("--candidate-sqlite", type=Path, required=True)
    build.add_argument("--store", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    load = operations.add_parser("import", help="Validate a batch of original-column labels")
    load.add_argument("--queue", nargs=2, metavar=("PATH", "SHA256"), required=True)
    load.add_argument("--labels", type=Path, required=True)
    load.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "prepare":
        result = prepare(*args.funnel, args.candidate_sqlite, args.store, args.out)
    else:
        result = import_labels(*args.queue, args.labels, args.out)
    print(canonical_json(result))


if __name__ == "__main__":
    main()
