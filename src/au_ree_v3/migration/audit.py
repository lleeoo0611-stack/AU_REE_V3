"""Inventory the immutable V2 engineering tree and its executable entry points."""

import ast
from collections import Counter, defaultdict
import csv
from graphlib import CycleError, TopologicalSorter
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
from urllib.parse import unquote

from .audit_capabilities import CAPABILITIES, CAPABILITY_FIELDS, TOOL_CAPABILITIES, V3_VERIFICATIONS

ENGINEERING_REF = "2af6f611e51eb4ac9dda73419d1afeba70d1307e"
ENGINEERING_TREE = "8ac9cc5713e0d3437f956aed8efb84ef6ae1b5f5"
PACKAGE = "au_ree_v2"
IN_SCOPE = {"src", "tools", "config", "data", "R", "tests", "docs", ".github", "results"}
ROOT_INPUTS = {"README.md", "pyproject.toml", "requirements-constraints.txt"}
ARCHIVED_MODULE_AUDIT = "results/phase10-2026-11-27/phase10_module_audit.json"
REPLAY_INDEX_NAMES = {
    "asset_manifest.json", "bundle_verification.json", "census_release.json",
    "phase10_baseline.json", "phase10_module_audit.json", "repository_integration.json",
    "snapshot_lock.json", "source_recovery_manifest.json", "verification.json",
    "verified-subset.json",
}
FILE_FIELDS = (
    "source_path", "source_commit", "source_blob_sha", "role", "used_by",
    "dynamic_references", "target_path", "disposition", "reason", "verification",
)
PATH_TOKEN = re.compile(
    r"(?<![\w/.-])(?:[\w./%+@()-]+/)*[\w%+@()-][\w.%+@()-]*\."
    r"(?:py|R|yaml|yml|jsonl?|csv|tsv|toml|md|txt|traineddata|pdf|zip|geojson)\b"
)
QUOTED_PATH = re.compile(r"""['"`]([^'"`\n]+\.[\w]{1,12})['"`]""")
R_SOURCE = re.compile(r"""\bsource\s*\(\s*['"]([^'"]+)['"]""")
TOOL_INVOCATION = re.compile(r"\bpython(?:3(?:\.\d+)?)?\s+-m\s+tools\.(\w+)")
CLI_INVOCATION = re.compile(r"\b(?:au-ree-v2|python(?:3)?\s+-m\s+au_ree_v2)\s+([\w-]+)")
DYNAMIC_CALLS = {
    "import_module", "__import__", "getattr", "glob", "rglob", "open", "read_text",
    "read_bytes", "read_csv", "read_json", "write_csv", "write_json", "run_path",
}
DUPLICATE_MIN_NODES = 16


def _git(root, *args, input_data=None):
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), *args],
        input=input_data, capture_output=True, check=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
    ).stdout


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _tree(root):
    entries = {}
    records = _git(root, "ls-tree", "-r", "-z", ENGINEERING_REF).split(b"\0")
    for record in filter(None, records):
        identity, raw_path = record.split(b"\t", 1)
        mode, kind, sha = identity.decode().split()
        path = raw_path.decode("utf-8")
        if path.split("/")[0] in IN_SCOPE and (not path.startswith(".github/") or
                                               path.startswith(".github/workflows/")):
            entries[path] = {"mode": mode, "kind": kind, "blob_sha": sha}
        elif path in ROOT_INPUTS:
            entries[path] = {"mode": mode, "kind": kind, "blob_sha": sha}
    return entries


def _scan_text(path):
    name = PurePosixPath(path)
    if name.parts[0] == "results":
        return name.suffix == ".md" or name.name in REPLAY_INDEX_NAMES
    if name.parts[0] == "data":
        return len(name.parts) == 2 and name.suffix in {".json", ".md", ".txt"}
    return name.suffix in {".py", ".R", ".yaml", ".yml", ".json", ".toml", ".md", ".txt"}


def _read_text_blobs(root, entries):
    wanted = {path: item["blob_sha"] for path, item in entries.items()
              if item["kind"] == "blob" and _scan_text(path)}
    data = _git(root, "cat-file", "--batch", input_data=b"".join(
        sha.encode() + b"\n" for sha in wanted.values()
    ))
    offset = 0
    sources, issues = {}, []
    for path, sha in wanted.items():
        line_end = data.index(b"\n", offset)
        header = data[offset:line_end].decode().split()
        offset = line_end + 1
        if len(header) != 3 or header[:2] != [sha, "blob"]:
            issues.append({"path": path, "reason": "Unavailable fixed blob", "header": header})
            continue
        size = int(header[2])
        value = data[offset:offset + size]
        offset += size + 1
        try:
            sources[path] = value.decode("utf-8")
        except UnicodeDecodeError:
            issues.append({"path": path, "reason": "Fixed blob is outside the UTF-8 scan scope"})
    return sources, issues


def _package_imports(tree, package, *, relative_package=False):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(package + "."):
                    names.add(alias.name.split(".")[1])
        elif isinstance(node, ast.ImportFrom):
            if relative_package and node.level == 1 and node.module:
                names.add(node.module.split(".")[0])
            elif ((relative_package and node.level == 1 and not node.module) or
                  (node.level == 0 and node.module == package)):
                names.update(alias.name for alias in node.names)
            elif node.level == 0 and node.module and node.module.startswith(package + "."):
                names.add(node.module.split(".")[1])
    return names


def _without_nested_scopes(body):
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def branch_score(node):
    """Apply V2's Phase 10 AST method to the function's own scope."""
    score = 1
    for item in _without_nested_scopes(node.body):
        if isinstance(item, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler)):
            score += 1
        elif isinstance(item, ast.BoolOp):
            score += len(item.values) - 1
        elif isinstance(item, ast.comprehension):
            score += 1 + len(item.ifs)
        elif isinstance(item, ast.Match):
            score += len(item.cases) - 1 + sum(case.guard is not None for case in item.cases)
    return score


def function_body_fingerprint(node):
    """Return the Phase 10 body fingerprint and AST size, excluding the leading docstring."""
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    normalized = ast.Module(body=body, type_ignores=[])
    nodes = sum(1 for _ in ast.walk(normalized))
    encoded = json.dumps(ast.dump(normalized), ensure_ascii=False).encode("utf-8")
    return sha256(encoded).hexdigest(), nodes


def package_metrics(trees, package=PACKAGE):
    """Measure a flat package with the fixed Phase 10 AST definitions."""
    graph = {name: sorted(_package_imports(tree, package, relative_package=True) & trees.keys())
             for name, tree in sorted(trees.items())}
    try:
        sorted_modules = list(TopologicalSorter(graph).static_order())
        cycle = None
    except CycleError as exc:
        sorted_modules, cycle = None, exc.args[1]
    complexity, bodies = [], defaultdict(list)
    for name, tree in sorted(trees.items()):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            label = {"module": name, "function": node.name, "line": node.lineno}
            complexity.append({**label, "score": branch_score(node)})
            fingerprint, nodes = function_body_fingerprint(node)
            if nodes >= DUPLICATE_MIN_NODES:
                bodies[fingerprint].append({**label, "ast_nodes": nodes})
    duplicates = {key: group for key, group in bodies.items() if len(group) > 1}
    cross_module = {key: group for key, group in duplicates.items()
                    if len({record["module"] for record in group}) > 1}
    scores = sorted(row["score"] for row in complexity)
    complexity.sort(key=lambda row: (-row["score"], row["module"], row["function"], row["line"]))
    return {
        "module_count": len(trees),
        "internal_edges": sum(map(len, graph.values())),
        "graph": graph,
        "topological_order": sorted_modules,
        "cycle_witness": cycle,
        "complexity_definition": "V2 Phase 10 branch score, excluding nested scopes",
        "functions": complexity,
        "cyclomatic_estimate": {
            "functions": len(scores),
            "p90": scores[(len(scores) * 9 - 1) // 10] if scores else None,
            "max": scores[-1] if scores else None,
            "top": complexity[:15],
        },
        "duplicate_minimum_ast_nodes": DUPLICATE_MIN_NODES,
        "duplicate_body_groups": duplicates,
        "cross_module_duplicate_groups": cross_module,
    }


def _target_path(token, user, entries, by_name):
    token = unquote(token)
    if not token or "://" in token:
        return None
    token = posixpath.normpath(token)
    if token in entries:
        return token
    relative = posixpath.normpath(str(PurePosixPath(user).parent / token))
    if relative in entries:
        return relative
    parent = PurePosixPath(user).parent
    if "/" not in token:
        unique_paths = by_name.get(token, ())
        if len(unique_paths) == 1:
            return unique_paths[0]
        while parent.parts:
            neighbor = str(parent / token)
            if neighbor in entries:
                return neighbor
            parent = parent.parent
    return None


def _call_name(value):
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return ""


def _scan_references(entries, texts, trees, module_paths):
    used_by, dynamic_at, dynamic_to = defaultdict(set), defaultdict(set), defaultdict(set)
    basenames = defaultdict(list)
    for path in entries:
        basenames[PurePosixPath(path).name].append(path)
    tool_modules = {PurePosixPath(path).stem: path for path in trees if path.startswith("tools/")}
    for path, text in texts.items():
        tokens = set(PATH_TOKEN.findall(text)) | set(QUOTED_PATH.findall(text))
        for target in filter(None, (_target_path(token, path, entries, basenames) for token in tokens)):
            if path != target:
                used_by[target].add(path)
        for token in R_SOURCE.findall(text) if path.endswith(".R") else ():
            if target := _target_path(token, path, entries, basenames):
                used_by[target].add(path)
        for name in set(TOOL_INVOCATION.findall(text)) & tool_modules.keys():
            target = tool_modules[name]
            if target != path:
                used_by[target].add(path)
        if (cli := module_paths.get("cli")) and path != cli and CLI_INVOCATION.search(text):
            used_by[cli].add(path)
        tree = trees.get(path)
        if tree is None:
            continue
        imports = _package_imports(tree, PACKAGE, relative_package=path in module_paths.values())
        for name in imports & module_paths.keys():
            target = module_paths[name]
            if target != path:
                used_by[target].add(path)
        for name in _package_imports(tree, "tools") & tool_modules.keys():
            target = tool_modules[name]
            if target != path:
                used_by[target].add(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            operation = _call_name(node.func)
            if operation not in DYNAMIC_CALLS:
                continue
            callsite = f"{path}:{node.lineno}:{operation}"
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                token = argument.value
                if operation in {"import_module", "__import__"} and token.startswith(PACKAGE + "."):
                    target = module_paths.get(token.split(".")[1])
                else:
                    target = _target_path(token, path, entries, basenames)
                if target and target != path:
                    used_by[target].add(path)
                    dynamic_to[target].add(callsite)
                elif operation in {"glob", "rglob", "import_module", "__import__", "run_path"}:
                    dynamic_at[path].add(callsite)
            else:
                dynamic_at[path].add(callsite)
    return used_by, dynamic_at, dynamic_to


def _disposition(path, used, scanned):
    name = PurePosixPath(path)
    category = name.parts[0]
    if category == "results":
        if name.name in REPLAY_INDEX_NAMES:
            return ("Frozen replay index", "KEEP", "Fixed V2 object",
                    "Retain the versioned lock or verification receipt for historical replay.")
        return ("Frozen research artifact", "ARCHIVE", "Fixed V2 object",
                "Preserve the historical input or result at its original V2 commit and blob.")
    if category == "data":
        return ("Evidence, research input or historical receipt", "KEEP", "Fixed V2 object",
                "Retain source snapshots and evidence until replay-aware review resolves their use.")
    if category == "config":
        return ("Scientific or source configuration", "KEEP", "Versioned V3 configuration",
                "Preserve the V2 rules and fingerprint while evaluating each proposed mapping.")
    if category == "tests":
        return ("Regression validation", "KEEP", "V3 parity fixtures",
                "Retain the V2 validation contract until the corresponding capability is compared.")
    if category == "docs" or path == "README.md":
        return ("Research contract or historical documentation", "KEEP", "Fixed V2 object",
                "Retain scientific assumptions, invocation examples and historical context.")
    if used or category in {"R", ".github"} or (category == "tools" and scanned):
        return ("Executable, workflow or package configuration", "REFACTOR", "Capability matrix",
                "A static reference or executable entry point identifies work for capability migration.")
    return ("Executable or build input", "KEEP", "Capability matrix",
            "Static consumption is unresolved. Preserve the fixed V2 object for further review.")


def _file_rows(entries, texts, used_by, dynamic_at, dynamic_to):
    for path, source in sorted(entries.items()):
        users = used_by[path]
        role, decision, target, reason = _disposition(path, users, path in texts)
        yield {
            "source_path": path,
            "source_commit": ENGINEERING_REF,
            "source_blob_sha": source["blob_sha"],
            "role": role,
            "used_by": " | ".join(sorted(users)),
            "dynamic_references": " | ".join(sorted(dynamic_at[path] | dynamic_to[path])),
            "target_path": target,
            "disposition": decision,
            "reason": reason,
            "verification": "FIXED_BLOB_TEXT_SCANNED" if path in texts else "FIXED_TREE_ENTRY_ONLY",
        }


def _cli_commands(tree):
    if tree is None:
        return set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "add_argument":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != "module":
            continue
        for keyword in node.keywords:
            if keyword.arg == "choices":
                return set(ast.literal_eval(keyword.value))
    return set()


def _entrypoint_files(tree_by_path, texts):
    tools = {PurePosixPath(path).stem for path in tree_by_path if path.startswith("tools/")}
    r_scripts = {path for path, txt in texts.items() if path.startswith("R/") and
                 re.search(r"\bcommandArgs\s*\(", txt)}
    workflows = {path for path in texts if path.startswith(".github/workflows/")}
    cli = _cli_commands(tree_by_path.get(f"src/{PACKAGE}/cli.py"))
    return {"cli": cli, "tools": tools, "r_scripts": r_scripts, "workflows": workflows}


def _matrix(entries, discovered, used_by):
    rows, mapped = [], set()
    for capability in CAPABILITIES:
        cap_id, domain, purpose, cli, paths, contracts, v3, decision, status, reason = capability
        tools = tuple(sorted(f"tools/{name}.py" for name, owner in TOOL_CAPABILITIES.items()
                             if owner == cap_id and name in discovered["tools"]))
        paths = tuple(path for path in paths if path in entries) + tools
        commands = tuple(command for command in cli if command in discovered["cli"])
        mapped.update(f"cli:{command}" for command in commands)
        mapped.update(f"tools:{PurePosixPath(path).stem}" for path in tools)
        mapped.update(f"r_scripts:{path}" for path in paths if path in discovered["r_scripts"])
        mapped.update(f"workflows:{path}" for path in paths if path in discovered["workflows"])
        tests = {user for path in paths for user in used_by[path] if user.startswith("tests/")}
        tests.update(path for path in paths if path.startswith("tests/"))
        paths = tuple(path for path in paths if not path.startswith("tests/"))
        implementation, fixtures, verification = V3_VERIFICATIONS.get(cap_id, ("", "", ""))
        rows.append(dict(zip(CAPABILITY_FIELDS, (
            cap_id, domain, purpose, " | ".join([*(f"au-ree-v2 {c}" for c in commands), *paths]),
            " | ".join(sorted(tests)), " | ".join(path for path in contracts if path in entries),
            ENGINEERING_REF, v3, implementation, decision, fixtures, verification, status, reason,
            v3.split(" ")[0] if v3 else "", "Sources are fixed to the engineering ref",
        ))))
    unmapped = {f"{kind}:{entry}" for kind, values in discovered.items() for entry in values} - mapped
    for entry in sorted(unmapped):
        rows.append(dict(zip(CAPABILITY_FIELDS, (
            f"unmapped-{entry}", "UNMAPPED", "Identify scientific scope from the executable",
            entry, "", "", ENGINEERING_REF, "", "", "DEFER", "", "", "NOT_RUN",
            "The executable was identified by the scan and needs capability classification.", "", "",
        ))))
    return rows, sorted(unmapped)


def _compare_archived_metrics(text, metrics):
    if text is None:
        return {"status": "BLOCKED", "reason": "Archived module audit receipt unavailable"}
    archived = json.loads(text)["worktree"]
    archived_inputs = archived["source_sha256"]
    current_inputs = metrics["source_sha256"]
    changed = sorted(path for path in archived_inputs.keys() | current_inputs.keys()
                     if archived_inputs.get(path) != current_inputs.get(path))
    source_differences = [{"path": path, "receipt_sha256": archived_inputs.get(path),
                           "fixed_commit_sha256": current_inputs.get(path)} for path in changed]
    metric_differences = {}
    for key in ("module_count", "internal_edges", "graph", "cycle_witness", "duplicate_body_groups",
                "cross_module_duplicate_groups"):
        if archived[key] != metrics[key]:
            metric_differences[key] = {"receipt": archived[key], "fixed_commit": metrics[key]}
    for key, actual in metrics["cyclomatic_estimate"].items():
        if archived["cyclomatic_estimate"][key] != actual:
            metric_differences[key] = {"receipt": archived["cyclomatic_estimate"][key],
                                       "fixed_commit": actual}
    return {
        "reference": ARCHIVED_MODULE_AUDIT,
        "status": "BLOCKED" if source_differences else ("FAIL" if metric_differences else "PASS"),
        "reason": "Archived worktree and fixed engineering commit use different source fingerprints"
        if source_differences else "Compared metrics on identical source fingerprints",
        "input_differences": source_differences,
        "metric_differences": metric_differences,
    }


def audit(v2_root, out):
    """Write a reproducible inventory from fixed Git objects. Return an audit receipt."""
    root = Path(v2_root).resolve()
    out = Path(out).resolve()
    if out.is_relative_to(root):
        raise ValueError("Write migration receipts outside the read-only V2 repository")
    out.mkdir(parents=True, exist_ok=True)
    receipt = {"source_commit": ENGINEERING_REF, "expected_source_tree": ENGINEERING_TREE}
    try:
        commit = _git(root, "rev-parse", "--verify", f"{ENGINEERING_REF}^{{commit}}").decode().strip()
        tree_sha = _git(root, "rev-parse", f"{commit}^{{tree}}").decode().strip()
        receipt["source_tree"] = tree_sha
        if commit != ENGINEERING_REF or tree_sha != ENGINEERING_TREE:
            receipt.update(status="FAIL", reason="Fixed commit or tree fingerprint differs")
            _write_json(out / "audit.json", receipt)
            return receipt
        entries = _tree(root)
        texts, issues = _read_text_blobs(root, entries)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        receipt.update(status="BLOCKED", reason="Fixed V2 Git objects are unavailable",
                       git_error=getattr(exc, "stderr", b"").decode(errors="replace").strip())
        _write_json(out / "audit.json", receipt)
        return receipt
    parsed = {}
    for path, text in texts.items():
        if path.endswith(".py"):
            try:
                parsed[path] = ast.parse(text, filename=path)
            except SyntaxError as exc:
                issues.append({"path": path, "reason": str(exc)})
    module_paths = {PurePosixPath(path).stem: path for path in parsed
                    if PurePosixPath(path).parent == PurePosixPath(f"src/{PACKAGE}")}
    metrics = package_metrics({name: parsed[path] for name, path in module_paths.items()})
    metrics["source_sha256"] = {path: sha256(texts[path].encode()).hexdigest()
                                for path in module_paths.values()}
    comparison = _compare_archived_metrics(texts.get(ARCHIVED_MODULE_AUDIT), metrics)
    used_by, dynamic_at, dynamic_to = _scan_references(entries, texts, parsed, module_paths)
    files = list(_file_rows(entries, texts, used_by, dynamic_at, dynamic_to))
    entrypoint_sets = _entrypoint_files(parsed, texts)
    matrix, unmapped = _matrix(entries, entrypoint_sets, used_by)
    _write_csv(out / "file_disposition.csv", FILE_FIELDS, files)
    _write_csv(out / "capability_matrix.csv", CAPABILITY_FIELDS, matrix)
    _write_json(out / "module_audit.json", metrics)
    _write_csv(out / "module_edges.csv", ("source", "dependency"), (
        {"source": source, "dependency": target}
        for source, targets in metrics["graph"].items() for target in targets
    ))
    _write_json(out / "unresolved_references.json", {
        "files_without_static_consumers": sorted(path for path in entries if not used_by[path]),
        "unresolved_runtime_calls": {path: sorted(values) for path, values in sorted(dynamic_at.items())
                                     if values},
        "unmapped_entrypoints": unmapped,
        "scan_issues": issues,
    })
    receipt.update(
        status="BLOCKED" if issues else "PASS",
        paths=len(entries),
        text_blobs_scanned=len(texts),
        tree_entries_without_full_content_scan=len(entries) - len(texts),
        path_counts=dict(sorted(Counter(path.split("/")[0] for path in entries).items())),
        file_dispositions=dict(sorted(Counter(row["disposition"] for row in files).items())),
        capabilities=len(matrix),
        capability_status=dict(sorted(Counter(row["parity_status"] for row in matrix).items())),
        discovered_entrypoints={key: sorted(values) for key, values in entrypoint_sets.items()},
        unmapped_entrypoints=unmapped,
        scan_issues=issues,
        metrics={key: metrics[key] for key in ("module_count", "internal_edges", "cycle_witness",
                                               "cyclomatic_estimate")},
        archived_metric_comparison=comparison,
        fixed_scope="V2 engineering tree inventory. Scientific equivalence is measured separately.",
    )
    _write_json(out / "audit.json", receipt)
    return receipt
