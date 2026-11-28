"""Read source-bound geological classifications and dated milestones from native layouts."""

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .contracts import ContractViolation, EvidenceAnchor, canonical_json
from .development import reported_calendar_date
from .documents import DocumentError

SCHEMA = "native-attribute-routes-v1"
POLICY_VERSION = "native-census-attribute-selection-v1"
DEPOSIT_TYPES = {
    "ALLUVIAL_REE": ("沖積型稀土礦床", r"Alluvial REE deposit which is hosted by alluvial fans[^.]+\."),
    "CARBONATITE_HOSTED_REO": ("碳酸岩寄主型稀土礦床", r"The [^.]{1,60} REO deposit occurs within the [^.]{1,75} carbonatite complex\b"),
    "CLAY_REGOLITH_HOSTED_REO": ("黏土風化殼寄主型稀土礦床", r"The deposit type is mixed clay regolith-hosted REO mineralisation overlying the [^.]+\."),
    "HEAVY_MINERAL_STRANDLINE": ("海灘岸線型重礦物砂礦床", r"The [^.]{1,45} deposit is a beach deposited HM strandline hosted within [^.]+\."),
    "OFFSHORE_HEAVY_MINERAL_SANDS": ("離岸沉積型重礦物砂礦床", r"The heavy mineral sands at the [^.]{1,60} Project is a fine-grained deposit hosted within the offshore depositional paleo-environment of [^.]+\."),
}
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_PUBLICATION = re.compile(rf"\b(?P<day>[1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)? (?P<month>{_MONTH}) (?P<year>[12][0-9]{{3}})\b", re.I)
_DATE = rf"(?:(?:[1-3]?[0-9] )?{_MONTH} )?[12][0-9]{{3}}"
_STUDY = r"(?P<stage>Pre[- ]Feasibility Study|Definitive Feasibility Study|PFS|DFS)"
_COMPLETE = re.compile(
    rf"\b{_STUDY}(?:(?: on| for) the [^,.;]{{1,90}} Project| based on (?:a|the) [^,.;]{{1,95}},)?"
    rf"(?: was| has been)? completed"
    rf" (?:in|on) (?P<date>{_DATE})\b", re.I,
)
_QUALIFIED = re.compile(r"\b(?:not|never|yet|only|phase|stage|pilot|would|will|planned|expected|if|subject to)\b", re.I)
_TENTATIVE = re.compile(r"\b(?:thought|interpreted|being investigated|targeting|possible|may|might|could)\b", re.I)
_ADJACENT = re.compile(r"\bas defined at the Company[’']s\b", re.I)
_HEADER = re.compile(r"^\s*Criteria {2,}JORC Code Explanation {2,}Commentary\s*$", re.I)
_HISTORY = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+History\s*$", re.I)
_NUMBERED_HEADING = re.compile(r"^\s*\d+(?:\.\d+)+\.? {2,}\S")
_KINDS = {"JORC_CLASSIFICATION": "DEPOSIT_TYPE", "JORC_STUDY_STATUS": "DEVELOPMENT_STAGE",
          "PROJECT_HISTORY": "DEVELOPMENT_STAGE"}
_ROWS = {"JORC_CLASSIFICATION": "Geology", "JORC_STUDY_STATUS": "Study Status"}
POLICY_SHA = sha256(canonical_json({
    "version": POLICY_VERSION, "scope": "CENSUS_DISPLAY_ONLY", "route_schema": SCHEMA,
    "deposit_classifications": DEPOSIT_TYPES, "routes": _KINDS, "rows": _ROWS, "native_header": _HEADER.pattern,
    "calendar_date": _PUBLICATION.pattern, "completed_event": _COMPLETE.pattern,
    "disqualifying_study_terms": _QUALIFIED.pattern, "tentative_geology_terms": _TENTATIVE.pattern,
    "adjacent_geology": _ADJACENT.pattern, "history_heading": _HISTORY.pattern,
    "next_heading": _NUMBERED_HEADING.pattern,
}).encode()).hexdigest()


@dataclass(frozen=True)
class NativeRegion:
    page: int
    start_line: int
    end_line: int
    column: int = 1

    def __post_init__(self):
        if (any(type(value) is not int or value < 1 for value in (self.page, self.start_line, self.end_line, self.column))
                or self.start_line > self.end_line):
            raise ContractViolation("Native region needs positive physical line and column bounds")

    def lines(self, document):
        lines = document.locate(self.page).text.splitlines()
        if self.end_line > len(lines):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_NATIVE_REGION_OUT_OF_BOUNDS")
        return lines[self.start_line - 1:self.end_line]

    def text(self, document):
        lines = self.lines(document)
        return " ".join(" ".join(line[self.column - 1:] for line in lines).split())

    def anchor(self, document, key, quote, role, *, row=None):
        view = self.text(document)
        start = view.find(quote)
        if not quote or start < 0 or view.count(quote) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_QUOTE_NOT_UNIQUE_IN_LAYOUT_REGION")
        span = {"view": "NATIVE_PDF_LAYOUT_REGION", "start_line": self.start_line, "end_line": self.end_line,
                "start_column": self.column, "start": start, "end": start + len(quote)}
        identity = sha256(canonical_json([SCHEMA, key, document.source_sha256, self.page, span, quote, role]).encode())
        locator = (f"PDF page {self.page}, lines {self.start_line}–{self.end_line}, "
                   f"column {self.column} onward, folded characters {start}–{start + len(quote)}")
        return EvidenceAnchor(anchor_id=identity.hexdigest(), source_key=key, source_sha=document.source_sha256,
                              page=self.page, row=row, column="Commentary" if row else None,
                              quote=quote, locator=locator, text_span=span, role=role)


@dataclass(frozen=True)
class BatchClaim:
    term: str
    role: str
    scope: str
    scope_kind: str
    publication_date: str
    event_date: str | None
    event_token: str | None
    anchors: tuple[EvidenceAnchor, ...]


class NativeClaimDeclined(DocumentError):
    def __init__(self, outcome, reason, anchors):
        super().__init__(outcome, reason)
        self.anchors = anchors


def load_routes(data, manifest_sha):
    routes = json.loads(data)
    if routes["schema"] != SCHEMA or routes["source_manifest_sha256"] != manifest_sha:
        raise ContractViolation("Native attribute batch belongs to another source manifest")
    bindings = [(_KINDS[adapter["adapter"]], {**source, **region, "adapter": adapter["adapter"]})
                for adapter in routes["batches"]
                for source in adapter["sources"] for region in source["regions"]]
    identities = {(kind, binding["receipt_sha256"], binding["page"]) for kind, binding in bindings}
    if len(identities) != len(bindings):
        raise ContractViolation("Duplicate native attribute review region")
    return [(kind, {**binding, "case_id": (
        f"native-{binding['adapter'].lower()}-{binding['project_id']}-{binding['page']}-{binding['receipt_sha256'][:12]}"
    )})
            for kind, binding in bindings]


def _local_scope(document, key, region, alias, role):
    matches = list(re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", region.text(document), re.I))
    if len(matches) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_NATIVE_SUBJECT_UNBOUND")
    return region.anchor(document, key, matches[0].group(), role)


def _cover_date(document, key, lines):
    view = NativeRegion(1, *lines)
    matches = list(_PUBLICATION.finditer(view.text(document)))
    if len(matches) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_ISSUER_PUBLICATION_DATE_UNBOUND")
    match = matches[0]
    date = " ".join(match.group(part) for part in ("day", "month", "year"))
    try:
        publication = reported_calendar_date(date)
    except ValueError as error:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_PUBLICATION_DATE_INVALID") from error
    return publication, view.anchor(document, key, match.group(), "ATTRIBUTE_ISSUER_PUBLICATION_DATE")


def _table_anchor(binding, document, key, region):
    row = _ROWS[binding["adapter"]]
    page = binding.get("row_page", region.page)
    lines = document.locate(page).text.splitlines()
    headers = [index for index, text in enumerate(lines, 1) if _HEADER.fullmatch(text)]
    if len(headers) != 1 or page not in (region.page, region.page - 1):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_JORC_TABLE_HEADER_UNBOUND")
    area = NativeRegion(page, binding["row_line"], binding["row_line"])
    line, = area.lines(document)
    label = re.match(rf"^\s*(?P<row>{row})\b", line, re.I)
    if not label or binding["row_line"] <= headers[0]:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_JORC_ROW_UNBOUND")
    if page == region.page:
        starts = [match.end() + 1 for match in re.finditer(r" {4,}(?=\S)", line[label.end():])]
        if (len(starts) < 2 or region.start_line != binding["row_line"]
                or region.column != label.end() + starts[-1]):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_JORC_COMMENTARY_COLUMN_UNBOUND")
        continuation = region.lines(document)[1:]
    else:
        next_page = document.locate(region.page).text.splitlines()
        repeated_headers = [(index, text) for index, text in enumerate(next_page, 1) if _HEADER.fullmatch(text)]
        if len(repeated_headers) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_JORC_TABLE_HEADER_UNBOUND")
        repeated_line, repeated_header = repeated_headers[0]
        if region.column != repeated_header.lower().index("commentary") + 1 or repeated_line >= region.start_line:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_JORC_COMMENTARY_COLUMN_UNBOUND")
        continuation = next_page[repeated_line:region.end_line]
    for text in continuation:
        if text[:label.end()].strip() not in ("", "For personal use only"):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_JORC_REGION_CROSSES_CRITERION")
    return area.anchor(document, key, label.group("row"), "ATTRIBUTE_JORC_CRITERION")


def _project_history(region, document, key, local):
    heading, *body = region.lines(document)
    body = [line.strip() for line in body if line.strip()]
    if not _HISTORY.fullmatch(heading) or any(_NUMBERED_HEADING.match(line) for line in body):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_NATIVE_HISTORY_HEADING_UNBOUND")
    if not (local and body and body[0].casefold().startswith(local.casefold() + " ")):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_HISTORY_SUBJECT_NOT_EXPLICIT")
    return region.anchor(document, key, " ".join(heading.split()), "ATTRIBUTE_HISTORY_HEADING")


def _geology(view):
    classified = [(term, match.group()) for term, (_, pattern) in DEPOSIT_TYPES.items()
                  for match in re.finditer(pattern, view, re.I)]
    if len(classified) != 1:
        return None
    return classified[0]


def _milestone(view):
    matches = list(_COMPLETE.finditer(view))
    if len(matches) != 1:
        raise DocumentError("CANDIDATE", "NO_UNIQUE_DATED_COMPLETION")
    match = matches[0]
    boundary = max(view.rfind(mark, 0, match.start()) for mark in (". ", "; ")) + 1
    completion = view[boundary:match.end()].strip()
    sentence_end = view.find(". ", match.end())
    if _QUALIFIED.search(view[boundary:len(view) if sentence_end < 0 else sentence_end]):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "PHASED_OR_CONDITIONAL_MILESTONE")
    stage = "PFS" if match.group("stage").upper().startswith(("PRE", "PFS")) else "DFS"
    return stage, completion, match.group("date")


def _review_role(binding, document, key, region, evidence, locality):
    view = region.text(document)
    adapter = binding["adapter"]
    row = _ROWS.get(adapter)
    if adapter == "JORC_CLASSIFICATION":
        if _ADJACENT.search(view):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REFERENCE_TO_ADJACENT_SCOPE")
        if _TENTATIVE.search(view):
            raise DocumentError("CANDIDATE", "TENTATIVE_GEOLOGICAL_INTERPRETATION")
        classified = _geology(view)
        if classified is None:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "NO_UNIQUE_CONFIRMED_DEPOSIT_TYPE")
        term, quote = classified
        if locality and locality.quote not in quote:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_NATIVE_SUBJECT_NOT_IN_CLASSIFICATION")
        proof = region.anchor(document, key, quote, "DEPOSIT_TYPE_CLAIM", row=row)
        return term, "CONFIRMED_DEPOSIT_TYPE", "NAMED_PROJECT_DEPOSIT", None, (proof, *evidence)
    term, quote, date = _milestone(view)
    if adapter == "JORC_STUDY_STATUS" and (not locality or locality.quote not in quote):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_NATIVE_SUBJECT_NOT_IN_STUDY")
    proof = region.anchor(document, key, quote, "DEVELOPMENT_STAGE_CLAIM", row=row)
    return term, "COMPLETED", "PROJECT", date, (proof, *evidence)


def read_native_claim(binding, edition, document):
    """Use structural source routes and one policy per evidence family."""
    if len(edition["project_ids"]) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_EDITION_HAS_MULTIPLE_PROJECT_SUBJECTS")
    key = edition["source_key"]
    region = NativeRegion(binding["page"], *binding["lines"], binding.get("column", 1))
    related = binding["subject"]
    subject_region = NativeRegion(related["page"], *related["lines"])
    subject = _local_scope(document, key, subject_region, related["alias"], "ATTRIBUTE_NATIVE_SUBJECT")
    publication, release = _cover_date(document, key, binding.get("publication_lines", (1, 10)))
    adapter = binding["adapter"]
    local = binding.get("local_subject")
    structure = (_table_anchor(binding, document, key, region) if adapter in _ROWS
                 else _project_history(region, document, key, local))
    locality = _local_scope(document, key, region, local, "ATTRIBUTE_LOCAL_SUBJECT") if local else None
    evidence = tuple(item for item in (subject, structure, locality, release) if item is not None)
    try:
        term, role, scope_kind, date, anchors = _review_role(binding, document, key, region, evidence, locality)
        event_date = reported_calendar_date(date)
    except DocumentError as error:
        original = region.anchor(document, key, region.text(document), "UNSELECTED_ATTRIBUTE_TEXT", row=_ROWS.get(adapter))
        raise NativeClaimDeclined(error.outcome, str(error), (original, *evidence)) from error
    except ValueError as error:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ATTRIBUTE_EVENT_DATE_INVALID") from error
    return BatchClaim(term, role, related["label"], scope_kind, publication, event_date, date, anchors)


def native_claim_matches(assertion, anchors):
    """Recheck serialized native claim syntax and evidence roles for a projected cell."""
    roles = {anchor.role for anchor in anchors}
    if not {"ATTRIBUTE_NATIVE_SUBJECT", "ATTRIBUTE_ISSUER_PUBLICATION_DATE"}.issubset(roles):
        return False
    if any(not isinstance(anchor.text_span, dict) or anchor.text_span.get("view") != "NATIVE_PDF_LAYOUT_REGION"
           for anchor in anchors):
        return False
    if assertion.attribute_kind == "DEPOSIT_TYPE":
        return ("ATTRIBUTE_JORC_CRITERION" in roles
                and _geology(assertion.source_term) == (assertion.controlled_term, assertion.source_term))
    try:
        stage, _, token = _milestone(assertion.source_term)
        date = reported_calendar_date(token)
    except ValueError:
        return False
    return (bool({"ATTRIBUTE_JORC_CRITERION", "ATTRIBUTE_HISTORY_HEADING"} & roles)
            and "ATTRIBUTE_LOCAL_SUBJECT" in roles and stage == assertion.controlled_term
            and token == assertion.event_date_token and date == assertion.event_date)
