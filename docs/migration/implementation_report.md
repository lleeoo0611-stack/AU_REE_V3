# Migration implementation report

## A · Frozen migration baseline

V3 started from `da2957f37a98093333de71f27fd815aa62918c38`. The reference repository was fetched by full SHA and used read-only.

| Reference | Commit | Purpose |
| --- | --- | --- |
| V2 engineering | `2af6f611e51eb4ac9dda73419d1afeba70d1307e` | Inventory and frozen archive artifacts |
| V2 science | `0dff6beb5aca52e565c37289e3ab295737e76942` | 2026-11-13 calculation and contracts |
| V2 subset | `dcda87e81e1250d443f73c1984faaa7beaa19930` | 2026-11-05 snapshot |

| Gate | Status | Evidence |
| --- | --- | --- |
| G0 | PASS | Both frozen profiles, inventory, crosswalk and Golden reference extraction passed locally. |
| G1 | NOT_RUN | This change establishes the reference and mapping. |
| G2 | NOT_RUN | The real-source end-to-end comparison belongs to the verified evidence pipeline. |
| G3 | NOT_RUN | All 538 required source fingerprints verified locally. V3 source-to-panel replay remains unexecuted. |
| G4 | BLOCKED | Archived Census verified. Local original availability and historical configuration mismatches block source replay. |
| G5 | NOT_RUN | No cutover or publication requested through this change. |

The archive check produced 93 projects × 51 fields, with 2,500 present, 1,715 inapplicable and 528 applicable but missing. Coverage was 82.40% against the historical denominator of 3,034 and 82.56% against the reclassified denominator of 3,028. The machine-readable receipt records the distinct rules and source fingerprints, per-asset availability and configuration mismatches. The 2026-11-13 archive check rehashed all 16 rankings against archival digests. The 2026-11-05 fresh subset publication has separate receipt fingerprints and a different artifact scope.

The [Golden manifest](../../tests/golden/golden_manifest.json) contains 130 cases across frozen resource observations, Census cells and source-recovery negatives. The [Oracle manifest](../../tests/golden/oracle_manifest.json) pins the expected JSONL to its V2 provenance and SHA-256. Synthetic Oracle faults and rejection cases use a distinct corpus. Research accuracy, recall, additional observations, comparable criterion values and ranking changes were unmeasured in A.

The audit covers 6,798 paths. It proposes retaining 1,234 files and refactoring 134, with 5,430 paths retained in frozen V2 Git for historical reading. These are dispositions, and this migration made zero file changes to V2. The [capability matrix](capability_matrix.csv) covers 29 capabilities and all 20 CLI commands, 49 tools, two R entry points and two workflows observed by the scan. The [crosswalk](schema_crosswalk.csv) verifies 362 fields, preserving source identifiers, exact lexical precision where available and unresolved legacy REAL semantics. Fourteen frozen input blobs, 4,743 provenance rows and 519 distinct historical Observation IDs were checked.

The fixed V2 engineering tree measured 78 Python modules, 343 internal edges and no cycles. Two blob fingerprints differed against the archived worktree metrics receipt. V3 relative engineering improvements, same-workload runtime and memory, and CI cost comparisons are unmeasured in A. Locally the inventory ran in 8.32 s with peak Python RSS of 243,648 KiB. The crosswalk ran in 0.36 s with peak Python RSS of 47,584 KiB. These are local offline tool measurements.

## A file dispositions

- Modified `README.md` to document executable commands and present scope.
- Added `.gitignore`, `pyproject.toml`, and `.github/workflows/migration.yml` for package installation, generated-output isolation and verification jobs.
- Added `docs/architecture.md` and `docs/migration/{acceptance,implementation_report,migration_specification_v1_1,scientific_contract}.md` for boundaries, gates, the pinned specification and reported results.
- Added `docs/migration/{file_disposition,capability_matrix,schema_crosswalk}.csv` and `config/migration/schema_crosswalk_v1.json` for inventory and mappings.
- Added `src/au_ree_v3/{__init__,__main__,cli}.py` and `src/au_ree_v3/migration/{__init__,audit,audit_capabilities,baseline,crosswalk,golden,reference_oracle}.py` plus `baselines_v1.json` for the executable checks.
- Added `tests/golden/{golden_cases.jsonl,golden_expected.jsonl,golden_manifest.json,oracle_manifest.json,synthetic_oracle_expected.jsonl,synthetic_rejections.jsonl}` and migration unit suites for frozen inputs and fault injection.

Follow the [README commands](../../README.md) to reproduce the receipts. Per-check outputs live under `work/migration/`. CI status and the exact tested commit are available on the GitHub PR.
