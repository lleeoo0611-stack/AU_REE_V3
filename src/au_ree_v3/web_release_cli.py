"""CLI entry points for governed research and website publication."""

from pathlib import Path

from .contracts import canonical_json

OPERATIONS = frozenset({"build-site", "verify-site", "rollback-web", "snapshot-site", "publish", "rollback"})


def _paths(parser, *names, required=True):
    for name in names:
        parser.add_argument(f"--{name}", type=Path, required=required)


def add_commands(research_commands) -> None:
    build = research_commands.add_parser("build-site", help="Assemble an empty, controlled preview, or authorized static site")
    build.add_argument("--mode", choices=("empty", "candidate", "formal"), required=True)
    build.add_argument("--frontend", type=Path, default=Path("web"))
    _paths(build, "out")
    _paths(build, "data", "profile", "research-build", "release-root", "store", required=False)
    build.add_argument("--release-id")
    build.add_argument("--expected-current")

    verify = research_commands.add_parser("verify-site", help="Verify that a static site's complete upload tree is safe")
    verify.add_argument("--mode", choices=("candidate", "formal"), required=True)
    _paths(verify, "site", "out")

    snapshot = research_commands.add_parser("snapshot-site", help="Freeze a verified upload tree authorized by research publication")
    _paths(snapshot, "site", "release-root", "store", "out")
    snapshot.add_argument("--release-id", required=True)
    snapshot.add_argument("--frontend", type=Path, default=Path("web"))

    web_rollback = research_commands.add_parser("rollback-web", help="Align the site pointer with an authorized research rollback")
    _paths(web_rollback, "site", "release-root", "store", "out")
    web_rollback.add_argument("--release-id", required=True)
    web_rollback.add_argument("--expected-current", required=True)

    publish = research_commands.add_parser("publish", help="Publish an archived research release through F5")
    _paths(publish, "release-root", "store", "out")
    _paths(publish, "replica-s3-config", required=False)
    publish.add_argument("--replica-failure-domain")
    publish.add_argument("--release-id", required=True)
    publish.add_argument("--previous-release-id", required=True)
    publish.add_argument("--expected-current")

    rollback = research_commands.add_parser("rollback", help="Restore the previous F5-authorized research pointer")
    _paths(rollback, "release-root", "store", "out")
    rollback.add_argument("--expected-current", required=True)


def _receipt(out: Path, name: str, result: dict, protected: Path | None = None) -> None:
    if protected is not None and out.resolve().is_relative_to(protected.resolve()):
        raise ValueError("verification receipt must be stored outside the static upload tree")
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(canonical_json(result) + "\n", encoding="utf-8")


def _build_site(args) -> dict:
    from .sources import ContentStore
    from .web_release import build_empty_formal_site, build_preview_site, sync_formal_site

    requirements = {
        "empty": (),
        "candidate": ("data", "release_id"),
        "formal": ("profile", "research_build", "release_root", "store", "release_id"),
    }
    needed = set(requirements[args.mode])
    known = set().union(*requirements.values(), {"expected_current"})
    available = {name for name in known if getattr(args, name) is not None}
    permitted = needed | ({"expected_current"} if args.mode == "formal" else set())
    if needed - available or available - permitted:
        raise ValueError(f"invalid arguments for {args.mode} site mode")
    common = {"frontend": args.frontend, "site": args.out}
    if args.mode == "empty":
        result = build_empty_formal_site(**common)
        return {**result, "reason": result["status"], "status": "PASS"}
    if args.mode == "candidate":
        result = build_preview_site(**common, data=args.data, release_id=args.release_id)
    else:
        result = sync_formal_site(
            **common, profile=args.profile, research_build=args.research_build, release_root=args.release_root,
            primary=ContentStore(args.store), release_id=args.release_id, expected_current=args.expected_current,
        )
    return {"status": "PASS", **result}


def run(args) -> dict:
    from .publication import PublicationNotAuthorized, publish_release, rollback_release
    from .replica import S3Replica
    from .sources import ContentStore
    from .web_release import rollback_formal_site, snapshot_formal_site, verify_site

    try:
        if args.operation == "build-site":
            return _build_site(args)
        if args.operation == "verify-site":
            result = verify_site(site=args.site, mode=args.mode)
            _receipt(args.out, "site_verification.json", result, args.site)
            return result
        storage = ContentStore(args.store)
        if args.operation == "snapshot-site":
            result = snapshot_formal_site(
                site=args.site, snapshot=args.out, frontend=args.frontend, release_root=args.release_root,
                primary=storage, release_id=args.release_id,
            )
            return {**result, "release_id": args.release_id}
        if args.operation == "rollback-web":
            if args.out.resolve().is_relative_to(args.site.resolve()):
                raise ValueError("rollback receipt must be stored outside the static upload tree")
            pointer = rollback_formal_site(
                site=args.site, release_root=args.release_root, primary=storage,
                release_id=args.release_id, expected_current=args.expected_current,
            )
            result = {"status": "PASS", **pointer}
            _receipt(args.out, "site_rollback.json", result, args.site)
            return result
        if args.operation == "rollback":
            result = rollback_release(
                release_root=args.release_root, expected_current=args.expected_current, storage=storage,
            )
            _receipt(args.out, "publication_rollback.json", result)
            return {"status": "PASS", "release_id": result["after"]["current"], **result}
        replica = S3Replica.from_file(args.replica_s3_config) if args.replica_s3_config else None
        result = publish_release(
            release_root=args.release_root, release_id=args.release_id, primary=storage, replica=replica,
            replica_failure_domain=args.replica_failure_domain, out=args.out,
            previous_release_id=args.previous_release_id, expected_current=args.expected_current,
        )
        return {**result, "status": result["F5"]}
    except PublicationNotAuthorized:
        return {"status": "BLOCKED", "reason": "FORMAL_RELEASE_NOT_AUTHORIZED"}
