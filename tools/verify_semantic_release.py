"""Cold-replay a pinned V3 shadow study and seal its reproducible artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from au_ree_v3.publication import (
    ArchiveError, _artifact_path, _checked_read, _code_archive, _digest, _encode, _extract_code,
    _input_objects, _profile_artifacts, _put, _write_once, load_release, replay_release,
)
from au_ree_v3.research_environment import environment_lock
from au_ree_v3.sources import ContentStore

SCHEMA = "semantic-shadow-release-v1"
BASELINE_SQL = "observations/research.sqlite"
ARTIFACTS = {
    "resource": (
        "semantic_ablation.json", "semantic_observations.json", "shadow_contained_mass.json",
        "semantic_assessments.json", "semantic_project_pairs.json", "resource_original_reread.json",
    ),
    "technical": (
        "technical_observations.jsonl", "technical_pairs.json", "technical_oracle.json", "source_readback.json",
        "technical_pilot_receipt.json",
    ),
    "technical-holdout": (
        "technical_observations.jsonl", "technical_pairs.json", "technical_oracle.json", "source_readback.json",
        "technical_pilot_receipt.json",
    ),
}
POLICY_PATHS = (
    "src/au_ree_v3/fresh_decision_policy_v1.json", "src/au_ree_v3/semantic_vocabulary_v1.json",
    "src/au_ree_v3/semantics.py", "src/au_ree_v3/normalization.py", "src/au_ree_v3/comparability.py",
    "src/au_ree_v3/semantic_research.py", "src/au_ree_v3/technical_processing.py",
)
LOCAL_DOCUMENT_TOOLS = ("pdftotext", "pdfinfo", "pdftoppm", "pdfimages", "tesseract")

_OFFLINE_SHADOW = """
import json
import os
import resource as system_resources
import runpy
import shutil
import sys
from pathlib import Path

permitted = {Path(path).resolve() for path in json.loads(sys.argv[3]).values() if path}

def enforce_offline(event, args):
    if event.startswith("socket."):
        raise RuntimeError("network access is disabled during shadow replay")
    if event == "os.system":
        raise RuntimeError("shell execution is disabled during shadow replay")
    if event in {"subprocess.Popen", "os.spawn", "os.exec", "os.posix_spawn"}:
        executable = shutil.which(os.fsdecode(args[1] if event == "os.spawn" else args[0]))
        if executable is None or Path(executable).resolve() not in permitted:
            raise RuntimeError("non-document command is disabled during shadow replay")

sys.addaudithook(enforce_offline)
sys.path.insert(0, str(Path.cwd() / "src"))
from au_ree_v3.contracts import canonical_json
from au_ree_v3.semantic_research import run_resource_ablation
from au_ree_v3.technical_processing import run_technical_pilot

paths = {name: Path(value) for name, value in json.loads(sys.argv[1]).items()}
out = Path(sys.argv[2])
profile, sources, assets = (paths[name] for name in ("profile", "sources", "assets"))
resource = run_resource_ablation(
    profile, paths["baseline"], sources, assets, out / "resource", reread_bindings=paths.get("reread-bindings"),
)
technical = run_technical_pilot(
    profile, sources, assets, paths["technical-bindings"], out / "technical", oracle=paths.get("technical-oracle"),
)
statuses = {"resource": resource["status"], "technical": technical["status"]}
if "technical-holdout-bindings" in paths:
    result = run_technical_pilot(
        profile, sources, assets, paths["technical-holdout-bindings"], out / "technical-holdout",
        oracle=paths["technical-holdout-oracle"],
    )
    statuses["technical-holdout"] = result["status"]
if "resource-holdout" in paths:
    holdout = runpy.run_path("tools/evaluate_semantic_resource_holdout.py")
    result = holdout["evaluate"](sources, assets)
    (out / "resource_holdout.json").write_text(canonical_json(result) + "\\n", encoding="utf8")
    statuses["resource-holdout"] = result["status"]
if "development-bindings" in paths:
    from au_ree_v3.development import review_milestones
    result = review_milestones(profile, sources, assets, paths["development-bindings"], out / "development")
    statuses["development"] = result["status"]
if "gis-inventory" in paths:
    from au_ree_v3.geography import evaluate_infrastructure_candidates
    result = evaluate_infrastructure_candidates(paths["gis-inventory"], sources, out / "infrastructure.json")
    statuses["infrastructure"] = result["status"]
(out / "stage_statuses.json").write_text(canonical_json(statuses) + "\\n", encoding="utf8")
(out / "runtime_measurements.json").write_text(canonical_json({
    "python_peak_rss_kib": system_resources.getrusage(system_resources.RUSAGE_SELF).ru_maxrss,
    "document_tool_peak_rss_kib": system_resources.getrusage(system_resources.RUSAGE_CHILDREN).ru_maxrss,
}) + "\\n", encoding="utf8")
"""


def _file_sha(path: Path) -> str:
    return _digest(path.read_bytes())


def _environment_bytes() -> bytes:
    document_tools = {
        tool: _file_sha(Path(path)) for tool in LOCAL_DOCUMENT_TOOLS if (path := shutil.which(tool))
    }
    return _encode({**environment_lock(), "offline_document_tool_sha256": document_tools})


def _science_file(path: Path) -> bytes:
    if path.name != "technical_pilot_receipt.json":
        return path.read_bytes()
    result = json.loads(path.read_bytes())
    result.pop("elapsed_seconds", None)
    return _encode(result)


def _sqlite_digest(path: Path) -> bytes:
    """Export canonical hashes of SQLite logical rows, independently of file-page layout."""
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        tables = connection.execute(
            "SELECT name, sql FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        logical = {}
        for name, declaration in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(_encode(row).decode("utf8") for row in connection.execute(f"SELECT * FROM {quoted}"))
            logical[name] = {"schema_sha256": _digest(declaration.encode()), "rows": len(rows),
                             "rows_sha256": _digest("".join(rows).encode())}
    return _encode(logical)


def _outputs(root: Path, enabled: set[str]) -> dict[str, bytes]:
    artifacts = {
        f"{stage}/{name}": _science_file(root / stage / name)
        for stage in ("resource", "technical", *sorted(enabled & {"technical-holdout"}))
        for name in ARTIFACTS[stage]
    }
    for stage in ("resource", "technical", *sorted(enabled & {"technical-holdout"})):
        filename = "semantic_research.sqlite" if stage == "resource" else "technical_shadow.sqlite"
        artifacts[f"{stage}/sqlite_logical_sha256.json"] = _sqlite_digest(root / stage / filename)
    optional = {
        "resource-holdout": "resource_holdout.json", "development": "development/development_milestones.json",
        "infrastructure": "infrastructure.json",
    }
    for stage in sorted(enabled & optional.keys()):
        file = optional[stage]
        artifacts[file] = (root / file).read_bytes()
    artifacts["stage_statuses.json"] = (root / "stage_statuses.json").read_bytes()
    return artifacts


def _run_detached(
    *, code_archive: bytes, temporary: Path, inputs: dict[str, Path], out: Path,
) -> tuple[dict[str, bytes], dict]:
    code = temporary / "code"
    _extract_code(code_archive, code)
    out.mkdir()
    started = perf_counter()
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _OFFLINE_SHADOW,
         json.dumps({name: str(path.resolve()) for name, path in inputs.items()}), str(out.resolve()),
         json.dumps({tool: shutil.which(tool) for tool in LOCAL_DOCUMENT_TOOLS})],
        cwd=code, check=False, capture_output=True, text=True, timeout=900,
    )
    if completed.returncode:
        raise ArchiveError(f"shadow rebuild failed: {completed.stderr.strip()[-2000:]}")
    statuses = json.loads((out / "stage_statuses.json").read_bytes())
    return _outputs(out, set(statuses)), {
        "elapsed_seconds": perf_counter() - started, "statuses": statuses,
        **json.loads((out / "runtime_measurements.json").read_bytes()),
    }


def _archive_inputs(args, cas: ContentStore, formal: dict) -> tuple[dict[str, str], dict[str, str], list[str]]:
    primary = ContentStore(args.assets)
    manifest = json.loads(args.sources.read_bytes())
    raw_ids = {entry["sha256"] for entry in manifest["editions"]}
    if len(raw_ids) != 144:
        raise ArchiveError("semantic release requires the pinned 144-source corpus")
    source_objects = sorted(_input_objects(manifest, set()))
    for identity in source_objects:
        _put(cas, _checked_read(primary, identity))

    declared = {"profile": args.profile, "sources": args.sources, "technical-bindings": args.technical_bindings}
    for label in (
        "reread-bindings", "technical-oracle", "technical-holdout-bindings", "technical-holdout-oracle",
        "development-bindings", "gis-inventory",
    ):
        path = getattr(args, label.replace("-", "_"))
        if path is not None:
            declared[label] = path
    inputs = {name: _put(cas, path.read_bytes()) for name, path in declared.items()}
    if args.resource_holdout:
        inputs["resource-holdout"] = _put(cas, _encode({"stage": "resource-holdout", "enabled": True}))
    if any(inputs[name] != formal["input_artifacts"][name] for name in ("profile", "sources")):
        raise ArchiveError("shadow profile or source manifest differs from the frozen formal study")

    raw_profile = json.loads(_checked_read(cas, inputs["profile"]))
    configuration = _profile_artifacts(args.profile, raw_profile, cas)
    baseline_files = {"build_receipt.json": formal["build_receipt_sha256"], **formal["output_hashes"]}
    for name, expected in baseline_files.items():
        if _put(cas, _artifact_path(args.baseline, name).read_bytes()) != expected:
            raise ArchiveError(f"formal baseline bytes differ for {name}")
    if any((args.baseline / (BASELINE_SQL + suffix)).exists() for suffix in ("-wal", "-journal")):
        raise ArchiveError("close the formal baseline SQLite writer before pinning its database")
    inputs["baseline-sqlite"] = _put(cas, (args.baseline / BASELINE_SQL).read_bytes())
    return inputs, configuration, source_objects


def _stage_inputs(
    temporary: Path, cas: ContentStore, inputs: dict[str, str], configuration: dict[str, str],
    source_objects: list[str], formal: dict,
) -> dict[str, Path]:
    assets = temporary / "assets"
    destination = ContentStore(assets)
    for identity in source_objects:
        _put(destination, _checked_read(cas, identity))
    bindings = temporary / "bindings"
    bindings.mkdir()
    paths = {}
    for name, identity in inputs.items():
        if name == "baseline-sqlite":
            continue
        path = bindings / (name + ".json")
        path.write_bytes(_checked_read(cas, identity))
        paths[name] = path
    for name, identity in configuration.items():
        path = _artifact_path(bindings, name)
        if path.exists():
            raise ArchiveError("profile component conflicts with another frozen input")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_checked_read(cas, identity))
    baseline = temporary / "formal-input"
    for name, identity in {
        "build_receipt.json": formal["build_receipt_sha256"], **formal["output_hashes"],
        BASELINE_SQL: inputs["baseline-sqlite"],
    }.items():
        path = _artifact_path(baseline, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_checked_read(cas, identity))
    return {**paths, "baseline": baseline, "assets": assets}


def _verify_formal_inputs(args, formal, sql_sha256, pointer_before):
    for name, expected in {"build_receipt.json": formal["build_receipt_sha256"], **formal["output_hashes"]}.items():
        if _file_sha(_artifact_path(args.baseline, name)) != expected:
            raise ArchiveError(f"formal input changed during the replay: {name}")
    if _file_sha(args.baseline / BASELINE_SQL) != sql_sha256:
        raise ArchiveError("formal baseline SQLite changed during the replay")
    current = args.formal_release_root / "current.json"
    if (current.read_bytes() if current.exists() else None) != pointer_before:
        raise ArchiveError("formal release pointer changed during the replay")


def verify_shadow_archive(cas: ContentStore, identity: str) -> dict:
    """Read back the canonical manifest and all content-addressed release objects."""
    payload = _checked_read(cas, identity)
    release = json.loads(payload)
    if release.get("schema") != SCHEMA or release.get("mode") != "SHADOW" or _encode(release) != payload:
        raise ArchiveError("shadow release manifest is invalid or noncanonical")
    formal = json.loads(_checked_read(cas, release["formal_release_id"]))
    if (
        formal["output_hashes"] != release["formal_output_hashes"]
        or formal["build_receipt_sha256"] != release["formal_build_receipt_sha256"]
        or (formal["cutoff"], formal["timezone"]) != (release["cutoff"], release["timezone"])
    ):
        raise ArchiveError("shadow manifest differs from the preserved formal reference")
    single = (
        identity, release["code_archive_sha256"], release["verification_program_sha256"],
        release["environment_sha256"], release["formal_release_id"], release["formal_build_receipt_sha256"],
    )
    required = set(single) | set(release["source_object_sha256"])
    for field in ("profile_artifacts", "policy_artifacts", "formal_output_hashes", "input_artifacts", "output_hashes"):
        required.update(release[field].values())
    for sha256 in sorted(required - {identity, release["formal_release_id"]}):
        _checked_read(cas, sha256)
    return {"status": "PASS", "exact_objects": len(required), "shadow_release_id": identity}


def verify(args) -> dict:
    started = perf_counter()
    for source in (args.formal_release_root, args.formal_store or args.assets, args.assets, args.baseline):
        if args.out.resolve().is_relative_to(source.resolve()):
            raise ArchiveError("shadow output must be outside formal and input paths")
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ArchiveError("shadow verification requires an empty output directory")
    storage = ContentStore(args.formal_store or args.assets)
    candidate = ContentStore(args.out / "shadow-store")
    pointer = args.formal_release_root / "current.json"
    before = pointer.read_bytes() if pointer.exists() else None
    formal = load_release(args.formal_release_root, args.formal_release_id, storage)
    formal_receipt = replay_release(
        release_root=args.formal_release_root, release_id=args.formal_release_id,
        storage=storage, out=args.out / "frozen-formal-replay",
    )
    if formal_receipt["F4"] != "PASS":
        return {"status": formal_receipt["F4"], "O4": formal_receipt["F4"],
                "F5": "BLOCKED", "reason": "FORMAL_REFERENCE_REPLAY_NOT_VERIFIED"}

    inputs, config, source_objects = _archive_inputs(args, candidate, formal)
    tar_bytes = _code_archive(args.repository, args.code_commit)
    code_sha = _put(candidate, tar_bytes)
    archive_id = _put(candidate, _checked_read(storage, args.formal_release_id))
    imported_tool = _put(candidate, Path(__file__).read_bytes())
    environment = _put(candidate, _environment_bytes())
    receipts, scientific = [], []
    for label in ("clean-a", "clean-b"):
        with TemporaryDirectory(prefix="ontology-replay-") as folder:
            workspace = Path(folder)
            paths = _stage_inputs(workspace, candidate, inputs, config, source_objects, formal)
            artifacts, receipt = _run_detached(
                code_archive=tar_bytes, temporary=workspace, inputs=paths, out=args.out / label,
            )
            if label == "clean-a":
                policy = {
                    name: _put(candidate, _artifact_path(workspace / "code", name).read_bytes())
                    for name in POLICY_PATHS
                }
            scientific.append(artifacts)
            receipts.append(receipt)
    _verify_formal_inputs(args, formal, inputs["baseline-sqlite"], before)
    if load_release(args.formal_release_root, args.formal_release_id, storage) != formal:
        raise ArchiveError("formal manifest changed during the replay")

    left, right = scientific
    differences = [
        {"artifact": path, "first": _digest(left[path]) if path in left else None,
         "second": _digest(right[path]) if path in right else None}
        for path in sorted(left.keys() | right.keys()) if left.get(path) != right.get(path)
    ]
    summary = {
        "status": "FAIL" if differences else "PASS", "O4": "FAIL" if differences else "PASS",
        "F5": "BLOCKED", "F5_reason": "INDEPENDENT_REMOTE_RESTORE_UNVERIFIED",
        "formal_release_id": args.formal_release_id, "formal_artifacts_exact": len(formal_receipt["actual"]),
        "formal_pointer_unchanged": True, "formal_baseline_sqlite_unchanged": True,
        "code_commit": args.code_commit, "differences": differences, "runs": receipts,
        "scope": "Deterministic reproducibility of evaluated states and scientific artifacts",
    }
    if differences or any("FAIL" in run["statuses"].values() for run in receipts):
        return {**summary, "status": "FAIL", "O4": "FAIL"}
    if any(run["statuses"].get(stage) != "PASS" for run in receipts for stage in ("resource", "technical")):
        return {**summary, "status": "BLOCKED", "O4": "BLOCKED", "reason": "REQUIRED_SHADOW_STAGE_INCOMPLETE"}

    release = {
        "schema": SCHEMA, "mode": "SHADOW", "policy_artifacts": policy,
        "code_commit": args.code_commit, "code_archive_sha256": code_sha,
        "verification_program_sha256": imported_tool, "environment_sha256": environment,
        "formal_release_id": archive_id, "formal_build_receipt_sha256": formal["build_receipt_sha256"],
        "formal_output_hashes": formal["output_hashes"],
        "cutoff": formal["cutoff"], "timezone": formal["timezone"],
        "input_artifacts": inputs, "profile_artifacts": config, "source_object_sha256": source_objects,
        "output_hashes": {name: _put(candidate, data) for name, data in sorted(left.items())},
        "stage_statuses": receipts[0]["statuses"],
        "formal_admissions_added": 0, "F5": "BLOCKED",
    }
    payload = _encode(release)
    identity = _put(candidate, payload)
    readback = verify_shadow_archive(candidate, identity)
    _write_once(args.out / "shadow-releases" / f"{identity}.json", payload)
    return {
        **summary, "shadow_release_id": identity, "shadow_artifacts_verified": len(left),
        "shadow_archive_readback": readback, "total_elapsed_seconds": perf_counter() - started,
    }


def replay_shadow(*, store: Path, identity: str, out: Path) -> dict:
    """Restore and cold-run one sealed shadow study from its archive only."""
    if out.resolve().is_relative_to(store.resolve()):
        raise ArchiveError("shadow replay output must be outside its immutable store")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ArchiveError("shadow replay requires an empty output directory")
    cas = ContentStore(store)
    readback = verify_shadow_archive(cas, identity)
    manifest = json.loads(_checked_read(cas, identity))
    result = {"shadow_release_id": identity, "shadow_archive_readback": readback, "F5": "BLOCKED"}
    if _digest(_environment_bytes()) != manifest["environment_sha256"]:
        return {**result, "status": "BLOCKED", "O4": "BLOCKED", "reason": "LOCKED_ENVIRONMENT_DIFFERS"}
    if _file_sha(Path(__file__)) != manifest["verification_program_sha256"]:
        return {**result, "status": "BLOCKED", "O4": "BLOCKED", "reason": "USE_ARCHIVED_VERIFICATION_PROGRAM"}
    formal = json.loads(_checked_read(cas, manifest["formal_release_id"]))
    with TemporaryDirectory(prefix="ontology-sealed-replay-") as folder:
        workspace = Path(folder)
        paths = _stage_inputs(
            workspace, cas, manifest["input_artifacts"], manifest["profile_artifacts"],
            manifest["source_object_sha256"], formal,
        )
        artifacts, elapsed = _run_detached(
            code_archive=_checked_read(cas, manifest["code_archive_sha256"]),
            temporary=workspace, inputs=paths, out=out / "restored",
        )
    expected = manifest["output_hashes"]
    actual = {name: _digest(data) for name, data in artifacts.items()}
    differences = [
        {"artifact": name, "expected": expected.get(name), "actual": actual.get(name)}
        for name in sorted(expected.keys() | actual.keys()) if expected.get(name) != actual.get(name)
    ]
    status = "FAIL" if differences else "PASS"
    return {
        **result, "status": status, "O4": status, "differences": differences, "artifact_count": len(actual),
        "run": elapsed,
    }


def _report(out: Path, filename: str, operation) -> int:
    try:
        summary = operation()
    except FileNotFoundError as error:
        summary = {"status": "BLOCKED", "O4": "BLOCKED", "F5": "BLOCKED", "reason": str(error)}
    except (ArchiveError, OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        summary = {"status": "FAIL", "O4": "FAIL", "F5": "BLOCKED", "reason": str(error)}
    out.mkdir(parents=True, exist_ok=True)
    _write_once(out / filename, _encode(summary))
    print(json.dumps(summary, ensure_ascii=False))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[summary["status"]]


def _replay_cli(argv) -> int:
    parser = argparse.ArgumentParser(description=replay_shadow.__doc__)
    parser.add_argument("--shadow-store", required=True, type=Path)
    parser.add_argument("--shadow-release-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(args.shadow_store.resolve()):
        parser.error("choose a replay output directory outside the shadow store")
    return _report(args.out, "shadow_replay_receipt.json", lambda: replay_shadow(
        store=args.shadow_store, identity=args.shadow_release_id, out=args.out,
    ))


def cli(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["replay"]:
        return _replay_cli(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__, epilog="Use the replay subcommand for an existing shadow release.")
    for name in ("baseline", "profile", "sources", "assets", "formal-release-root", "technical-bindings", "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--formal-release-id", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    for name in (
        "formal-store", "reread-bindings", "technical-oracle", "technical-holdout-bindings",
        "technical-holdout-oracle", "development-bindings", "gis-inventory",
    ):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--resource-holdout", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.technical_holdout_bindings) != bool(args.technical_holdout_oracle):
        parser.error("provide both technical holdout bindings and their independent oracle")
    if any(args.out.resolve().is_relative_to(p.resolve()) for p in (
        args.assets, args.baseline, args.formal_release_root, args.formal_store or args.assets,
    )):
        parser.error("choose an output directory outside all formal and source inputs")
    return _report(args.out, "semantic_release_verification.json", lambda: verify(args))


if __name__ == "__main__":
    sys.exit(cli())
