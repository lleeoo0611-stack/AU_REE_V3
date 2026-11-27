"""Audit selected source responsibilities, direct imports and exact repeated AST bodies."""

import argparse
import ast
from collections import defaultdict
import json
from pathlib import Path

from engineering_benchmark import static_imports
from au_ree_v3.migration.audit import DUPLICATE_MIN_NODES, branch_score, function_body_fingerprint

GROUPS = {
    "application": "Pinned historical replay entry point",
    "fresh_research": "V3 all-93 formal build and source-manifest verification",
    "observations": "Shared historical parser primitives and fixed V2 claim binding",
    "fresh_observations": "V3 multi-strategy evidence admission and selection",
    "resource_extraction": "Native document structure parsers bound to resource evidence",
    "composition_extraction": "Oxide table parsing and exact component retrieval",
    "census": "Historical resource projection",
    "fresh_census": "93-by-51 V3 ledger and governed scientific cells",
    "fresh_decision": "Decision policy, complete-case cohort construction and score projection",
    "decision": "Numerical SAW, PROMETHEE II, TOPSIS and leave-out kernels",
    "comparability": "Approved v1 scoped criterion admission and formal resource comparator",
    "semantics": "Versioned scientific context and independent purpose assessment",
    "normalization": "Exact Decimal units, oxide identities and rounding/derivation evidence",
    "semantic_research": "Same-input A/B/C/D shadow ablation and reporting",
    "technical_processing": "T1v2 shadow technical measurements, anchoring and Oracle review",
    "development": "D1 scoped issuer milestone extraction and shadow labeling",
    "geography": "GIS geometry identity and proximity evidence eligibility",
    "source_inventory": "Full document corpus inventory and parsability",
    "candidate_search": "Read-only candidate retrieval index",
    "documents": "SHA-bound native PDF/HTML/CSV and OCR document views",
    "research_delta": "Cross-release science, cohort and artifact verification",
    "publication": "Content-addressed archive, offline replay and publication pointer",
}

def audit(roots):
    files = {}
    for name in GROUPS:
        for tag, root in roots:
            path = root / f"{name}.py"
            if path.is_file():
                files[name] = tag, path
                break
    scope = {"au_ree_v3."+name for name in files}
    found, duplicates = {}, defaultdict(list)
    for name, (tag, path) in files.items():
        module = "au_ree_v3." + name
        tree = ast.parse(path.read_bytes())
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        scores = sorted(branch_score(node) for node in functions)
        found[name] = {
            "origin_snapshot":tag,"responsibility":GROUPS[name],
            "imports_within_reviewed_files":sorted(static_imports(module,path,tree,scope)),
            "named_ast_functions":len(functions),"maximum_branch_score":scores[-1] if scores else None,
        }
        for node in functions:
            signature, nodes = function_body_fingerprint(node)
            if nodes >= DUPLICATE_MIN_NODES:
                duplicates[signature].append({"module":name,"function":node.name,"line":node.lineno})
    return {
        "files":found,
        "repeated_AST_body_groups_across_reviewed_modules":[group for group in duplicates.values()
                                                            if len({row["module"] for row in group}) > 1],
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("baseline-code", "current-code", "out"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    report = {
        "schema": "ontology-source-responsibility-audit-v1",
        "measurement_scope": "Static named files, including functions not used in the equivalent formal workload",
        "baseline": audit([("immutable-baseline", args.baseline_code / "src/au_ree_v3")]),
        "after": audit([("immutable-current", args.current_code / "src/au_ree_v3")]),
        "decisions": [
            "Keep pinned historical replay and current governed research separate.",
            "Share the SHA-bound document views between strategy extraction and reviewer observations.",
            "Keep approved formal admission, shadow purpose assessment and quantitative normalization distinct.",
            "Keep numerical ranking kernels independent of cohort construction and release comparisons.",
            "Consolidation opportunity is repeated canonical hashing code.",
        ],
        "limits": "Static counts of expanded scope describe structure. Exact fingerprints do not measure approximate duplication.",
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
