"""Pin unchanged descriptive bindings to a verified superset of source captures."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from au_ree_v3.contracts import canonical_json
from au_ree_v3.registry import load_profile
from au_ree_v3.sources import SCHEMA


ATTRIBUTE_INPUTS = frozenset({"attribute_bindings", "attribute_milestones", "attribute_batches"})


def _encoded(document):
    return (canonical_json(document) + "\n").encode()


def _capture_map(manifest):
    rows = manifest["editions"]
    captures = {row["receipt_sha256"]: row for row in rows}
    if len(captures) != len(rows):
        raise ValueError("Duplicate capture in source manifest")
    return captures


def _projects(manifest):
    return {row["project_id"] for row in manifest["projects"]}


def require_expansion(fixed, expanded, fixed_sha):
    """Check the original scientific/source scope before changing any input pin."""
    identity = ("schema", "profile_id", "cutoff", "timezone")
    if fixed["schema"] != SCHEMA or any(fixed[key] != expanded[key] for key in identity):
        raise ValueError("Source manifests have different research identities")
    if _projects(fixed) != _projects(expanded):
        raise ValueError("Expanded manifest changes the project cohort")
    if fixed_sha not in {row["sha256"] for row in expanded.get("input_manifests", ())}:
        raise ValueError("Expanded manifest does not identify the fixed input")
    original, combined = _capture_map(fixed), _capture_map(expanded)
    if any(combined.get(capture) != row for capture, row in original.items()):
        raise ValueError("Expanded manifest changes a fixed source edition")
    if combined.keys() == original.keys():
        raise ValueError("No additional source captures in the expanded manifest")
    return len(original), len(combined)


def prepare_profile(profile_path, fixed_path, expanded_path, output):
    settings = load_profile(profile_path)
    fixed, expanded = fixed_path.read_bytes(), expanded_path.read_bytes()
    fixed_sha, expanded_sha = (sha256(data).hexdigest() for data in (fixed, expanded))
    before, after = require_expansion(json.loads(fixed), json.loads(expanded), fixed_sha)
    profile = json.loads(profile_path.read_bytes())
    if not ATTRIBUTE_INPUTS.intersection(profile["inputs"]):
        raise ValueError("Profile has no descriptive source pins to update")
    pinned, artifacts = {}, {}
    for name, record in profile["inputs"].items():
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe profile path for {name}")
        original = (profile_path.parent / relative).read_bytes()
        if sha256(original).hexdigest() != record["sha256"]:
            raise ValueError(f"Invalid fingerprint for {name}")
        if name in ATTRIBUTE_INPUTS:
            data = json.loads(original)
            if data["source_manifest_sha256"] != fixed_sha:
                raise ValueError(f"Original input {name} is not pinned to the fixed manifest")
            data["source_manifest_sha256"] = expanded_sha
            result = _encoded(data)
            record["sha256"] = sha256(result).hexdigest()
            pinned[name] = record["sha256"]
        else:
            result = original
        if relative in {Path("profile.json"), Path("expanded_source_profile_receipt.json")}:
            raise ValueError("Profile artifact overlaps a generated file")
        if artifacts.setdefault(relative, result) != result:
            raise ValueError("Incompatible profile artifacts target the same path")
    output.mkdir(parents=True, exist_ok=False)
    for relative, result in artifacts.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result)
    destination = output / "profile.json"
    destination.write_bytes(_encoded(profile))
    load_profile(destination)
    receipt = {"status": "PASS", "profile_id": settings.profile_id, "profile_sha256": sha256(destination.read_bytes()).hexdigest(),
               "fixed_manifest_sha256": fixed_sha, "expanded_manifest_sha256": expanded_sha,
               "fixed_capture_count": before, "expanded_capture_count": after, "repinned_artifacts": pinned}
    (output / "expanded_source_profile_receipt.json").write_bytes(_encoded(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "fixed", "expanded", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(canonical_json(prepare_profile(args.profile, args.fixed, args.expanded, args.out)))


if __name__ == "__main__":
    main()
