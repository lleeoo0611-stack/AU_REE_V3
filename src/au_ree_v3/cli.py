"""Command-line entry point for research and migration verification."""

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
    registry = commands.add_parser("registry", help="Verify the fixed research cohort")
    registry_commands = registry.add_subparsers(dest="operation", required=True)
    verify_registry = registry_commands.add_parser("import-projects", help="Verify the pinned project registry")
    verify_registry.add_argument("--v2-root", type=Path, required=True)
    verify_registry.add_argument("--profile", type=Path, required=True)
    verify_registry.add_argument("--out", type=Path, required=True)

    sources = commands.add_parser("sources", help="Discover and capture public source editions")
    source_commands = sources.add_subparsers(dest="operation", required=True)
    for operation, timeout in (("discover", 12), ("acquire", 20)):
        command = source_commands.add_parser(operation)
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--workers", type=int, default=4)
        command.add_argument("--timeout", type=int, default=timeout)
        command.add_argument("--per-project-limit", type=int, default=2)
        if operation == "acquire":
            command.add_argument("--store", type=Path, required=True, help="Content-addressed source store")
            command.add_argument("--replica", type=Path, help="Optional independent replica location")

    combine = source_commands.add_parser("combine", help="Combine immutable public capture runs")
    combine.add_argument("--profile", type=Path, required=True)
    combine.add_argument("--manifest", type=Path, action="append", required=True)
    combine.add_argument("--out", type=Path, required=True)
    combine.add_argument("--store", type=Path, required=True)
    combine.add_argument("--replica", type=Path)
    inventory = source_commands.add_parser("inventory", help="Inspect verified captured source content")
    for argument in ("manifest", "store", "out"):
        inventory.add_argument(f"--{argument}", type=Path, required=True)
    inventory.add_argument("--profile", type=Path)
    inventory.add_argument("--baseline-criteria", type=Path)
    inventory.add_argument("--alternate-manifest", type=Path, action="append", default=[])
    inventory.add_argument("--workers", type=int, default=2)
    inventory.add_argument("--ocr-model", type=Path)

    research = commands.add_parser("research", help="Build and verify the fresh research profile")
    research_commands = research.add_subparsers(dest="operation", required=True)
    extract = research_commands.add_parser("extract", help="Extract and govern fresh resource observations")
    extract.add_argument("--bindings", type=Path, required=True)
    extract.add_argument("--sources", type=Path, required=True)
    extract.add_argument("--assets", type=Path, required=True)
    extract.add_argument("--database", type=Path, required=True)
    extract.add_argument("--out", type=Path, required=True)
    extract.add_argument("--code-commit", required=True)
    build = research_commands.add_parser("build", help="Build the fresh Census and decision inputs")
    build.add_argument("--profile", type=Path, required=True)
    build.add_argument("--bindings", type=Path, required=True)
    build.add_argument("--sources", type=Path, required=True)
    build.add_argument("--assets", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--code-commit", required=True)
    archive = research_commands.add_parser("archive", help="Archive a fresh research candidate by exact bytes")
    for argument in ("profile", "bindings", "sources", "build", "store", "out"):
        archive.add_argument(f"--{argument}", type=Path, required=True)
    archive.add_argument("--repository", type=Path, default=Path.cwd())
    archive.add_argument("--replica", type=Path)
    archive.add_argument("--replica-failure-domain")

    for operation in ("replay", "verify", "rehearse-rollback"):
        command = research_commands.add_parser(operation)
        command.add_argument("--release-root", type=Path, required=True)
        command.add_argument("--release-id", required=True)
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        if operation == "replay":
            command.add_argument("--offline", action="store_true", required=True)
        if operation == "rehearse-rollback":
            command.add_argument("--previous-release-id", required=True)

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
    operation = getattr(args, "operation", args.command)
    try:
        if args.command == "replay-resource":
            from .application import replay as replay_resource
            result = replay_resource(args.bindings, args.assets, args.database, args.out, code_commit=args.code_commit)
        elif args.command == "registry":
            from .migration.project_registry import verify_reference
            result = verify_reference(args.v2_root, args.profile, args.out)
        elif args.command == "sources":
            result = _run_sources(args)
        elif args.command == "research":
            result = _run_research(args)
        else:
            result = _run_migration(args)
    except (OSError, ValueError) as error:
        print(f"{operation}: {error}", file=sys.stderr)
        return 1
    response = {"operation": operation, "status": result["status"], "receipt_directory": str(args.out)}
    for key in ("reason", "release_id", "gates", "F4", "F5"):
        if key in result:
            response[key] = result[key]
    print(json.dumps(response, ensure_ascii=False))
    return EXIT_CODES[result["status"]]


def _run_sources(args) -> dict:
    from .sources import acquire_sources, combine_acquisition, discover_sources

    if args.operation == "inventory":
        from .source_inventory import inventory_sources

        return inventory_sources(args.manifest, args.store, args.out, profile_path=args.profile,
                                 baseline_criteria=args.baseline_criteria,
                                 alternate_manifests=tuple(args.alternate_manifest), workers=args.workers,
                                 ocr_model=args.ocr_model)
    if args.operation == "combine":
        return combine_acquisition(args.profile, args.manifest, args.out, store=args.store, replica=args.replica)
    limits = {"workers": args.workers, "timeout": args.timeout, "per_project_limit": args.per_project_limit}
    if args.operation == "discover":
        return discover_sources(args.profile, args.out, **limits)
    return acquire_sources(args.profile, args.out, store=args.store, replica=args.replica, **limits)


def _run_research(args) -> dict:
    if args.operation == "extract":
        from .fresh_observations import extract_sources

        return extract_sources(args.bindings, args.sources, args.assets, args.database, args.out, code_commit=args.code_commit)

    if args.operation != "build":
        return _run_publication(args)

    from .fresh_research import build_research

    result = build_research(
        profile=args.profile, bindings=args.bindings, sources=args.sources, assets=args.assets,
        out=args.out, code_commit=args.code_commit,
    )
    statuses = set(result["gates"].values())
    status = "FAIL" if "FAIL" in statuses else "PASS" if statuses == {"PASS"} else "BLOCKED"
    return {"status": status, **result}


def _run_publication(args) -> dict:
    from .publication import archive_release, rehearse_rollback, replay_release
    from .sources import ContentStore

    storage = ContentStore(args.store)
    if args.operation == "archive":
        replica = ContentStore(args.replica) if args.replica else None
        return archive_release(
            repository=args.repository, release_root=args.out, profile=args.profile, bindings=args.bindings,
            sources=args.sources, build=args.build, primary=storage, replica=replica,
            replica_failure_domain=args.replica_failure_domain,
        )
    if args.operation == "rehearse-rollback":
        return rehearse_rollback(
            release_root=args.release_root, previous_release_id=args.previous_release_id,
            release_id=args.release_id, storage=storage, out=args.out,
        )
    return replay_release(release_root=args.release_root, release_id=args.release_id, storage=storage, out=args.out)


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
