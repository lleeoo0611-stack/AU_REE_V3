"""Source-bound document views with deterministic synthetic parsing fixtures."""

from hashlib import sha256
from pathlib import Path
import re
from shutil import which
import subprocess
import unittest
from unittest.mock import patch
import zlib

from au_ree_v3.documents import DocumentCache, DocumentError, OCRConfig, pdf_pages


def _pdf(objects: list[bytes]) -> bytes:
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start_xref = len(data)
    data += f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    data += (f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\n"
             f"startxref\n{start_xref}\n%%EOF\n").encode()
    return bytes(data)


def _stream(dictionary: bytes, content: bytes) -> bytes:
    return (b"<< " + dictionary + f" /Length {len(content)} >>\nstream\n".encode()
            + content + b"\nendstream")


def _bitmap() -> tuple[int, int, bytes]:
    glyphs = {
        "O": ("01110", "11011", "10001", "10001", "10001", "11011", "01110"),
        "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
        "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    }
    phrase = tuple("0000" + "0".join(glyphs[letter][line] for letter in "ORE") + "0000"
                   for line in range(7))
    blank = "0" * len(phrase[0])
    scanlines = (blank, blank, *phrase, blank, blank)
    scale = 12
    raw = bytes((0 if pixel == "1" else 255)
                for row in scanlines for _ in range(scale)
                for pixel in row for _ in range(scale))
    return len(scanlines[0]) * scale, len(scanlines) * scale, zlib.compress(raw)


def _example_pdf() -> bytes:
    width, height, raster = _bitmap()
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 2 /Kids [3 0 R 6 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 350 190]"
        b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        _stream(b"", b"BT /F1 24 Tf 40 110 Td (Grade 0.0040) Tj ET"),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 350 190]"
        b" /Resources << /XObject << /Image 7 0 R >> >> /Contents 8 0 R >>",
        _stream((f"/Type /XObject /Subtype /Image /Width {width} /Height {height}"
                 " /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode").encode(), raster),
        _stream(b"", b"q 290 0 0 128 28 30 cm /Image Do Q"),
    ])


def _english_model() -> Path | None:
    if not which("tesseract") or not which("pdftoppm"):
        return None
    tesseract = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, check=True)
    match = re.search(r'"([^"]+)"', tesseract.stdout)
    path = Path(match[1]) / "eng.traineddata" if match else None
    return path if path and path.is_file() else None


class DocumentViewTests(unittest.TestCase):
    def test_html_locator_keeps_source_order_and_ignores_scripts(self):
        raw = b"<style>not text</style>\n<p>Grade &amp; size</p>\n<script>1</script><p>0.40%</p>"
        digest = sha256(raw).hexdigest()
        cache = DocumentCache()
        result = cache.html(raw, digest)
        self.assertIs(result, cache.html(raw, digest))
        self.assertEqual(len(result.fragments), 2)
        located = result.locate(1)
        self.assertEqual(located.source_sha256, digest)
        self.assertEqual(located.text, "Grade & size")
        self.assertEqual(located.locator, "HTML fragment 1, line 2, character 4")
        self.assertEqual(result.locate(2).text, "0.40%")
        with self.assertRaisesRegex(DocumentError, "WRONG_SHA"):
            cache.html(raw + b" ", digest)
        with self.assertRaisesRegex(DocumentError, "HTML_FRAGMENT_MISSING"):
            result.locate(3)

    def test_csv_locator_distinguishes_cells_and_preserves_multiline_tokens(self):
        raw = b'project,grade,note\r\n\r\nAlpha,0.000,"line 1\r\nline 2"\r\nBeta,,not disclosed\r\n'
        signature = sha256(raw).hexdigest()
        cache = DocumentCache()
        view = cache.csv(raw, signature)
        self.assertIs(view, cache.csv(raw, signature))
        self.assertEqual(view.locate(2, "grade").text, "0.000")
        self.assertEqual(view.locate(3, 2).text, "")
        multiline = view.locate(2, "note")
        self.assertEqual(multiline.text, "line 1\r\nline 2")
        self.assertEqual(multiline.locator, "CSV record 2, column 3, lines 3-4")
        self.assertEqual(multiline.source_sha256, signature)
        malformed = b"x,y\n1\n"
        with self.assertRaisesRegex(DocumentError, "CSV_ROW_WIDTH_MISMATCH"):
            cache.csv(malformed, sha256(malformed).hexdigest())
        ambiguous = b"x,x\n1,2\n"
        with self.assertRaisesRegex(DocumentError, "CSV_COLUMN_AMBIGUOUS_OR_MISSING"):
            cache.csv(ambiguous, sha256(ambiguous).hexdigest()).locate(2, "x")

    @unittest.skipUnless(which("pdftotext"), "Poppler native renderer unavailable")
    def test_pdf_cache_reuses_native_pages_without_moving_physical_ordinals(self):
        raw = _example_pdf()
        digest = sha256(raw).hexdigest()
        cache = DocumentCache()
        with patch("au_ree_v3.documents.pdf_pages", wraps=pdf_pages) as renderer:
            view = cache.pdf(raw, digest, 2)
            narrow = cache.pdf(raw, digest, 1)
            self.assertEqual(renderer.call_count, 1)
        self.assertEqual(narrow.pages, view.pages[:1])
        self.assertFalse(view.pages[1].strip())
        found = view.locate(1, line=1, column=7, end_column=12)
        self.assertEqual(found.text, "0.0040")
        self.assertEqual(found.method, "NATIVE_PDF")
        self.assertEqual(found.source_sha256, digest)
        self.assertEqual(view.blocks(1)[0].locator, "PDF page 1, lines 1-1")
        self.assertEqual(view.blocks(1)[0].text, "Grade 0.0040")
        self.assertEqual(view.blocks(2), ())
        self.assertIn("pdftotext version", view.runtime)
        with self.assertRaisesRegex(DocumentError, "WRONG_SHA"):
            cache.pdf(raw + b" ", digest, 1)
        with self.assertRaisesRegex(DocumentError, "PHYSICAL_PAGE_MISSING"):
            cache.pdf(raw, digest, 3)
        unavailable = cache.pdf(raw, digest, 2, ocr=OCRConfig(Path("/nonexistent/eng.traineddata")))
        self.assertEqual(unavailable.pages, view.pages)
        self.assertEqual(unavailable.ocr_pages[0].status, "BLOCKED")

    @unittest.skipUnless(which("pdftotext"), "Poppler native renderer unavailable")
    def test_image_only_page_ocr_is_a_separate_candidate_and_records_model(self):
        model = _english_model()
        if model is None:
            self.skipTest("Tesseract, Poppler rasterizer or English model unavailable")
        raw = _example_pdf()
        digest = sha256(raw).hexdigest()
        cache = DocumentCache()
        config = OCRConfig(model, dpi=250, page_segmentation_mode=8)
        parsed = cache.pdf(raw, digest, 2, ocr=config)
        self.assertEqual(len(parsed.ocr_pages), 1)
        candidate = parsed.ocr_pages[0]
        self.assertEqual((candidate.page, candidate.status), (2, "CANDIDATE"), candidate)
        self.assertEqual(candidate.runtime.model_sha256, sha256(model.read_bytes()).hexdigest())
        self.assertIn("tesseract", candidate.runtime.tesseract)
        self.assertIn("pdftoppm", candidate.runtime.renderer)
        self.assertEqual(parsed.locate(2, use_ocr=True).method, "OCR_CANDIDATE")
        self.assertIn("ORE", parsed.locate(2, use_ocr=True).text)
        self.assertFalse(parsed.locate(2).text.strip())
        self.assertIs(cache.pdf(raw, digest, 2, ocr=config).ocr_pages[0], candidate)


if __name__ == "__main__":
    unittest.main()
