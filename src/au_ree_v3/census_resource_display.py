"""Project exact issuer resource statements into Census display facts."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
import json
import re
from typing import TYPE_CHECKING

from .census_display import DisplayFact
from .documents import DocumentCache
from .normalization import reported_interval
from .observations import NUMBER, digest, exact_value
from .resource_context import BoundStatement, Measurement, bind_context
from .resource_extraction import _chemical_species, _date_present, _flatten
from .resource_sources import ResourceSourceReader, plan_source_editions

if TYPE_CHECKING:
    from pathlib import Path

RULE = "exact-selected-resource-display-v1"
ROLE = "REPORTED_RESOURCE"
UNKNOWN = "UNKNOWN_CONTEXT"
RESOURCE = ("ore_mt", "grade", "contained_treo_kt")
_FIELDS = (
    "resource_jorc_class_as_reported", "resource_estimate_effective_date", "resource_cutoff_as_reported",
    "resource_scope_or_deposit_name", "ree_grade_or_chemistry_basis",
)
_CATEGORY_NAMES = {
    "MEASURED": "M", "INDICATED": "Ind", "INFERRED": "Inf", "M": "M", "IND": "Ind", "INF": "Inf",
}
_CATEGORY_TOKEN = r"(?:Measured|Indicated|Inferred|M|Ind|Inf)"
_CATEGORY_LIST = re.compile(rf"\b{_CATEGORY_TOKEN}(?:\s*(?:,\s*(?:and\s+)?|[+/&]|and\s+)\s*{_CATEGORY_TOKEN}){{1,2}}\b", re.I)
_CATEGORY_ROW = re.compile(rf"^\s*({_CATEGORY_TOKEN})\s{{2,}}(?=.*\d)", re.I)
_PRINTED_TOTAL = re.compile(r"^\s*(?P<label>(?:Grand |Global |Combined )?Total)\b", re.I)
_SCOPE = re.compile(r"(?<![A-Za-z])Mineral Resource(?:s| Estimate| Statement)?\b", re.I)
_NON_RESOURCE = re.compile(r"\b(?:Ore Reserve|Exploration Target)\b", re.I)
_TABLE_BOUNDARY = re.compile(r"^\s*(?:Table|Figure)\s+\d+\b", re.I)
_CHEMISTRY = re.compile(r"\b(?:In[ -]Situ\s+)?TREO(?:\s*(?:[+\-−]|including|excluding)\s*(?:Y2O3|CeO2|Sc2O3|Y|Ce|Sc))?(?:\s+Grade)?\b", re.I)
_DEFINITION = re.compile(r"\bTREO\s*(?:=|(?:means|includes|is|and\s+MREO\s+estimates?\s+were)\b)", re.I)
_PAGE_WATERMARK = "for personal use only"
_ANALYTE_CONTINUATION = re.compile(r"\s*(?:[+−-]\s*\w|including\b|excluding\b)", re.I)


@dataclass(frozen=True, slots=True)
class _Phrase:
    page: int
    locator: str
    text: str


@dataclass(frozen=True, slots=True)
class _Reported:
    value: str
    token: str
    phrase: _Phrase
    unit: str = ""


def _report_inputs(receipt, observations):
    views = receipt.get("science_views", {})
    release = receipt.get("release_id")
    if (receipt.get("status") != "PASS" or receipt.get("release_mode") != "FORMAL"
            or views.get("release_id") != release or views.get("release_mode") != "FORMAL"
            or views.get("research_cutoff") != receipt.get("research_cutoff")):
        raise ValueError("Resource display requires a verified governed Census receipt")
    indexed = {}
    for entry in observations:
        if entry.get("selected_for_census") is not True or entry.get("outcome") != "VALIDATED":
            continue
        key = entry["observation_id"]
        if key in indexed:
            raise ValueError("Resource display observations repeat a selected identity")
        indexed[key] = entry
    reports = []
    for name, expected in (("verified_resource_presence", set(RESOURCE)),
                           ("scoped_reported_resources", set(RESOURCE[:2]))):
        for project, report in sorted(views.get(name, {}).items()):
            statement = report["statement"]
            joins = []
            for line in report["inputs"]:
                observation = indexed.get(line["observation_id"], {})
                decision = line.get("temporal_decision", {})
                if (line.get("release_id") != release or line.get("project_id") != project
                        or decision.get("status") != "ELIGIBLE" or decision.get("cutoff") != receipt["research_cutoff"]
                        or not _exact_observation(statement, line, observation)):
                    break
                joins.append((line, observation))
            if len(joins) != len(expected) or {entry["variable"] for _, entry in joins} != expected:
                raise ValueError(f"Resource display receipt does not join its exact selected observations for {project}")
            reports.append((project, statement, tuple(joins)))
    if len({(project, value["statement_id"]) for project, value, _ in reports}) != len(reports):
        raise ValueError("Resource display receipt repeats a selected statement")
    return reports


def _exact_observation(statement, lineage, observation):
    same = ("scope_id", "statement_id", "source_sha256", "effective_date")
    association = (
        ("raw_token", "raw_value"), ("raw_unit", "raw_unit"), ("physical_page", "page"),
        ("normalized_value", "value"), ("normalized_unit", "unit"), ("table", "table"), ("row", "row"), ("column", "column"),
    )
    return (all(observation.get(key) == statement.get(key) == lineage.get(key) for key in same)
            and observation.get("project_id") == lineage["project_id"]
            and lineage.get("selection_id") == digest(["fresh-resource-selection-v1", lineage["release_id"], lineage["validation_id"]])
            and all(lineage[left] == observation.get(right) for left, right in association))


def _recheck_statement(bound: BoundStatement, statement: dict, joins) -> dict[str, Measurement] | None:
    if bound.scope.name != statement.get("scope") or bound.decision.status != "ELIGIBLE":
        return None
    by_id = {item.raw.observation_id: item for item in bound.measurements}
    verified = {}
    for lineage, expected in joins:
        measured = by_id.get(expected["observation_id"])
        if (measured is None or measured.validation.outcome != "VALIDATED"
                or measured.validation.validation_id != lineage["validation_id"]
                or measured.anchor.anchor_id != lineage["anchor_id"]
                or bound.decision.decision_id != lineage["temporal_decision"]["decision_id"]
                or bound.source.url != lineage["source_url"]
                or any(expected.get(key) != value for key, value in measured.science().items())):
            return None
        verified[expected["variable"]] = measured
    return verified


def _original_phrase(pdf, page, quote):
    if not quote or type(page) is not int or not 1 <= page <= len(pdf.pages):
        return None
    text = pdf.locate(page).text
    lines = text.splitlines()
    offsets = []
    fragments = []
    start = 0
    for line, raw in enumerate(lines, start=1):
        if printed := _flatten(raw):
            offsets.append((start, line))
            fragments.append(printed)
            start += len(printed) + 1
    native = " ".join(fragments)
    matches = list(re.finditer(re.escape(_flatten(quote)), native, re.I))
    if len(matches) != 1:
        return None
    match, = matches
    positions = [offset for offset, _ in offsets]
    first = offsets[bisect_right(positions, match.start()) - 1][1]
    last = offsets[bisect_right(positions, match.end() - 1) - 1][1]
    locator = f"PDF page {page}, line {first}" if first == last else f"PDF page {page}, lines {first}-{last}"
    return _Phrase(page, locator, match[0])


def _statement_phrases(pdf, bound, measurements, binding):
    grade = measurements["grade"]
    native = json.loads(grade.raw.original_payload).get("native_table_binding") or {}
    disconnected = {item["quote"] for item in native.get("context_links_not_established", ())}
    # Statement context has already been linked by resource_context. Retain
    # exact source text and exclude any independently marked disjoint quote.
    table_header = binding.get("table", {}).get("header")
    proposals = ((grade.anchor.page, grade.anchor.table), (grade.anchor.page, table_header), *bound.statement.context,
                 *((cell.anchor.page, cell.anchor.quote) for cell in measurements.values()))
    return tuple(dict.fromkeys(
        original for page, quote in proposals if quote not in disconnected
        and (original := _original_phrase(pdf, page, quote)) is not None
    ))


def _names(category):
    return tuple(_CATEGORY_NAMES[name] for name in (category or "").split("_") if name in _CATEGORY_NAMES)


def _printed_classes(text):
    return tuple(_CATEGORY_NAMES[match[0].upper()] for match in re.finditer(rf"\b{_CATEGORY_TOKEN}\b", text, re.I))


def _explicit_class(phrases, expected):
    for phrase in phrases:
        for match in _CATEGORY_LIST.finditer(phrase.text):
            if set(_printed_classes(match[0])) == set(expected):
                return _Reported(match[0], match[0], phrase)
    return None


def _class_rows(pdf, cell, expected):
    quote = cell.anchor.quote
    page = cell.anchor.page
    total = _PRINTED_TOTAL.match(quote or "")
    located = _original_phrase(pdf, page, quote)
    if not located or not total or not located.locator.rsplit(", ", 1)[-1].startswith("line "):
        return None
    last = int(located.locator.rsplit(" ", 1)[-1])
    native = pdf.locate(page).text.splitlines()
    known = []
    first = last
    for line in range(last - 1, max(0, last - 14), -1):
        row = native[line - 1]
        if not row.strip() or row.strip().casefold() == _PAGE_WATERMARK:
            continue
        if not (match := _CATEGORY_ROW.match(row)):
            break
        known.append(match[1])
        first = line
    if len(known) != len(expected) or set(_printed_classes(" ".join(known))) != set(expected):
        return None
    phrase = _Phrase(page, f"PDF page {page}, lines {first}-{last}", "\n".join(native[first - 1:last]))
    return _Reported(" + ".join(reversed(known)) + f" ({total['label']})", total["label"], phrase)


def _resource_class(phrases, pdf, measured, expected):
    if not expected:
        return None
    exact = _explicit_class(phrases, expected)
    if exact:
        return exact
    return next((value for cell in measured.values() if (value := _class_rows(pdf, cell, expected))), None)


def _effective_date(phrases, effective):
    if not effective:
        return None
    parsed = date.fromisoformat(effective + "-01" if len(effective) == 7 else effective)
    month_year = rf"\b{parsed:%B},?\s+{parsed.year}\b"
    pattern = month_year if len(effective) == 7 else rf"\b{parsed.day}\s+{month_year}"
    for phrase in phrases:
        if _date_present(phrase.text, effective, effective=True) and (found := re.search(pattern, phrase.text, re.I)):
            return _Reported(effective, found[0], phrase)
    return None


def _linked_effective_date(pdf, statement, measured, link):
    """Use a reviewed cover date only for its exact re-read resource statement."""
    effective = statement.effective_date
    marker = str(link.get("cover_footnote", ""))
    if (link.get("source_sha256") != pdf.source_sha256 or not effective or len(effective) != 10
            or re.fullmatch(r"[1-9]", marker) is None
            or statement.cutoff_analyte != "TREO" or not statement.cutoff or not statement.cutoff_unit):
        return None
    cover, table, footnote = (
        _original_phrase(pdf, link.get(role, {}).get("page"), link.get(role, {}).get("quote"))
        for role in ("cover", "statement", "cutoff_footnote")
    )
    if not all((cover, table, footnote)) or cover.page == table.page or footnote.page != cover.page:
        return None
    ore, grade = measured["ore_mt"], measured["grade"]
    if {ore.anchor.page, grade.anchor.page} != {table.page} or grade.raw.raw_unit != "%":
        return None
    if ore.raw.raw_unit not in ("Mt", "million tonnes"):
        return None
    amount = re.escape(ore.raw.raw_value)
    percentage = re.escape(grade.raw.raw_value)
    cutoff = rf"{re.escape(statement.cutoff)}\s*{re.escape(statement.cutoff_unit)}\s+TREO"
    roles = (cover.text, table.text, footnote.text)
    if any(not _SCOPE.search(text) or _NON_RESOURCE.search(text) for text in roles):
        return None
    if (not re.search(rf"\bto\s+{amount}\s+Mt\s+at\s+{percentage}%\s+Total Rare Earth Oxide", cover.text, re.I)
            or not re.search(rf"\b{amount}\s+million tonnes\s+at an average grade of\s+{percentage}%", table.text, re.I)
            or not re.search(rf"cut[- ]off grade of\s+{cutoff}\b", table.text, re.I)
            or not re.search(rf"^{marker}\s+Mineral Resource cut[- ]off grade of\s+{cutoff}\b", footnote.text, re.I)):
        return None
    parsed = date.fromisoformat(effective)
    printed = rf"{parsed.day}\s+{parsed:%B}\s+{parsed.year}"
    found = re.search(rf"\bfrom\s+\d{{1,2}}\s+[A-Za-z]+\s+\d{{4}}\s+to\s+(?P<date>{printed}){marker}\b", cover.text, re.I)
    if found is None:
        return None
    return _Reported(effective, found["date"], cover), {
        "effective_date": effective, "effective_date_precision": "DAY",
        "effective_date_role": "SAME_EDITION_LINKED_ESTIMATE_DATE",
        "date_link_statement_locator": table.locator, "date_link_cutoff_locator": footnote.locator,
    }


def _cutoff(phrases, statement, binding):
    if not (statement.cutoff and statement.cutoff_unit and statement.cutoff_analyte):
        return None
    supplied = binding.get("cutoff", {}).get("reported")
    units = re.escape(statement.cutoff_unit)
    pattern = re.escape(supplied) if supplied else rf"(?P<value>{NUMBER})\s*{units}\s+{re.escape(statement.cutoff_analyte)}\b"
    for phrase in phrases:
        if "cut" not in phrase.text.casefold() and not supplied:
            continue
        for found in re.finditer(pattern, phrase.text, re.I):
            token = re.search(NUMBER, found[0])
            if (token and not _ANALYTE_CONTINUATION.match(phrase.text, found.end())
                    and exact_value(token[0]) == exact_value(statement.cutoff)):
                return _Reported(found[0], token[0], phrase, statement.cutoff_unit)
    return None


def _scope(phrases, effective):
    scoped = tuple(phrase for phrase in phrases if _SCOPE.search(phrase.text) and not _NON_RESOURCE.search(phrase.text)
                   and len(phrase.text) <= 240)
    if scoped:
        dated = [item for item in scoped if _date_present(item.text, effective, effective=True)]
        preferred = dated or scoped
        phrase = next((item for item in preferred if _TABLE_BOUNDARY.match(item.text)), preferred[0])
        return _Reported(phrase.text, phrase.text, phrase)
    return None


def _chemistry(phrases, pdf, grade, label):
    payload = json.loads(grade.raw.original_payload)
    header = (payload.get("native_table_binding") or {}).get("header_anchor")
    if header and (match := _CHEMISTRY.search(header["text"])):
        original = pdf.locate(header["physical_page"], line=header["line"], column=header["start_column"],
                              end_column=header["end_column"])
        if original.text == header["text"]:
            return _Reported(original.text, match[0], _Phrase(header["physical_page"], original.locator, original.text))
    if label and label.casefold() != "treo":
        for phrase in phrases:
            match = re.search(re.escape(label), phrase.text, re.I)
            if match and not _ANALYTE_CONTINUATION.match(phrase.text, match.end()):
                return _Reported(match[0], match[0], phrase)
    for phrase in phrases:
        definition = _DEFINITION.search(phrase.text)
        if definition and (match := _CHEMISTRY.match(phrase.text, definition.start())):
            return _Reported(match[0], match[0], phrase)
    return None


def _reported_amount(measured):
    raw = measured.raw
    payload = json.loads(raw.original_payload)
    native = payload.get("native_table_binding") or {}
    if interval := native.get("raw_rounding_interval"):
        low, high = interval
    else:
        _, low, high = reported_interval(raw.raw_value)
    return {"token": raw.raw_value, "unit": raw.raw_unit,
            "interval_as_reported": f"{low} to {high}", "precision_mode": "SOURCE_TOKEN_ROUNDING_INTERVAL"}


def _fact_context(bound, measured, edition, grade, cutoff, category_token, effective, chemistry):
    statement = bound.statement
    members = statement.treo_members or _chemical_species(tuple(text for _, text in statement.context))
    context = {
        "display_rule": RULE, "source_kind": bound.source.kind, "source_role": "ISSUER_PDF", "as_reported_at": bound.source.publication_date,
        "temporal_decision_id": bound.decision.decision_id, "temporal_status": "ELIGIBLE",
        "effective_date": effective.value if effective else UNKNOWN,
        "effective_date_precision": {7: "MONTH", 10: "DAY"}.get(len(effective.value), UNKNOWN) if effective else UNKNOWN,
        "effective_date_role": ("ESTIMATE_MONTH_FROM_STATEMENT" if len(effective.value) == 7 else
                                "EFFECTIVE_DATE_FROM_STATEMENT") if effective else UNKNOWN,
        "statement_id": bound.statement_id, "scope_id": bound.scope.scope_id,
        "jorc_category": (statement.category or UNKNOWN) if not category_token else UNKNOWN,
        "reporting_standard": json.loads(grade.raw.original_payload).get("reporting_standard") or UNKNOWN,
        "cutoff_token": cutoff.token if cutoff else UNKNOWN, "cutoff_unit": statement.cutoff_unit or UNKNOWN,
        "cutoff_analyte": statement.cutoff_analyte or UNKNOWN,
        "material_basis": grade.raw.material_basis or UNKNOWN, "denominator_basis": grade.raw.denominator_basis or UNKNOWN,
        "chemical_label": chemistry.token if chemistry else UNKNOWN, "treo_members": ", ".join(members) or UNKNOWN,
        "measurement_observation_ids": ", ".join(sorted(cell.raw.observation_id for cell in measured.values())),
        "grade_anchor_id": grade.anchor.anchor_id,
        "source_edition_id": edition["edition_id"], "capture_time": edition["event_acquired_at"],
    }
    if category_token:
        context["category_state"] = "SOURCE_ROW_AND_GOVERNED_CATEGORY_DIFFER"
        context["source_category_token"] = category_token
    native = json.loads(grade.raw.original_payload).get("native_table_binding") or {}
    if parent := native.get("parent_header_anchor"):
        context["native_parent_header"] = parent["text"]
        context["native_parent_locator"] = parent["locator"]
    for variable, cell in measured.items():
        for key, value in _reported_amount(cell).items():
            context[f"reported_{variable}_{key}"] = value
    return context


def _category_review(project, bound, edition, grade, pdf, context):
    phrase = _original_phrase(pdf, grade.anchor.page, grade.anchor.quote)
    if phrase is None:
        return None
    return {
        "project_id": project, "data_role": ROLE, "state": "SCIENTIFIC_CONFLICT",
        "label": "Reported JORC resource category", "reason": "SOURCE_ROW_AND_GOVERNED_CATEGORY_DIFFER",
        "source_sha256": bound.source.sha256, "source_capture_sha256": edition["receipt_sha256"],
        "source_url": bound.source.url, "publication_date": bound.source.publication_date,
        "locator": phrase.locator, "quote": phrase.text, "scope": bound.scope.name,
        "context": {**context, "physical_page": str(phrase.page)},
    }


def _facts_for(project, bound, measured, capture, edition, binding, diagnostics):
    grade = measured["grade"]
    statement = bound.statement
    phrases = _statement_phrases(capture.pdf, bound, measured, binding)
    classes = _names(statement.category)
    source_row_class = _CATEGORY_LIST.search(grade.anchor.quote or "")
    category_conflict = source_row_class and set(_printed_classes(source_row_class[0])) != set(classes)
    cutoff = _cutoff(phrases, statement, binding)
    effective = _effective_date(phrases, statement.effective_date)
    chemistry = _chemistry(phrases, capture.pdf, grade, statement.reported_chemical_label)
    link = binding.get("display_date_link")
    linked = _linked_effective_date(capture.pdf, statement, measured, link) if effective is None and link else None
    reported = (
        None if category_conflict else _resource_class(phrases, capture.pdf, measured, classes),
        effective if linked is None else linked[0], cutoff, _scope(phrases, statement.effective_date), chemistry,
    )
    context = _fact_context(bound, measured, edition, grade, cutoff, source_row_class[0] if category_conflict else None,
                            effective, chemistry)
    for field_id, value in zip(_FIELDS, reported, strict=True):
        field_context = {**context, **linked[1]} if linked is not None and field_id == _FIELDS[1] else context
        if value is None:
            conflict = category_conflict and field_id == _FIELDS[0]
            diagnostic = {"project_id": project, "statement_id": bound.statement_id, "field_id": field_id,
                          "status": "SCIENTIFIC_CONFLICT" if conflict else "EVIDENCE_INSUFFICIENT",
                          "reason": "SOURCE_ROW_AND_GOVERNED_CATEGORY_DIFFER" if conflict else
                          "EXACT_SAME_STATEMENT_CONTEXT_NOT_REBOUND"}
            if conflict and (review := _category_review(project, bound, edition, grade, capture.pdf, context)):
                diagnostic["review_evidence"] = review
            diagnostics.append(diagnostic)
            continue
        yield DisplayFact(
            project_id=project, field_id=field_id, value=value.value, raw_token=value.token, raw_unit=value.unit,
            source_sha256=bound.source.sha256, source_capture_sha256=edition["receipt_sha256"],
            source_url=bound.source.url, publication_date=bound.source.publication_date,
            locator=value.phrase.locator, quote=value.phrase.text, scope=bound.scope.name,
            subject_id=bound.statement_id, data_role=ROLE, context={**field_context, "physical_page": str(value.phrase.page)},
        )


def reported_resource_facts(census_receipt: dict, observations, source_manifest: dict, *, assets: Path,
                            bindings: dict, manifest_sha: str, cache: DocumentCache | None = None,
                            diagnostics: list[dict] | None = None) -> tuple[DisplayFact, ...]:
    """Rebind proof-pinned observations from original bytes for display only."""
    diagnostics = [] if diagnostics is None else diagnostics
    reports = _report_inputs(census_receipt, observations)
    expected = {(project, statement["statement_id"]): (statement, joins) for project, statement, joins in reports}
    needed = {(project, statement["source_sha256"]) for project, statement, _ in reports}
    plan = plan_source_editions(bindings, source_manifest, manifest_sha)
    if plan.cutoff != census_receipt["research_cutoff"]:
        raise ValueError("Resource display has a different exact source cutoff")
    plan = replace(plan, matches=[match for match in plan.matches if (match[2].entity_id, match[1]["sha256"]) in needed])
    reader = ResourceSourceReader(assets, plan, cache if cache is not None else DocumentCache())
    candidates = defaultdict(list)
    blocked = {}
    for binding, edition, entity in plan.matches:
        case, capture = reader.read(binding, edition, entity)
        if capture is None:
            blocked[(entity.entity_id, edition["sha256"])] = (case.get("outcome", "SOURCE_UNAVAILABLE"),
                                                               case.get("reason", "EXACT_SOURCE_UNAVAILABLE"))
            continue
        for bound in bind_context(binding, entity, capture, case, cutoff=plan.cutoff, rules_sha=plan.rules_sha):
            key = entity.entity_id, bound.statement_id
            if key in expected and (measured := _recheck_statement(bound, *expected[key])):
                candidates[key].append((bound, measured, capture, edition, binding))
    result = []
    for key, (statement, _) in sorted(expected.items()):
        if len(candidates[key]) == 1:
            result.extend(_facts_for(key[0], *candidates[key][0], diagnostics))
            continue
        status, reason = (("SCIENTIFIC_CONFLICT", "AMBIGUOUS_CAPTURED_STATEMENT") if len(candidates[key]) > 1 else
                          blocked.get((key[0], statement["source_sha256"]),
                                      ("EVIDENCE_INSUFFICIENT", "GOVERNED_STATEMENT_OR_EDITION_NOT_REBOUND")))
        diagnostics.append({"project_id": key[0], "statement_id": key[1], "status": status, "reason": reason})
    return tuple(result)
