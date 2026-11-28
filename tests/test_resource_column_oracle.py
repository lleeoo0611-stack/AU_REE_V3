from hashlib import sha256
import unittest
from types import SimpleNamespace

from tools.build_resource_column_oracle import PLAN, _annotate_page, _literal_fields, _verify_header, build


class ResourceColumnOracleTest(unittest.TestCase):
    def test_preserves_original_token_and_rejects_header_mismatch(self):
        header = "Tonnes (Mt)                       TREO (ppm)"
        fields = _literal_fields("Measured              1,230.0            40%")
        self.assertEqual(fields, ("1,230.0", "40"))
        spec = {
            "table_scope": "Original resource estimate", "header_lines": [1],
            "header_sha256": sha256(header.encode()).hexdigest(),
        }
        _verify_header(spec, [header])
        with self.assertRaisesRegex(ValueError, "header differs"):
            _verify_header(spec, [header.replace("TREO", "THM")])

    def test_ambiguous_native_row_requires_independent_printed_anchor(self):
        header = "Tonnes (Mt)                       TREO (ppm)"
        value = "Measured               53.0                 200"
        poppler = SimpleNamespace(locate=lambda page: SimpleNamespace(text=header + "\n" + value))
        row = SimpleNamespace(cells=[SimpleNamespace(text="53.0"), SimpleNamespace(text="200")])
        geometry = SimpleNamespace(page=lambda page: [row, row])
        spec = {
            "table_scope": "Original resource estimate", "header_lines": [1],
            "header_sha256": sha256(header.encode()).hexdigest(), "body_lines": [2], "width": 2,
        }
        with self.assertRaisesRegex(ValueError, "does not bind uniquely"):
            _annotate_page("sha", 1, poppler, geometry, [spec])

    def test_adapting_development_rows_is_rejected_before_source_access(self):
        record = {"source_sha256": "digest", "partition": "DEVELOPMENT"}
        with self.assertRaisesRegex(ValueError, "outside the frozen issuer partition"):
            build(plan={"schema": PLAN, "tables": [record]}, split={"digest": {"partition": "DEVELOPMENT"}},
                  manifest=None, store=None)
