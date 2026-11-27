"""Read a hash-locked PDF edition into separate physical page text views."""

import hashlib
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
    observed_sha = hashlib.sha256(content).hexdigest()
    if observed_sha != source.sha256:
        raise DocumentError("SCIENTIFIC_CONFLICT", "WRONG_SHA", observed_sha256=observed_sha)
    return content


def pdf_parser_version() -> str:
    try:
        result = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True, check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise DocumentError("CODE_ERROR", "PDF_PARSER_UNAVAILABLE") from error
    return result.stderr.splitlines()[0]


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
