"""Compare recomputed point and sensitivity ranks with frozen V2 matrices."""

from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import platform
from time import perf_counter

from ..decision import LEAVE_OUT_VERSION, POINT_METHODS, POINT_METHOD_VERSION, leave_one_out, rank_panel
from .baseline import BASELINES, PHASE22_SUBSET, PinnedRepository, checked_csv
from .reference_oracle import compare_files


SUBSET_DIR = str(PurePosixPath(BASELINES["snapshot_lock"]).parent)
SUBSET_REF = BASELINES["refs"]["subset"]
PANEL_COUNT = BASELINES["profiles"][PHASE22_SUBSET]["historical_panel_count"]
INPUT_ARTIFACTS = ("matrix", "criteria", "weights", "runtime")


def _frozen_blob(repository, path, receipt):
    data = repository.read("subset", path)
    receipt[path] = {
        "sha256": sha256(data).hexdigest() if data is not None else None,
        "status": "PASS" if data is not None else "BLOCKED",
    }
    return data


def _panel_paths(repository):
    paths = repository.git("ls-tree", "-r", "--name-only", "-z", SUBSET_REF, SUBSET_DIR)
    if paths is None:
        return None
    root = PurePosixPath(SUBSET_DIR)
    return tuple(sorted({
        PurePosixPath(path.decode()).parent.name
        for path in paths.split(b"\0")
        if path.endswith(b"/ranking.csv") and PurePosixPath(path.decode()).parent.parent == root
    }))


def _index(rows, *fields, required=True):
    index = {}
    for row in rows:
        key = tuple(row[field] for field in fields)
        if not all(key) or key in index:
            raise ValueError(f"Missing or duplicate {fields}: {key!r}")
        index[key] = row
    if not index and required:
        raise ValueError(f"Expected rows keyed by {fields}")
    return index


def _policy(runtime, configuration):
    if len(runtime) != 1:
        raise ValueError("Expected one frozen runtime row per panel")
    record, = runtime
    draw = configuration["monte_carlo"]
    pairs = {
        "threshold": configuration["promethee_threshold"],
        "seed": draw["seed"],
        "reps": draw["reps"],
        "concentration": draw["dirichlet_concentration"],
        "method_version": draw["method_version"],
    }
    for field, configured in pairs.items():
        actual = record[field]
        if isinstance(configured, (int, float)):
            same = Decimal(actual) == Decimal(str(configured))
        else:
            same = actual == configured
        if not same:
            raise ValueError(f"Frozen runtime {field} does not match frozen research configuration")
    return record


def _rank_token(rank, *, change=False):
    number = Decimal(str(rank))
    if (not number.is_finite() or not change and number <= 0
            or number * 2 != (number * 2).to_integral_value()):
        raise ValueError("Invalid integer or half-integer rank value")
    return format(number.normalize(), "f") if number else "0"


def _panel_input(name, tables, configuration):
    runtime = _policy(tables["runtime"], configuration)
    criteria = {code: row for (code,), row in sorted(_index(tables["criteria"], "code").items())}
    projects = _index(tables["matrix"], "project_id")
    if "canonical_project_id" in tables["matrix"][0]:
        _index(tables["matrix"], "canonical_project_id")
    matrix = {
        project_id: {criterion: float(row[criterion]) for criterion in criteria}
        for (project_id,), row in projects.items()
    }
    weights = defaultdict(dict)
    for (scenario, criterion), row in _index(tables["weights"], "scenario", "criterion").items():
        weights[scenario][criterion] = float(row["weight"])
    if any(scenario_weights.keys() != set(criteria) for scenario_weights in weights.values()):
        raise ValueError("Frozen scenario weights disagree with the declared criteria")
    metadata = {
        "panel": name,
        "input_status": "PASS",
        "projects": len(projects),
        "criteria": list(criteria),
        "scenarios": sorted(weights),
        "frozen_runtime": runtime,
    }
    return metadata, criteria, projects, matrix, weights


def _point_rows(name, tables, provenance, configuration):
    metadata, _, projects, matrix, weights = _panel_input(name, tables, configuration)
    threshold = float(metadata["frozen_runtime"]["threshold"])
    start = perf_counter()
    results = {scenario: rank_panel(matrix, weights[scenario], threshold) for scenario in sorted(weights)}
    metadata["python_point_elapsed_s"] = perf_counter() - start
    expected = [
        _expected_row(name, scenario, project_id, archived, provenance)
        for (scenario, project_id), archived in sorted(
            _index(tables["ranking"], "scenario", "project_id").items()
        )
    ]
    actual = [
        _actual_row(name, scenario, project_id, row, methods)
        for scenario, methods in results.items()
        for (project_id,), row in sorted(projects.items())
    ]
    return metadata, expected, actual


def _leave_out_rows(name, tables, provenance, configuration):
    metadata, criteria, _, matrix, weights = _panel_input(name, tables, configuration)
    threshold = float(metadata["frozen_runtime"]["threshold"])
    domains = {code: row["domain"] for code, row in criteria.items()}
    start = perf_counter()
    results = {
        scenario: leave_one_out(matrix, weights[scenario], domains, threshold)
        for scenario in sorted(weights)
    }
    metadata["python_leave_out_elapsed_s"] = perf_counter() - start
    candidates = (
        {("criterion", code) for code in criteria}
        | {("domain", domain) for domain in domains.values()}
    )
    metadata["leave_out_variants"] = sum(len(removals) for removals in results.values())
    metadata["ineligible_removals"] = [
        {"scenario": scenario, "type": kind, "removed": removed,
         "reason": "NO_POSITIVE_WEIGHT_REMAINS"}
        for scenario, removals in results.items()
        for kind, removed in sorted(candidates - removals.keys())
    ]
    ranking_path = f"{SUBSET_DIR}/{name}/leave_out.csv"
    expected = [
        {
            "case_id": "/".join((name, *key)),
            "expected_source": {"git_ref": SUBSET_REF, "path": ranking_path},
            "evidence_tier": "FROZEN_V2_LEAVE_OUT_RANKING",
            "reference_sha256": provenance[ranking_path]["sha256"],
            "values": _leave_out_values(row["rank"], row["rank_change"]),
        }
        for key, row in sorted(_index(
            tables["leave_out"], "scenario", "type", "removed", "project_id", required=False,
        ).items())
    ]
    actual = [
        {
            "case_id": "/".join((name, scenario, kind, removed, project_id)),
            "v3_reference": {"engine": LEAVE_OUT_VERSION},
            "values": _leave_out_values(change.rank, change.rank_change),
        }
        for scenario, removals in results.items()
        for (kind, removed), projects in sorted(removals.items())
        for project_id, change in sorted(projects.items())
    ]
    return metadata, expected, actual


def _leave_out_values(rank, change):
    return {"rank": _rank_token(rank), "rank_change": _rank_token(change, change=True)}


def _row_values(metadata, scores):
    return {
        "project_name": metadata["project_name"],
        "scope": metadata["scope"],
        "methods": scores,
    }


def _expected_row(panel, scenario, project_id, row, provenance):
    ranking_path = f"{SUBSET_DIR}/{panel}/ranking.csv"
    scores = {
        method: {"score": float(row[method]), "rank": _rank_token(row[f"{method}_rank"])}
        for method in POINT_METHODS
    }
    return {
        "case_id": f"{panel}/{scenario}/{project_id}",
        "expected_source": {"git_ref": SUBSET_REF, "path": ranking_path},
        "evidence_tier": "FROZEN_V2_POINT_RANKING",
        "reference_sha256": provenance[ranking_path]["sha256"],
        "values": _row_values(row, scores),
        "comparison_rules": {
            f"/methods/{method}/score": {"kind": "computed_float", "method": method}
            for method in POINT_METHODS
        },
    }


def _actual_row(panel, scenario, project_id, row, results):
    points = {method: values[project_id] for method, values in results.items()}
    scores = {method: {"score": point.score, "rank": _rank_token(point.rank)}
              for method, point in points.items()}
    return {
        "case_id": f"{panel}/{scenario}/{project_id}",
        "v3_reference": {"engine": POINT_METHOD_VERSION},
        "values": _row_values(row, scores),
    }


def _jsonl(path, rows):
    with path.open("w", encoding="utf-8") as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def verify_point_mcda(v2_root: Path, out: Path) -> dict:
    """Compare derivative point ranking against the 2026-11-05 frozen archive."""
    start = perf_counter()
    report, expected, actual = _verify_ranks(
        v2_root, out, "point_mcda", POINT_METHOD_VERSION,
        "Frozen benefit matrices to point ranking only", "ranking", _point_rows,
    )
    report["monte_carlo_and_sensitivity"] = "NOT_RUN"
    report["point_scores_and_ranks_matched"] = report["compared_ranking_rows"] * len(POINT_METHODS)
    report["score_and_tie_statistics"] = _statistics(expected, actual)
    report["benefit_matrix_cells"] = sum(
        panel["projects"] * len(panel["criteria"])
        for panel in report["panels"] if panel["input_status"] == "PASS"
    )
    report["python_point_elapsed_s"] = sum(
        panel.get("python_point_elapsed_s", 0) for panel in report["panels"]
    )
    return _write_verification(report, out / "point_mcda_verification.json", start)


def verify_leave_one_out(v2_root: Path, out: Path) -> dict:
    """Compare frozen-matrix PROMETHEE II leave-out ranks and exact changes."""
    start = perf_counter()
    report, expected, _ = _verify_ranks(
        v2_root, out, "leave_out", LEAVE_OUT_VERSION,
        "Frozen benefit matrices to criterion and domain leave-out ranks only",
        "leave_out", _leave_out_rows,
    )
    report["monte_carlo"] = "NOT_RUN"
    report["frozen_rows_by_removal_type"] = dict(Counter(
        row["case_id"].split("/")[2] for row in expected
    ))
    report["frozen_rows_with_changed_rank"] = sum(
        row["values"]["rank_change"] != "0" for row in expected
    )
    report["eligible_removal_variants"] = sum(
        panel.get("leave_out_variants", 0) for panel in report["panels"]
    )
    report["ineligible_removal_variants"] = sum(
        len(panel.get("ineligible_removals", ())) for panel in report["panels"]
    )
    report["python_leave_out_elapsed_s"] = sum(
        panel.get("python_leave_out_elapsed_s", 0) for panel in report["panels"]
    )
    return _write_verification(report, out / "leave_out_verification.json", start)


def _verify_ranks(v2_root, out, name, method, scope, expected_result, compute_rows):
    if out.resolve().is_relative_to(v2_root.resolve()):
        raise ValueError("Write migration reports outside the V2 reference checkout")
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": f"{name.replace('_', '-')}-verification-v1",
        "profile": PHASE22_SUBSET,
        "research_cutoff": BASELINES["profiles"][PHASE22_SUBSET]["cutoff"],
        "v2_ref": SUBSET_REF,
        "v3_method": method,
        "expected_panel_count": PANEL_COUNT,
        "scope": scope,
        "historical_source_replay": "NOT_RUN",
        "matrix_generation": "NOT_RUN",
        "environment": {"python": platform.python_version(), "os": platform.platform()},
        "artifacts": {},
        "panels": [],
    }
    repository = PinnedRepository(v2_root)
    expected, actual = _verify_panels(repository, report, expected_result, compute_rows)
    expected_path = out / f"{name}_expected.jsonl"
    actual_path = out / f"{name}_actual.jsonl"
    _jsonl(expected_path, expected)
    _jsonl(actual_path, actual)
    oracle = compare_files(expected_path, actual_path, out)
    report["oracle_status"] = oracle["status"]
    report["oracle_case_counts"] = oracle["counts"]
    _summarize_panel_matches(report, oracle)
    report["compared_ranking_rows"] = oracle["verified_reference_count"]
    return report, expected, actual


def _write_verification(report, path, start):
    report["elapsed_s"] = perf_counter() - start
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return report


def _statistics(expected, actual):
    matches = {row["case_id"]: row["values"]["methods"] for row in actual}
    largest = {method: None for method in POINT_METHODS}
    frozen_ties, computed_ties = defaultdict(list), defaultdict(list)
    for row in expected:
        panel_and_scenario, project = row["case_id"].rsplit("/", 1)
        for method, point in row["values"]["methods"].items():
            frozen_ties[(panel_and_scenario, method, point["rank"])].append(project)
            if row["case_id"] in matches:
                delta = abs(point["score"] - matches[row["case_id"]][method]["score"])
                largest[method] = max(delta, largest[method] or 0)
    for case_id, points in matches.items():
        panel_and_scenario, project = case_id.rsplit("/", 1)
        for method, point in points.items():
            computed_ties[(panel_and_scenario, method, point["rank"])].append(project)
    expected_groups = {key: sorted(group) for key, group in frozen_ties.items() if len(group) > 1}
    actual_groups = {key: sorted(group) for key, group in computed_ties.items() if len(group) > 1}
    return {
        "max_abs_score_difference": largest,
        "expected_tie_groups": len(expected_groups),
        "actual_tie_groups": len(actual_groups),
        "matching_tie_groups": sum(group == actual_groups.get(key)
                                   for key, group in expected_groups.items()),
    }


def _verify_panels(repository, report, expected_result, compute_rows):
    artifacts = report["artifacts"]
    expected, actual = [], []
    if repository.resolve(f"{SUBSET_REF}^{{commit}}") != SUBSET_REF:
        report.update(status="BLOCKED", reason="Frozen V2 subset commit unavailable")
        return expected, actual
    names = _panel_paths(repository)
    if names is None:
        report.update(status="BLOCKED", reason="Frozen V2 subset tree unavailable")
        return expected, actual
    report["discovered_panel_count"] = len(names)
    report["status"] = "PASS" if len(names) == PANEL_COUNT else "FAIL"
    config = _frozen_blob(repository, "config/research.json", artifacts)
    if config is None:
        report.update(status="BLOCKED", reason="Frozen research configuration unavailable")
        return expected, actual
    settings = json.loads(config)
    if settings["research_evidence_cutoff"] != report["research_cutoff"]:
        report.update(status="FAIL", reason="Frozen research cutoff disagrees with subset profile")
        return expected, actual
    for name in names:
        entries = {
            label: _frozen_blob(repository, f"{SUBSET_DIR}/{name}/{label}.csv", artifacts)
            for label in (*INPUT_ARTIFACTS, expected_result)
        }
        if missing := [label for label, data in entries.items() if data is None]:
            report["panels"].append({"panel": name, "input_status": "BLOCKED", "missing": missing})
            continue
        try:
            tables = {label: checked_csv(data) for label, data in entries.items()}
            metadata, reference, computed = compute_rows(name, tables, artifacts, settings)
        except (ArithmeticError, ValueError, KeyError) as error:
            report["panels"].append({"panel": name, "input_status": "FAIL", "reason": str(error)})
        else:
            report["panels"].append(metadata)
            expected.extend(reference)
            actual.extend(computed)
    return expected, actual


def _summarize_panel_matches(report, oracle):
    cases = defaultdict(list)
    for case in oracle["cases"]:
        cases[case["case_id"].split("/", 1)[0]].append(case["status"])
    for panel in report["panels"]:
        if panel["input_status"] != "PASS":
            panel["comparison_status"] = panel["input_status"]
            continue
        statuses = cases[panel["panel"]]
        if not statuses and panel.get("leave_out_variants") == 0:
            panel["comparison_status"] = "PASS"
            panel["empty_variant_scope_verified"] = True
        else:
            panel["comparison_status"] = "PASS" if statuses and set(statuses) == {"PASS"} else "FAIL"
        panel["ranking_rows_matched"] = statuses.count("PASS")
    statuses = {panel["comparison_status"] for panel in report["panels"]} | {report["status"]}
    if oracle["status"] == "FAIL" or "FAIL" in statuses:
        report["status"] = "FAIL"
    elif "BLOCKED" in statuses:
        report["status"] = "BLOCKED"
