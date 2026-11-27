#!/usr/bin/env python3
"""Recover the sealed Census configuration files by their recorded byte digests."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from au_ree_v3.migration.baseline import ARCHIVED_CENSUS, BASELINES, PinnedRepository


def recover_bytes(name: str, raw: bytes, expected: str) -> tuple[bytes, str]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"Invalid archived digest for {name}")
    if sha256(raw).hexdigest() == expected:
        return raw, "EXACT_FIXED_SCIENCE_GIT_BLOB"
    if name == "summary_rules.json" and b"\r" not in raw:
        crlf = raw.replace(b"\n", b"\r\n")
        if sha256(crlf).hexdigest() == expected and json.loads(crlf) == json.loads(raw):
            return crlf, "FIXED_SCIENCE_GIT_BLOB_LF_TO_CRLF"
    raise ValueError(f"Archived configuration SHA unavailable for {name}")


def _store_exact(path: Path, data: bytes) -> None:
    """Publish an immutable content-addressed file without overwriting a collision."""
    with NamedTemporaryFile(dir=path.parent) as staged:
        staged.write(data)
        staged.flush()
        try:
            os.link(staged.name, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise ValueError(f"Conflicting bytes at content-addressed path {path.name}") from None


def recover_configuration(v2_root: Path, out: Path) -> dict:
    if out.resolve().is_relative_to(v2_root.resolve()):
        raise ValueError("Write configuration recovery outside the V2 reference checkout")
    repo = PinnedRepository(v2_root)
    archive = f"{BASELINES['archive_path']}/census_release.json"
    archive_pin = next(pin for pin in BASELINES["pins"][ARCHIVED_CENSUS]
                       if pin["path"] == archive and pin["ref"] == "engineering")
    content = repo.read("engineering", archive)
    if content is None or sha256(content).hexdigest() != archive_pin["sha256"]:
        raise ValueError("Pinned Census release receipt is unavailable or has the wrong digest")
    release = json.loads(content)
    science = BASELINES["refs"]["science"]
    cutoff = BASELINES["profiles"][ARCHIVED_CENSUS]["cutoff"]
    if release["code"]["commit"] != science or release["cutoff"] != cutoff:
        raise ValueError("Census release has a different code commit or cutoff")
    if not release["configuration_files"]:
        raise ValueError("Census release is missing configuration fingerprints")

    evidence = {}
    recovered = {}
    for name, expected in sorted(release["configuration_files"].items()):
        if Path(name).name != name:
            raise ValueError(f"Invalid configuration filename {name}")
        source_path = f"config/{name}"
        raw = repo.read("science", source_path)
        if raw is None:
            raise ValueError(f"Pinned V2 configuration blob is unavailable for {name}")
        edition, method = recover_bytes(name, raw, expected)
        recovered[expected] = edition
        evidence[name] = {
            "source_commit": science,
            "source_path": source_path,
            "source_sha256": sha256(raw).hexdigest(),
            "expected_sha256": expected,
            "actual_sha256": sha256(edition).hexdigest(),
            "asset": f"configuration/{expected}",
            "bytes": len(edition),
            "method": method,
            "status": "EXACT_SHA",
        }

    asset_dir = out / "configuration"
    if asset_dir.resolve().is_relative_to(v2_root.resolve()) or asset_dir.is_symlink():
        raise ValueError("Configuration asset directory must be outside V2 and cannot be a symlink")
    asset_dir.mkdir(parents=True, exist_ok=True)
    for digest, data in recovered.items():
        target = asset_dir / digest
        if target.is_symlink() or (target.exists() and target.read_bytes() != data):
            raise ValueError(f"Conflicting bytes at content-addressed path {digest}")
    for digest, data in recovered.items():
        _store_exact(asset_dir / digest, data)

    return {
        "schema": "archive-configuration-byte-recovery-v1",
        "status": "PASS",
        "cutoff": cutoff,
        "archive_commit": BASELINES["refs"]["engineering"],
        "release_path": archive,
        "release_sha256": archive_pin["sha256"],
        "files": evidence,
        "composite_configuration_digest_status": "NOT_RUN",
        "source_to_output_replay_status": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="Work directory or an external asset library")
    args = parser.parse_args()
    try:
        receipt = recover_configuration(args.v2_root, args.out)
    except (KeyError, OSError, ValueError) as error:
        parser.exit(2, f"Configuration recovery blocked: {error}\n")
    print(json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
