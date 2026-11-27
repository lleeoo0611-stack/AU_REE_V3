"""Compare frozen reference rows to V3 results without running science code."""

import csv
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from pathlib import Path

_EXPECTED_FIELDS = {
    "case_id", "values", "expected_source", "evidence_tier", "reference_sha256",
    "comparison_rules", "status", "reason", "synthetic",
}
_ACTUAL_FIELDS = {"case_id", "values", "v3_reference"}
_DIFF_FIELDS = (
    "case_id", "field", "expected", "actual", "expected_present", "actual_present",
    "expected_type", "actual_type", "comparison_rule", "severity", "source_reference",
    "v3_reference", "reason",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MISSING = object()
_DEFAULT_TOLERANCE = Decimal("1e-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _invalid_constant(token):
    raise ValueError(f"non-finite JSON number {token}")


def _load_rows(data):
    rows = {}
    for number, line in enumerate(data.decode("utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_float=Decimal, parse_constant=_invalid_constant,
                             object_pairs_hook=_object)
            if not isinstance(row, dict) or not _nonempty_text(row.get("case_id")):
                raise ValueError("case_id must be nonempty text")
            case_id = row["case_id"]
            if case_id in rows:
                raise ValueError(f"duplicate case_id {case_id!r}")
            rows[case_id] = row
        except (ValueError, ArithmeticError) as error:
            raise ValueError(f"line {number}: {error}") from error
    return rows


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _pointer(parent, key):
    return f"{parent}/" + str(key).replace("~", "~0").replace("/", "~1")


def _value_at(values, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError(f"invalid field pointer {pointer!r}")
    for fragment in pointer[1:].split("/"):
        key = fragment.replace("~1", "/").replace("~0", "~")
        if isinstance(values, dict) and key in values:
            values = values[key]
        elif (isinstance(values, list) and re.fullmatch(r"0|[1-9][0-9]*", key)
              and int(key) < len(values)):
            values = values[int(key)]
        else:
            raise ValueError(f"comparison rule targets an absent reference field {pointer!r}")
    return values


def _validate_row(row, *, expected):
    allowed = _EXPECTED_FIELDS if expected else _ACTUAL_FIELDS
    if extra := row.keys() - allowed:
        raise ValueError(f"unknown row fields {sorted(extra)}")
    if expected:
        if not isinstance(row.get("synthetic", False), bool):
            raise ValueError("synthetic must be a boolean")
        status = row.get("status", "COMPARABLE")
        if status not in ("COMPARABLE", "BLOCKED", "NOT_RUN"):
            raise ValueError(f"unknown reference status {status!r}")
        if status != "COMPARABLE":
            if not _nonempty_text(row.get("reason")):
                raise ValueError(f"{status} requires a reason")
            return
        if not _nonempty_text(row.get("evidence_tier")):
            raise ValueError("evidence_tier is required for a comparable reference")
        if row["evidence_tier"] == "SYNTHETIC" and not row.get("synthetic"):
            raise ValueError("synthetic evidence must be marked synthetic=true")
        source = row.get("expected_source")
        if not (_nonempty_text(source) or isinstance(source, dict) and source):
            raise ValueError("expected_source is required for a comparable reference")
        if "reference_sha256" in row or not row.get("synthetic"):
            sha = row.get("reference_sha256")
            if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
                raise ValueError("reference_sha256 must be a lowercase SHA-256")
    values = row.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("values must be a nonempty object")


def _validate_rules(rules, values):
    if not isinstance(rules, dict):
        raise ValueError("comparison_rules must be an object of field pointers")
    for pointer, rule in rules.items():
        if isinstance(pointer, str) and any(
            prefix in rules for prefix in _pointer_parents(pointer)
        ):
            raise ValueError(f"overlapping comparison rule at {pointer!r}")
        value = _value_at(values, pointer)
        if not isinstance(rule, dict):
            raise ValueError(f"{pointer}: comparison rule must be an object")
        kind = rule.get("kind")
        if kind == "computed_float":
            _validate_float_rule(rule, value)
        elif kind == "derived_decimal":
            _validate_decimal_rule(rule, value)
        elif kind == "unordered_set":
            if rule.keys() != {"kind"} or _set_items(value) is None:
                raise ValueError(f"{pointer}: unordered_set requires an array of distinct scalars")
        else:
            raise ValueError(f"{pointer}: unknown comparison rule {kind!r}")


def _pointer_parents(pointer):
    for offset, character in enumerate(pointer):
        if character == "/" and offset:
            yield pointer[:offset]


def _validate_float_rule(rule, value):
    allowed = {"kind", "method", "abs_tol", "rel_tol", "tolerance_reason"}
    if rule.keys() - allowed or not _nonempty_text(rule.get("method")) or not _finite_number(value):
        raise ValueError("computed_float requires a declared method and finite JSON number")
    for name in ("abs_tol", "rel_tol"):
        tolerance = rule.get(name, _DEFAULT_TOLERANCE)
        if not _finite_number(tolerance) or tolerance < 0:
            raise ValueError(f"{name} must be a finite nonnegative number")
        if tolerance != _DEFAULT_TOLERANCE and not _nonempty_text(rule.get("tolerance_reason")):
            raise ValueError(f"overridden {name} requires a tolerance_reason")


def _validate_decimal_rule(rule, value):
    places = rule.get("decimal_places")
    if (rule.keys() != {"kind", "formula_version", "decimal_places"}
            or not _nonempty_text(rule.get("formula_version"))
            or type(places) is not int or places < 0
            or _rounded_decimal(value, places) is None):
        raise ValueError("derived_decimal requires a formula version, decimal places and finite decimal")


def _finite_number(value):
    if type(value) not in (int, Decimal):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _rounded_decimal(value, places):
    if type(value) not in (int, Decimal, str):
        return None
    try:
        number = Decimal(value)
        if not number.is_finite():
            return None
        with localcontext() as context:
            context.prec = max(28, number.adjusted() + places + 3)
            return number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        return None


def _scalar_key(value):
    if isinstance(value, Decimal):
        return ("decimal", value.as_tuple())
    return (type(value).__name__, value)


def _set_items(value):
    if not isinstance(value, list) or any(isinstance(item, (list, dict)) for item in value):
        return None
    items = {_scalar_key(item) for item in value}
    return items if len(items) == len(value) else None


def _rule_matches(rule, expected, actual):
    if rule["kind"] == "computed_float":
        return _finite_number(actual) and math.isclose(
            expected, actual, abs_tol=float(rule.get("abs_tol", _DEFAULT_TOLERANCE)),
            rel_tol=float(rule.get("rel_tol", _DEFAULT_TOLERANCE)),
        )
    if rule["kind"] == "derived_decimal":
        places = rule["decimal_places"]
        return _rounded_decimal(expected, places) == _rounded_decimal(actual, places)
    return _set_items(expected) == _set_items(actual)


def _changes(expected, actual, rules, field=""):
    rule = rules.get(field)
    if expected is _MISSING or actual is _MISSING:
        yield field, expected, actual, rule, "field is absent"
    elif rule:
        if not _rule_matches(rule, expected, actual):
            yield field, expected, actual, rule, "declared comparison failed"
    elif type(expected) is not type(actual):
        yield field, expected, actual, None, "JSON types differ"
    elif isinstance(expected, dict):
        for key in sorted(expected.keys() | actual.keys()):
            yield from _changes(expected.get(key, _MISSING), actual.get(key, _MISSING),
                                rules, _pointer(field, key))
    elif isinstance(expected, list):
        for index in range(max(len(expected), len(actual))):
            left = expected[index] if index < len(expected) else _MISSING
            right = actual[index] if index < len(actual) else _MISSING
            yield from _changes(left, right, rules, _pointer(field, index))
    elif _scalar_key(expected) != _scalar_key(actual):
        yield field, expected, actual, None, "exact values differ"


def _difference(case_id, field, expected, actual, rule, reason, *, reference=None, result=None,
                severity="ERROR"):
    return {
        "case_id": case_id,
        "field": field,
        "expected": None if expected is _MISSING else expected,
        "actual": None if actual is _MISSING else actual,
        "expected_present": expected is not _MISSING,
        "actual_present": actual is not _MISSING,
        "expected_type": None if expected is _MISSING else type(expected).__name__,
        "actual_type": None if actual is _MISSING else type(actual).__name__,
        "comparison_rule": rule or {"kind": "exact"},
        "severity": severity,
        "source_reference": (reference or {}).get("expected_source"),
        "v3_reference": (result or {}).get("v3_reference"),
        "reason": reason,
    }


def _compare_one(case_id, reference, actual):
    rejected = _difference(case_id, "", _MISSING, _MISSING, None, "",
                           reference=reference, result=actual)
    try:
        if reference is not None:
            _validate_row(reference, expected=True)
        if actual is not None:
            _validate_row(actual, expected=False)
        if reference is None:
            raise ValueError("unexpected case_id")
        status = reference.get("status", "COMPARABLE")
        if status != "COMPARABLE":
            rejected.update(severity=status, reason=reference["reason"])
            return status, [rejected]
        if actual is None:
            raise ValueError("expected case_id is absent from actual results")
        rules = reference.get("comparison_rules", {})
        _validate_rules(rules, reference["values"])
    except ValueError as error:
        rejected["reason"] = str(error)
        return "FAIL", [rejected]
    differences = [
        _difference(case_id, field, left, right, rule, reason, reference=reference, result=actual)
        for field, left, right, rule, reason in _changes(reference["values"], actual["values"], rules)
    ]
    return ("FAIL" if differences else "PASS"), differences


def _write_report(report, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "oracle_diff.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8",
    )
    with (out / "oracle_diff.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_DIFF_FIELDS)
        writer.writeheader()
        for row in report["differences"]:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, default=str)
                if key in {"expected", "actual", "source_reference", "v3_reference", "comparison_rule"}
                else value
                for key, value in row.items()
            })


def compare_files(expected: Path, actual: Path, out: Path, *,
                  case_ids: Iterable[str] | None = None) -> dict:
    """Write JSON and CSV evidence for a comparison of expected and actual JSONL."""
    report = {
        "schema_version": "reference-oracle-v1",
        "status": "NOT_RUN",
        "inputs": {
            "expected": {"path": str(expected), "sha256": None},
            "actual": {"path": str(actual), "sha256": None},
        },
        "cases": [],
        "differences": [],
        "counts": {},
        "verified_reference_count": 0,
        "verified_synthetic_count": 0,
    }
    try:
        if not actual.exists():
            report["reason"] = "actual results are absent"
        elif not expected.exists():
            report.update(status="BLOCKED", reason="frozen expected results are absent")
        else:
            _compare_inputs(report, expected, actual, case_ids)
    except (OSError, ValueError) as error:
        report.update(status="FAIL", reason=str(error))
        report["differences"].append(_difference("", "", _MISSING, _MISSING, None, str(error)))
    _write_report(report, out)
    return report


def _compare_inputs(report, expected, actual, case_ids):
    rows = {}
    for role, path in (("expected", expected), ("actual", actual)):
        data = path.read_bytes()
        report["inputs"][role]["sha256"] = hashlib.sha256(data).hexdigest()
        if role == "expected" and path.name == "golden_expected.jsonl":
            _verify_manifest(path, report["inputs"][role])
        try:
            rows[role] = _load_rows(data)
        except ValueError as error:
            raise ValueError(f"{role} results: {error}") from error

    all_cases = rows["expected"].keys() | rows["actual"].keys()
    selected = set(case_ids) if case_ids is not None else all_cases
    if case_ids is not None:
        if not selected or any(not _nonempty_text(case_id) for case_id in selected):
            raise ValueError("comparison scope requires nonempty case IDs")
        report["scope"] = {
            "case_ids": sorted(selected),
            "excluded_expected_count": len(rows["expected"].keys() - selected),
            "excluded_actual_count": len(rows["actual"].keys() - selected),
        }
    for case_id in sorted(selected):
        reference = rows["expected"].get(case_id)
        status, differences = _compare_one(case_id, reference, rows["actual"].get(case_id))
        report["cases"].append({
            "case_id": case_id,
            "status": status,
            "synthetic": (reference or {}).get("synthetic", False),
            "evidence_tier": (reference or {}).get("evidence_tier"),
            "expected_source": (reference or {}).get("expected_source"),
            "reference_sha256": (reference or {}).get("reference_sha256"),
            "difference_count": len(differences),
        })
        report["differences"].extend(differences)
    report["counts"] = dict(Counter(case["status"] for case in report["cases"]))
    for status in ("FAIL", "BLOCKED", "NOT_RUN", "PASS"):
        if report["counts"].get(status):
            report["status"] = status
            break
    if not report["cases"]:
        report["reason"] = "no cases to compare"
    for case in report["cases"]:
        if case["status"] == "PASS":
            count = "verified_synthetic_count" if case["synthetic"] else "verified_reference_count"
            report[count] += 1


def _verify_manifest(expected, receipt):
    manifest = expected.with_name("oracle_manifest.json")
    if not manifest.exists():
        return
    data = manifest.read_bytes()
    try:
        metadata = json.loads(data, object_pairs_hook=_object)
        entry = metadata["expected_file"]
        if entry["path"] != expected.name or entry["sha256"] != receipt["sha256"]:
            raise ValueError("oracle manifest does not match frozen expected bytes")
    except (KeyError, TypeError) as error:
        raise ValueError("oracle manifest is missing its expected_file fingerprint") from error
    receipt["manifest_path"] = str(manifest)
    receipt["manifest_sha256"] = hashlib.sha256(data).hexdigest()
