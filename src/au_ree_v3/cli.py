"""Command-line entry point for migration verification."""

from argparse import ArgumentParser
import json
from pathlib import Path
import sys

EXIT_CODES = {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "NOT_RUN": 3, "UNMEASURED": 4}


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(prog="au-ree-v3")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay-resource", help="Replay a reviewed exact-source resource binding")
    replay.add_argument("--bindings", type=Path, required=True)
    replay.add_argument("--assets", type=Path, required=True)
    replay.add_argument("--database", type=Path, required=True)
    replay.add_argument("--out", type=Path, required=True)
    replay.add_argument("--code-commit", required=True, help="Full commit SHA of the code used")
    migration = commands.add_parser("migration", help="Audit and verify fixed V2 references")
    operations = migration.add_subparsers(dest="operation", required=True)
    for name in ("audit", "build-golden", "verify-crosswalk", "verify-baseline"):
        operation = operations.add_parser(name)
        operation.add_argument("--v2-root", type=Path, required=True)
        operation.add_argument("--out", type=Path, required=True)
        if name in {"build-golden", "verify-baseline"}:
            operation.add_argument("--assets", type=Path)
        if name == "verify-baseline":
            operation.add_argument("--profile", required=True, choices=(
                "archived-census-2026-11-13", "phase22-2026-11-05",
            ))

    temporal = operations.add_parser("audit-temporal", help="Audit Golden time evidence without changing expectations")
    temporal.add_argument("--v2-root", type=Path, required=True)
    temporal.add_argument("--expected", type=Path, required=True)
    temporal.add_argument("--oracle-manifest", type=Path, required=True)
    temporal.add_argument("--out", type=Path, required=True)
    oracle = operations.add_parser("compare-oracle")
    oracle.add_argument("--expected", type=Path, required=True)
    oracle.add_argument("--actual", type=Path, required=True)
    oracle.add_argument("--out", type=Path, required=True)
    oracle.add_argument("--case-id", action="append", help="Limit the comparison to this case (repeatable)")
    args = parser.parse_args(argv)
    operation = args.operation if args.command == "migration" else args.command
    try:
        if args.command == "replay-resource":
            from .application import replay as replay_resource
            result = replay_resource(args.bindings, args.assets, args.database, args.out, code_commit=args.code_commit)
        else:
            result = _run_migration(args)
    except (OSError, ValueError) as error:
        print(f"{operation}: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"operation": operation, "status": result["status"],
                      "receipt_directory": str(args.out)}, ensure_ascii=False))
    return EXIT_CODES[result["status"]]


def _run_migration(args) -> dict:
    match args.operation:
        case "audit":
            from .migration.audit import audit
            return audit(args.v2_root, args.out)
        case "build-golden":
            from .migration.golden import build_golden
            return build_golden(args.v2_root, args.out, args.assets)
        case "verify-crosswalk":
            from .migration.crosswalk import verify_crosswalk
            return verify_crosswalk(args.v2_root, args.out)
        case "verify-baseline":
            from .migration.baseline import verify_baseline
            return verify_baseline(args.v2_root, args.profile, args.out, args.assets)
        case "audit-temporal":
            from .migration.temporal_audit import audit_temporal
            return audit_temporal(args.v2_root, args.expected, args.oracle_manifest, args.out)
        case "compare-oracle":
            from .migration.reference_oracle import compare_files
            return compare_files(args.expected, args.actual, args.out, case_ids=args.case_id)
    raise ValueError(f"Unrecognized migration operation: {args.operation}")
