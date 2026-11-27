"""Verify structural metrics and dependency gaps without requiring a V2 checkout."""

import ast
import json
from pathlib import Path
import tempfile
import unittest

from au_ree_v3.migration.audit import _matrix, _scan_references, audit, branch_score, package_metrics


class MigrationAuditTests(unittest.TestCase):
    def test_branch_score_and_duplicate_bodies(self):
        template = """
def run(items):
    \"\"\"{description}\"\"\"
    result = [item for item in items if item and item > 0]
    def nested():
        if result:
            return 1
        return 2
    return result
"""
        first = ast.parse(template.format(description="First description"))
        second = ast.parse(template.format(description="Second description"))
        self.assertEqual(branch_score(first.body[0]), 4)
        self.assertEqual(branch_score(first.body[0].body[2]), 2)
        metrics = package_metrics({"first": first, "second": second})
        groups = metrics["cross_module_duplicate_groups"].values()
        self.assertTrue(any({entry["module"] for entry in group} == {"first", "second"}
                            for group in groups))

    def test_graph_includes_nested_imports_and_detects_cycle(self):
        metrics = package_metrics({
            "first": ast.parse("def run():\n    from . import second\n"),
            "second": ast.parse("import au_ree_v2.first"),
        })
        self.assertEqual(metrics["graph"], {"first": ["second"], "second": ["first"]})
        self.assertIsNone(metrics["topological_order"])
        self.assertEqual(set(metrics["cycle_witness"]), {"first", "second"})

    def test_references_preserve_unresolved_runtime_consumption(self):
        source = "src/au_ree_v2/example.py"
        module = "src/au_ree_v2/reader.py"
        fixture = "data/input with spaces.json"
        test = "tests/test_example.py"
        sources = {
            source: ("import importlib\n"
                     "importlib.import_module('au_ree_v2.reader')\n"
                     "open('data/input with spaces.json')\n"
                     "open(computed_path)\n"),
            module: "",
            test: "from au_ree_v2 import example\n",
        }
        users, unresolved, runtime = _scan_references(
            dict.fromkeys([source, module, fixture, test]), sources,
            {name: ast.parse(text) for name, text in sources.items()},
            {"reader": module, "example": source},
        )
        self.assertEqual(users[fixture], {source})
        self.assertEqual(users[source], {test})
        self.assertEqual(runtime[module], {source + ":2:import_module"})
        self.assertEqual(unresolved[source], {source + ":4:open"})

    def test_absent_checkout_receipt_and_unmapped_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "absent_checkout"
            output = Path(directory) / "audit"
            receipt = audit(root, output)
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertEqual(json.loads((output / "audit.json").read_text()), receipt)
            with self.assertRaises(ValueError):
                audit(root, root / "receipts")
        rows, unmapped = _matrix({}, {"cli": {"new"}, "tools": set(), "r_scripts": set(),
                                      "workflows": set()}, {})
        self.assertEqual(unmapped, ["cli:new"])
        self.assertEqual(rows[-1]["domain"], "UNMAPPED")
        self.assertEqual(rows[-1]["parity_status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
