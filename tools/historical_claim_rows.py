"""Rebind frozen claim hints to native PDF rows for extraction experiments."""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from au_ree_v3.documents import DocumentCache, DocumentError
from au_ree_v3.migration.baseline import BASELINES, PinnedRepository
from au_ree_v3.observations import digest
from au_ree_v3.sources import IntegrityError


_NUMBER = re.compile(r"(?<![\w.,])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.,])")
_CAPTION = re.compile(r"^\s*(?:(?:Table|Appendix)\s+[\w.]+\s*[:.–—-]?\s+)", re.I)
_RESOURCE = re.compile(r"\b(?:mineral resources?|resource estimate|resource inventory|MRE)\b", re.I)
_RARE_EARTH = re.compile(r"\b(?:TREO|REO|REE|rare[ -]+earths?|Nd2O3|Pr6O11)\b", re.I)
_NON_RESOURCE = re.compile(r"\b(?:ore reserves?|exploration targets?|drill(?:ing)? intercepts?|rock chips?|"
                           r"metallurgical (?:samples?|results?|tests?|testwork)|flotation (?:tests?|results?)|"
                           r"assay results|scandium)\b", re.I)


def _fold(text):
    return " ".join(text.split()).casefold()


def _marker_pattern(marker):
    # A short numeric marker must match the original token, not a digit
    # sequence inside a different number on the same page.
    if _NUMBER.fullmatch(marker):
        return re.compile(r"(?<![\w.,])" + re.escape(marker) + r"(?![\w.,])")
    return re.compile(r"(?<!\w)" + re.escape(marker) + r"(?!\w)")


def _table_row(line):
    return len(_NUMBER.findall(line)) >= 2 and len(re.split(r"\s{2,}", line.strip())) >= 3


def locate_claim_row(page_text, markers, original_token=None):
    """Require the entire page bundle and a unique original multi-column row."""
    lines = page_text.splitlines()
    page = _fold(page_text)
    markers = tuple(dict.fromkeys(_fold(marker) for marker in markers if marker.strip()))
    patterns = [(marker, _marker_pattern(marker)) for marker in markers]
    if not patterns or not all(pattern.search(page) for _, pattern in patterns):
        return None, "PAGE_MARKERS_NOT_FOUND"
    numeric = [pattern for marker, pattern in patterns if _NUMBER.search(marker)]
    if not numeric:
        return None, "NUMERIC_ROW_MARKER_MISSING"
    token = _marker_pattern(_fold(original_token)) if original_token else None
    matches = []
    for line, raw in enumerate(lines, 1):
        if not _table_row(raw):
            continue
        folded = _fold(raw)
        witnesses = [pattern for pattern in numeric if pattern.search(folded)]
        specific = any(len(_NUMBER.findall(marker)) >= 2 and pattern.search(folded)
                       for marker, pattern in patterns)
        if (specific or len(witnesses) >= 2) and (token is None or token.search(folded)):
            matches.append(line)
    if not matches:
        return None, "MARKERS_NOT_BOUND_TO_NUMERIC_TABLE_ROW"
    if len(matches) != 1:
        return None, "AMBIGUOUS_MARKED_TABLE_ROW"
    return matches[0], None


def _caption_label(text):
    resource, other = _RESOURCE.search(text), _NON_RESOURCE.search(text)
    if resource and not other:
        return True
    if other and not resource:
        return False
    return None


def _label_context(lines, line, claim):
    """Find printed resource/non-resource context at the matched row."""
    binding = claim.get("cell_binding") or {}
    expected = _fold(binding.get("table_label") or "")
    window_start = max(0, line - 25)
    before = list(enumerate(lines[window_start:line - 1], window_start))
    after = list(enumerate(lines[line:line + 5], line))
    captions = [(i, row) for i, row in before if _CAPTION.match(row)]
    if captions:
        caption_index, caption = captions[-1]
    else:
        # A caption below its table must agree with the frozen print marker.
        choices = [(i, row) for i, row in (*reversed(before), *after)
                   if _caption_label(row) is not None and any(
                       _fold(marker) in _fold(row) for marker in claim["page_markers"]
                       if len(_fold(marker)) > 14 and _caption_label(marker) is not None)]
        if not choices:
            return None, None
        caption_index, caption = choices[0]
    label = _caption_label(caption)
    if label is None or (expected and _caption_label(expected) != label):
        return None, None
    start = max(0, line - 16, min(caption_index + 1, line - 10))
    context = "\n".join((caption, *lines[start:line - 1]))
    if label and not _RARE_EARTH.search(context):
        return None, None
    return label, context


def _negative_page_rows(document, source, page, project):
    """Sample original grid rows under explicitly non-resource table captions."""
    lines = document.locate(page).text.splitlines()
    captions = [index for index, row in enumerate(lines) if _CAPTION.match(row)]
    for start, following in zip(captions, (*captions[1:], len(lines))):
        caption = lines[start]
        if _caption_label(caption) is not False or _table_row(caption):
            continue
        end = min(following, start + 18)
        # The caption must introduce a printed grid with a header; don't turn
        # neighbouring unlabeled sections or narrative into negatives.
        first = next((index for index in range(start + 2, end) if _table_row(lines[index])), None)
        if first is None or not any(len(re.split(r"\s{2,}", row.strip())) >= 2
                                    and _NUMBER.search(row) is None for row in lines[start + 1:first]):
            continue
        table_rows = []
        for index in range(first, end):
            row = lines[index]
            if not _table_row(row):
                break
            table_rows.append(index)
        context = "\n".join(lines[start:first])
        for index in dict.fromkeys((table_rows[0], table_rows[-1])):
            yield {"project": project, "source": source, "page": page, "line": index + 1,
                   "label": False, "context": context, "row_text": lines[index], "origin": "historical_same_document_control"}


def _claim_hints(payload, projects):
    for record in payload["projects"]:
        native = str(record["id"])
        project = "AU-" + digest([record.get("entity_namespace", "project_registry_v1"), native])[:20]
        for ordinal, claim in enumerate(record.get("observations", ()), 1):
            page = claim.get("pdf_page")
            source = claim.get("source_sha256")
            row = {"project_id": project, "source_sha256": source, "physical_page": page,
                   "claim_index": ordinal, "variable": claim["variable"]}
            if project not in projects:
                yield row, claim, "PROJECT_OUTSIDE_REGISTRY"
            elif type(page) is not int or page < 1 or not claim.get("page_markers"):
                yield row, claim, "PDF_PAGE_OR_MARKERS_MISSING"
            else:
                yield row, claim, None


def _read_pdf(source, last_page, store, historical_assets):
    try:
        body = store.read(source)
    except FileNotFoundError:
        path = historical_assets / source
        if not path.is_file():
            path = historical_assets / "assets" / source
        body = path.read_bytes()
    return DocumentCache().pdf(body, source, last_page=last_page)


def _bound_group(source, group, store, historical_assets):
    pages = {row["physical_page"] for row, _ in group}
    try:
        return _read_pdf(source, max(pages), store, historical_assets)
    except DocumentError as error:
        if str(error) != "PHYSICAL_PAGE_MISSING":
            raise
        # A bad historic locator must not block other pages in the same file.
        return _read_pdf(source, None, store, historical_assets)


def _bind_claim(document, row, claim):
    page = row["physical_page"]
    if page > len(document.pages):
        return {**row, "status": "PHYSICAL_PAGE_MISSING"}, None
    page_text = document.locate(page).text
    line, reason = locate_claim_row(page_text, claim["page_markers"], claim.get("original_token"))
    if reason:
        return {**row, "status": reason}, None
    label, context = _label_context(page_text.splitlines(), line, claim)
    receipt = {**row, "line": line, "status": "BOUND_POSITIVE" if label is True else
               "BOUND_NEGATIVE" if label is False else "ROW_BOUND_CLASS_UNPROVEN"}
    if label is None:
        return receipt, None
    return receipt, {"project": row["project_id"], "source": row["source_sha256"], "page": page, "line": line,
                     "label": label, "context": context, "row_text": document.locate(page, line=line).text,
                     "origin": "historical_claims"}


def rebind_historical_claims(v2_root, expected_sha256, projects, manifest, store, historical_assets):
    """Return one disposition per historical observation and original-only row labels."""
    data = PinnedRepository(Path(v2_root)).read("science", "data/claims.json")
    if data is None or sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Pinned V2 claims.json SHA differs")
    group, receipts, examples = defaultdict(list), [], []
    current = {(entry["sha256"], project) for entry in manifest["editions"] for project in entry["project_ids"]}
    for row, claim, reason in _claim_hints(json.loads(data), projects):
        row["registered_source_project_pair"] = (row["source_sha256"], row["project_id"]) in current
        if reason:
            receipts.append({**row, "status": reason})
        else:
            group[row["source_sha256"]].append((row, claim))
    n_docs = n_pages = n_controls = 0
    historical_assets = Path(historical_assets)
    for source, records in sorted(group.items()):
        try:
            document = _bound_group(source, records, store, historical_assets)
        except FileNotFoundError:
            document, reason = None, "EXACT_ORIGINAL_MISSING"
        except OSError:
            document, reason = None, "EXACT_ORIGINAL_UNREADABLE"
        except IntegrityError:
            document, reason = None, "EXACT_ORIGINAL_SHA_MISMATCH"
        except DocumentError as error:
            document, reason = None, str(error)
        if document is None:
            receipts.extend({**row, "status": reason} for row, _ in records)
            continue
        n_docs += 1
        n_pages += len(document.pages)
        has_positive = False
        for row, claim in records:
            receipt, example = _bind_claim(document, row, claim)
            receipts.append(receipt)
            if example is not None:
                examples.append(example)
                has_positive |= example["label"]
        source_projects = {row["project_id"] for row, _ in records}
        if has_positive and len(source_projects) == 1:
            project, = source_projects
            for page in range(1, len(document.pages) + 1):
                controls = tuple(_negative_page_rows(document, source, page, project))
                n_controls += len(controls)
                examples.extend(controls)
    return {
        "v2_ref": BASELINES["refs"]["science"], "claims_sha256": expected_sha256,
        "observations": len(receipts), "registered_source_project_observations": sum(
            row["registered_source_project_pair"] for row in receipts),
        "native_pdfs": n_docs, "physical_pages_decoded": n_pages, "same_document_negative_controls": n_controls,
        "claim_dispositions": dict(Counter(row["status"] for row in receipts)),
        "claims": sorted(receipts, key=lambda row: (row["project_id"], row["claim_index"])),
    }, examples
