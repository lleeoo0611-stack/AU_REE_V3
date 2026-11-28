"""A pinned CPU structure experiment. Model boxes locate original PDF words."""

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from math import isfinite
from pathlib import Path

from .documents import DocumentError
from .table_geometry import _join, _line_groups, _mid, _mupdf_fragments, _pieces, _rows, _word_in

RULE = "resource-table-transformer-cpu-v1"
WEIGHT_FILE = "onnx/model_quantized.onnx"
MODEL_PINS = {
    "detection": {
        "repository": "Xenova/table-transformer-detection",
        "revision": "187ac355617c8fee3d69c00d461ecf8eb8a4a5b7",
        "file": WEIGHT_FILE,
        "sha256": "ffb22794497a7092ad949bca248f22ff860fb457e6402986e09f6023b6e91c88",
        "preprocessing_config_sha256": "aa9394c0c19cb47f8bd980c3cdc41868062c6041551f9b84f1994b0bb2d756b6",
        "upstream": "microsoft/table-transformer-detection",
        "upstream_revision": "2357cbe2b5a5d1c03e54f32764f06058933b65ab",
        "license": "MIT (upstream)",
    },
    "structure": {
        "repository": "Xenova/table-transformer-structure-recognition",
        "revision": "5387550de655512721e1b88e4e42117001ba4813",
        "file": WEIGHT_FILE,
        "sha256": "c11f4033da75e9c4d41c403ef356e89caa0a37a7d111b55461e7d5ba856bb6b6",
        "preprocessing_config_sha256": "faf6b63783f6bd609daa71ab5f58a1a640c0d27141d68ec32881debb00512876",
        "upstream": "microsoft/table-transformer-structure-recognition",
        "upstream_revision": "f4d4bdc85c3fe4b1fa49658882a5d38bbdd0f343",
        "license": "MIT (upstream)",
    },
}
_DETECT_SIZE = (800, 800)
_STRUCTURE_SIZE = (800, 1000)
_MIN_PROBABILITY = 0.5
_SCALE = 1.5
_TABLE_PADDING_PTS = 4


@dataclass(frozen=True)
class TransformerModels:
    detector: object
    structure: object
    runtime: str

    @property
    def provenance(self):
        return {
            "rule": RULE, "models": MODEL_PINS, "backend": "CPUExecutionProvider",
            "onnxruntime": self.runtime, "threshold": _MIN_PROBABILITY,
            "libraries": {name: version(name) for name in ("PyMuPDF", "numpy", "Pillow")},
            "pixels_per_point": _SCALE, "detection_resize": _DETECT_SIZE, "structure_resize": _STRUCTURE_SIZE,
        }


def _pinned_bytes(path: Path, role: str) -> bytes:
    data = Path(path).read_bytes()
    if sha256(data).hexdigest() != MODEL_PINS[role]["sha256"]:
        raise ValueError(f"Table Transformer {role} model SHA-256 differs")
    return data


def load_transformer_models(detector: Path, structure: Path) -> TransformerModels:
    """Read and verify each pinned graph once for all documents in an evaluation."""
    import onnxruntime

    if onnxruntime.__version__ != "1.21.1":
        raise ValueError("Table Transformer evaluation requires ONNX Runtime 1.21.1 CPU")
    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    options.use_deterministic_compute = True
    detect, recognize = (
        onnxruntime.InferenceSession(_pinned_bytes(path, role), sess_options=options, providers=["CPUExecutionProvider"])
        for path, role in ((detector, "detection"), (structure, "structure"))
    )
    return TransformerModels(detect, recognize, onnxruntime.__version__)


def _image_batch(image, size):
    import numpy as np
    from PIL import Image

    shortest, longest = size
    scale = min(shortest / min(image.size), longest / max(image.size))
    resized = image.resize(tuple(max(1, round(n * scale)) for n in image.size), Image.Resampling.BILINEAR)
    rgb = np.asarray(resized, dtype=np.float32) / 255
    rgb = (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])


def _rect(geometry, area):
    x, y, width, height = geometry
    left, top, right, bottom = area

    def convert(value, low, high):
        return low + min(1.0, max(0.0, float(value))) * (high - low)

    return (convert(x - width / 2, left, right), convert(y - height / 2, top, bottom),
            convert(x + width / 2, left, right), convert(y + height / 2, top, bottom))


def _predictions(session, image, area, size, classes):
    import numpy as np

    logits, geometry = session.run(["logits", "pred_boxes"], {"pixel_values": _image_batch(image, size)})
    scores = np.exp(logits[0] - logits[0].max(axis=-1, keepdims=True))
    scores /= scores.sum(axis=-1, keepdims=True)
    predictions = []
    for bounds, probabilities in zip(geometry[0], scores, strict=True):
        label = int(probabilities.argmax())
        if label in classes and probabilities[label] >= _MIN_PROBABILITY and all(isfinite(value) for value in bounds):
            predictions.append((label, float(probabilities[label]), _rect(bounds, area)))
    return predictions


def _coverage(first, second):
    x = max(0, min(first[2], second[2]) - max(first[0], second[0]))
    y = max(0, min(first[3], second[3]) - max(first[1], second[1]))
    area = (first[2] - first[0]) * (first[3] - first[1])
    return x * y / area if area else 0


def _unique_boxes(predictions, label):
    selected = []
    for key, _, rect in sorted(predictions, key=lambda value: (-value[1], value[2])):
        if key == label and rect[0] < rect[2] and rect[1] < rect[3]:
            if all(max(_coverage(rect, other), _coverage(other, rect)) < 0.8 for other in selected):
                selected.append(rect)
    return selected


def _view(page, clip=None):
    import pymupdf
    from PIL import Image

    picture = page.get_pixmap(matrix=pymupdf.Matrix(_SCALE, _SCALE), clip=clip, alpha=False, colorspace=pymupdf.csRGB)
    native_area = tuple(value / _SCALE for value in (picture.x, picture.y, picture.x + picture.width, picture.y + picture.height))
    return Image.frombytes("RGB", (picture.width, picture.height), picture.samples), native_area


def _column_word_groups(row_words, columns):
    """Assign a native token only when a single predicted column owns it."""
    result = {}
    for word in row_words:
        center = _mid(word.bbox, 0, 2)
        candidates = [index for index, col in enumerate(columns) if col[0] <= center < col[2]]
        if len(candidates) == 1:
            result.setdefault(candidates[0], []).append(word)
        elif not candidates and center < columns[0][0]:
            # Local printed labels may sit outside the detected grid.
            result.setdefault(-1, []).append(word)
    return tuple(_join(result[index]) for index in sorted(result))


def _structured_rows(page, models, words, table):
    import pymupdf

    x0, y0, x1, y1 = table
    padding = _TABLE_PADDING_PTS
    clip = pymupdf.Rect(x0-padding, y0-padding, x1+padding, y1+padding) & page.rect
    image, area = _view(page, clip)
    grid = _predictions(models.structure, image, area, _STRUCTURE_SIZE, {1, 2})
    columns = sorted(_unique_boxes(grid, 1), key=lambda col: col[0])
    bands = sorted(_unique_boxes(grid, 2), key=lambda row: row[1])
    if len(columns) < 2 or len(bands) < 2:
        return
    for band in bands:
        native = [word for word in words if _word_in(word, band)]
        for line in _line_groups(native):
            if parts := _column_word_groups(line, columns):
                yield parts


@dataclass(frozen=True)
class _TransformerReader:
    document: object
    models: TransformerModels

    @property
    def page_count(self):
        return self.document.page_count

    def page(self, number):
        if type(number) is not int or not 1 <= number <= self.page_count:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "PHYSICAL_PAGE_MISSING")
        page = self.document[number - 1]
        if page.rotation:
            return ()
        words = tuple(_mupdf_fragments(page))
        if not words:
            return ()
        image, area = _view(page)
        predictions = _predictions(self.models.detector, image, area, _DETECT_SIZE, {0})
        tables = sorted(_unique_boxes(predictions, 0), key=lambda bounds: (bounds[1], bounds[0]))
        groups = [parts for table in tables for parts in _structured_rows(page, self.models, words, table)]
        native_lines = tuple(_pieces(line) for line in _line_groups(words))
        original = {(row.bbox, row.text): row for row in _rows(number, groups, native_lines)}
        return tuple(original.values())


@contextmanager
def open_transformer_geometry(body: bytes, models: TransformerModels):
    """Supply original PDF bytes to the same candidate row interface as geometry."""
    import pymupdf

    try:
        document = pymupdf.open(stream=body, filetype="pdf")
    except (RuntimeError, ValueError) as error:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "GEOMETRY_PDF_UNREADABLE") from error
    with document:
        if document.needs_pass:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "GEOMETRY_PDF_ENCRYPTED")
        yield _TransformerReader(document, models)
