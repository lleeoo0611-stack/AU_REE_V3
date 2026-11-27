"""Build reusable source-bound document views from verified original editions."""

import csv
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import io
import re
import subprocess
from pathlib import Path

from .contracts import Source


class DocumentError(ValueError):
    def __init__(self, outcome, reason, *, observed_sha256=None):
        super().__init__(reason)
        self.outcome = outcome
        self.observed_sha256 = observed_sha256


def read_asset(source: Source, assets: Path) -> bytes:
    """Read the specified local edition, verifying the bytes before parsing."""
    path = assets / "assets" / source.sha256
    if not path.exists():
        path = assets / source.sha256
    try:
        content = path.read_bytes()
    except OSError as error:
        raise DocumentError("SOURCE_UNAVAILABLE", "MISSING") from error
    _verified_content(content, source.sha256)
    return content


def pdf_parser_version() -> str:
    try:
        return _version("pdftotext", "-v")
    except (OSError, subprocess.SubprocessError) as error:
        raise DocumentError("CODE_ERROR", "PDF_PARSER_UNAVAILABLE") from error


def pdf_pages(content: bytes, last_page: int) -> tuple[str, ...]:
    """Use Poppler native positioned text, preserving one-based page offsets."""
    if not content.startswith(b"%PDF-"):
        raise DocumentError("SCIENTIFIC_CONFLICT", "PDF_SIGNATURE_MISSING")
    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(last_page), "-layout", "-enc", "UTF-8", "-", "-"],
            input=content, capture_output=True, check=True, timeout=30,
        )
    except FileNotFoundError as error:
        raise DocumentError("CODE_ERROR", "PDF_PARSER_UNAVAILABLE") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "PDF_TEXT_UNAVAILABLE") from error
    pages = result.stdout.decode("utf-8").split("\f")
    if pages[-1] == "":
        pages.pop()
    if len(pages) < last_page:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "PHYSICAL_PAGE_MISSING")
    return tuple(pages)


@dataclass(frozen=True, slots=True)
class LocatedText:
    source_sha256: str
    locator: str
    text: str
    method: str


@dataclass(frozen=True, slots=True)
class OCRConfig:
    model_path: Path
    dpi: int = 200
    page_segmentation_mode: int = 6

    def __post_init__(self) -> None:
        model = Path(self.model_path).resolve()
        object.__setattr__(self, "model_path", model)
        if model.suffix != ".traineddata" or not re.fullmatch(r"[A-Za-z0-9_]+", model.stem):
            raise ValueError("Select one Tesseract traineddata model")
        if type(self.dpi) is not int or not 72 <= self.dpi <= 300:
            raise ValueError("OCR rendering resolution must be 72 to 300 DPI")
        if type(self.page_segmentation_mode) is not int or not 3 <= self.page_segmentation_mode <= 13:
            raise ValueError("OCR must use one text-recognition segmentation mode")


@dataclass(frozen=True, slots=True)
class OCRRuntime:
    tesseract: str
    renderer: str
    model_sha256: str
    language: str
    dpi: int
    page_segmentation_mode: int


@dataclass(frozen=True, slots=True)
class OCRPage:
    page: int
    status: str
    text: str | None = None
    runtime: OCRRuntime | None = None
    reason: str | None = None


def _at(items: tuple, ordinal: int, label: str):
    if type(ordinal) is not int or not 1 <= ordinal <= len(items):
        raise DocumentError("EVIDENCE_INSUFFICIENT", f"{label}_MISSING")
    return items[ordinal - 1]


@dataclass(frozen=True, slots=True)
class PDFDocument:
    source_sha256: str
    pages: tuple[str, ...]
    runtime: str
    ocr_pages: tuple[OCRPage, ...] = ()

    def blocks(self, page: int) -> tuple[LocatedText, ...]:
        """Group contiguous native layout lines without reparsing the PDF."""
        lines = _at(self.pages, page, "PHYSICAL_PAGE").splitlines()
        blocks, first = [], None
        for index, text in enumerate([*lines, ""], start=1):
            if text.strip():
                if first is None:
                    first = index
            elif first is not None:
                blocks.append(LocatedText(
                    self.source_sha256, f"PDF page {page}, lines {first}-{index - 1}",
                    "\n".join(lines[first - 1:index - 1]), "NATIVE_PDF",
                ))
                first = None
        return tuple(blocks)

    def locate(self, page: int, *, line: int | None = None, column: int | None = None,
               end_column: int | None = None, use_ocr: bool = False) -> LocatedText:
        """Locate native page text or an explicitly requested OCR candidate."""
        text = _at(self.pages, page, "PHYSICAL_PAGE")
        method = "NATIVE_PDF"
        if use_ocr:
            ocr = next((result for result in self.ocr_pages if result.page == page), None)
            if ocr is None or ocr.status != "CANDIDATE" or not ocr.text:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "OCR_CANDIDATE_MISSING")
            text, method = ocr.text, "OCR_CANDIDATE"
        address = f"PDF page {page}"
        if line is not None:
            text = _at(tuple(text.splitlines()), line, "TEXT_LINE")
            address += f", line {line}"
        if column is not None:
            end = column if end_column is None else end_column
            if (line is None or type(column) is not int or type(end) is not int
                    or not 1 <= column <= end <= len(text)):
                raise DocumentError("EVIDENCE_INSUFFICIENT", "TEXT_COLUMN_MISSING")
            text = text[column - 1:end]
            address += f", columns {column}-{end}"
        elif end_column is not None:
            raise ValueError("An ending column requires a starting column")
        return LocatedText(self.source_sha256, address, text, method)


@dataclass(frozen=True, slots=True)
class HTMLDocument:
    source_sha256: str
    fragments: tuple[LocatedText, ...]

    def locate(self, fragment: int) -> LocatedText:
        return _at(self.fragments, fragment, "HTML_FRAGMENT")


class _HTMLFragments(HTMLParser):
    def __init__(self, source_sha256: str):
        super().__init__(convert_charrefs=True)
        self.source_sha256 = source_sha256
        self.fragments: list[LocatedText] = []
        self.ignored_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self.ignored_tag:
            self.ignored_tag = None

    def handle_data(self, data: str) -> None:
        if self.ignored_tag is None and data.strip():
            line, column = self.getpos()
            locator = f"HTML fragment {len(self.fragments) + 1}, line {line}, character {column + 1}"
            self.fragments.append(LocatedText(self.source_sha256, locator, data, "HTML_TEXT"))


@dataclass(frozen=True, slots=True)
class CSVDocument:
    source_sha256: str
    rows: tuple[tuple[str, ...], ...]
    physical_lines: tuple[tuple[int, int], ...]

    def locate(self, row: int, column: int | str) -> LocatedText:
        """Return a cell using record numbers (header=1) or the exact header name."""
        values = _at(self.rows, row, "CSV_ROW")
        if isinstance(column, str):
            matches = [index for index, name in enumerate(self.rows[0], start=1) if name == column]
            if len(matches) != 1:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "CSV_COLUMN_AMBIGUOUS_OR_MISSING")
            column = matches[0]
        token = _at(values, column, "CSV_COLUMN")
        start, end = self.physical_lines[row - 1]
        return LocatedText(self.source_sha256, f"CSV record {row}, column {column}, lines {start}-{end}",
                           token, "CSV_FIELD")


def _verified_content(content: bytes, source_sha256: str) -> None:
    observed = hashlib.sha256(content).hexdigest()
    if observed != source_sha256:
        raise DocumentError("SCIENTIFIC_CONFLICT", "WRONG_SHA", observed_sha256=observed)


def _decoded(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "DOCUMENT_ENCODING_UNSUPPORTED") from error


def _version(tool: str, flag: str) -> str:
    output = subprocess.run([tool, flag], capture_output=True, check=True, timeout=5)
    lines = (output.stdout + output.stderr).decode("utf-8", errors="replace").splitlines()
    if not lines:
        raise OSError(f"Missing version from {tool}")
    return lines[0]


def _ocr_text(content: bytes, page: int, config: OCRConfig) -> str:
    image = subprocess.run(
        ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(config.dpi), "-singlefile", "-png", "-"],
        input=content, capture_output=True, check=True, timeout=30,
    ).stdout
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--tessdata-dir", str(config.model_path.parent),
         "-l", config.model_path.stem, "--psm", str(config.page_segmentation_mode)],
        input=image, capture_output=True, check=True, timeout=60,
    )
    return result.stdout.decode("utf-8")


class DocumentCache:
    """Reuse views within one execution, validating input bytes even on a cache hit."""

    def __init__(self):
        self._pdf: dict[str, tuple[str, ...]] = {}
        self._html: dict[str, HTMLDocument] = {}
        self._csv: dict[str, CSVDocument] = {}
        self._ocr: dict[tuple[str, int, OCRRuntime], OCRPage] = {}
        self._pdf_runtime: str | None = None
        self._ocr_tools: tuple[str, str] | None = None

    def pdf(self, content: bytes, source_sha256: str, last_page: int, *,
            ocr: OCRConfig | None = None) -> PDFDocument:
        _verified_content(content, source_sha256)
        if type(last_page) is not int or last_page < 1:
            raise ValueError("last_page must be a positive physical page number")
        native = self._pdf.get(source_sha256, ())
        if len(native) < last_page:
            native = pdf_pages(content, last_page)
            self._pdf[source_sha256] = native
        if self._pdf_runtime is None:
            self._pdf_runtime = pdf_parser_version()
        pages = native[:last_page]
        ocr_pages = self._fallback(content, source_sha256, pages, ocr) if ocr else ()
        return PDFDocument(source_sha256, pages, self._pdf_runtime, ocr_pages)

    def _fallback(self, content: bytes, sha: str, pages: tuple[str, ...],
                  config: OCRConfig) -> tuple[OCRPage, ...]:
        unreadable = [page for page, text in enumerate(pages, start=1) if not text.strip()]
        if not unreadable:
            return ()
        try:
            model_sha = hashlib.sha256(config.model_path.read_bytes()).hexdigest()
            if self._ocr_tools is None:
                self._ocr_tools = (_version("tesseract", "--version"), _version("pdftoppm", "-v"))
        except (OSError, subprocess.SubprocessError):
            return tuple(OCRPage(page, "BLOCKED", reason="OCR_RUNTIME_OR_MODEL_UNAVAILABLE")
                         for page in unreadable)
        runtime = OCRRuntime(*self._ocr_tools, model_sha, config.model_path.stem,
                             config.dpi, config.page_segmentation_mode)
        candidates = []
        for page in unreadable:
            key = (sha, page, runtime)
            previous = self._ocr.get(key)
            if previous is not None:
                candidates.append(previous)
                continue
            try:
                text = _ocr_text(content, page, config)
                if hashlib.sha256(config.model_path.read_bytes()).hexdigest() != model_sha:
                    raise OSError("OCR model changed during recognition")
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
                candidates.append(OCRPage(page, "BLOCKED", runtime=runtime, reason="OCR_UNAVAILABLE"))
                continue
            result = (OCRPage(page, "CANDIDATE", text, runtime) if text.strip()
                      else OCRPage(page, "BLOCKED", runtime=runtime, reason="OCR_TEXT_EMPTY"))
            self._ocr[key] = result
            candidates.append(result)
        return tuple(candidates)

    def html(self, content: bytes, source_sha256: str) -> HTMLDocument:
        _verified_content(content, source_sha256)
        if source_sha256 not in self._html:
            parser = _HTMLFragments(source_sha256)
            parser.feed(_decoded(content))
            parser.close()
            self._html[source_sha256] = HTMLDocument(source_sha256, tuple(parser.fragments))
        return self._html[source_sha256]

    def csv(self, content: bytes, source_sha256: str) -> CSVDocument:
        _verified_content(content, source_sha256)
        if source_sha256 not in self._csv:
            reader = csv.reader(io.StringIO(_decoded(content), newline=""), strict=True)
            rows, lines = [], []
            previous = 0
            try:
                for row in reader:
                    if row:
                        rows.append(tuple(row))
                        lines.append((previous + 1, reader.line_num))
                    previous = reader.line_num
            except csv.Error as error:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "CSV_PARSE_FAILED") from error
            if not rows or any(len(row) != len(rows[0]) for row in rows):
                raise DocumentError("EVIDENCE_INSUFFICIENT", "CSV_ROW_WIDTH_MISMATCH")
            self._csv[source_sha256] = CSVDocument(source_sha256, tuple(rows), tuple(lines))
        return self._csv[source_sha256]
