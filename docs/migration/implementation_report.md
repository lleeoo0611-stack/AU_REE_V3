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
| G1 | PASS | Real Mt Weld imports and synthetic contract tests passed. See B below. |
| G2 | PASS | `VERTICAL_SLICE_VERIFIED` for the declared Mt Weld 2024 source and three resource fields. |
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

## B · Evidence contracts and transactional storage

[A PR #1](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/1) tested commit `8ee8ea6f383ca7f2938171cdbbb8ef6554d39edf` and merged as `5ee98cc24c7a716bb1d150933bfaaeb3e8a9b1b9`. [Actions run 46064858937](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46064858937) succeeded for static/unit verification in 7 seconds of job elapsed time. [Manual Actions run 46065054821](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46065054821) completed frozen reference integrity in 31 seconds. Runner billing and equivalent V2 CI cost are unmeasured.

The importer verified the Mt Weld original `fe26ac29c4773a5af95e909a1cd95ca1e9ad37c34560dfa72c1e9674e4c8a503` before importing three V2 observations into the shadow layer. The typed interchange SHA-256 was `11e6141503e0ab23df0384c8c6d5840579e1b824e90dd1413e180ac6432eb2be`. Repeating the import inserted zero rows. The V2 input database kept its original SHA-256. V3 SQLite integrity was `ok` and the foreign-key check returned zero rows. The frozen V2 raw token and unit were null for this fixture and remained null in the import. The contract stores source-document tokens independently when available.

```sh
python -m unittest discover -s tests -p 'test_store.py' -v
python -m unittest discover -s tests -p 'test_v2_import.py' -v
python tools/verify_v2_import.py --v2-root "$V2_ROOT" --v2-python "$V2_PYTHON" \
  --asset "$MT_WELD_PDF" --binding config/migration/mt_weld_resource_binding_v1.json \
  --out work/migration/frozen-v2-import
```

The 16 B unit tests cover source editions, missing evidence, distinct scopes and stable IDs, exact precision and dates, idempotence and rollback. The importer reports independent failures and leaves scientific acceptance to the evidence rules. This unit contributes zero formal observations, comparable criteria or rankings. The same-capability engineering comparison is deferred to the real source path in C.

Added `src/au_ree_v3/contracts/__init__.py`, `store.py`, and `migration/v2_import.py` for versioned records, SQLite migration and the read-only V2 adapter. Added `tests/test_store.py` and `tests/test_v2_import.py` and the reproducible real-fixture tools and binding. Updated the capability matrix and its generator. No files were consolidated, archived or removed in V3 in this work unit.

## C · Verified Mt Weld resource slice

[B PR #2](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/2) tested commit `6cbe4dbfa44d4e9ba2209d6a12dbfae87b9328cd` and merged as `9a9e0fbf6018f345e8323f39e75005385f174825`. [Actions run 46065238626](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46065238626) passed the static/unit job in 8 seconds elapsed.

The slice ran from the same SHA-256 Mt Weld PDF using V3 commit `447862eeac4f5a8c9097a3cbbce4aeaba5951812`. It extracted 106.6 Mt, 4.12% TREO and the native table token `4,389` kt TREO. It preserved all three frozen Observation IDs and source-page lineage. The independent Oracle matched three selected observations and three present Census cells, with zero differences. Three independently projected frozen unselected Table 1 values also matched, including its rounded 4.1% TREO. That comparison covered page, scope, source date and digest, raw tokens, unit, value and selection state. The historical recognizer IDs were outside the unselected comparison; V2 records the selection state without a narrower rejection reason.

Two clean runs produced byte-identical evidence, Census, per-case and replay artifacts. The input SHA-256 was `029abf67f8fc70e4b70989c217c335ff8d9c36f7366a74c2c3429122b645551c`, rules SHA-256 `664eb9341a3a099a7961d4e93f0d17efd594adc585ae6366f238ac8cf8e131ae` and science output SHA-256 `c6d2650cd2ac1b02c5d1ceb75052f7e970c9eb09e60c370fdb0fcc38f1730254`. V3 also matched 11 independently expected synthetic rejection outcomes/reasons and 33 corresponding UNKNOWN/null cells while retaining an independent valid source. The 11-test integration suite, including actual PDF replay, passed. V3 schema version is 1. The release remains experimental. Formal observation gain is 0. New comparable Project–criterion counts, research population and ranks for this slice are unmeasured.

### Matched-output engineering measurement

The benchmark fixture is the same source digest, statement, cutoff, project, three selected values and Observation IDs. V2 entry points are `store.connect`, `evidence.ingest_claims` and `evidence.resource_values` at the science ref. V3 uses `application.replay` at the C code commit. Both selected outputs matched. V2 validates the frozen claim payload and V3 also re-extracts native tokens, checks same-page context and writes Census and evidence. The measurements characterize these implemented paths on this fixture.

| Metric | V2 science path | V3 resource path |
| --- | ---: | ---: |
| Profiled internal modules | 15 | 7 |
| Observed cross-module call edges | 4 | 9 |
| Called-function AST branch score, p90 / max | 65 / 120 | 16 / 29 |
| Cross-module duplicate-body groups | 0 | 0 |
| Runtime in three fresh processes, seconds | 0.929 / 1.049 / 0.744 | 0.127 / 0.117 / 0.115 |
| Maximum peak Python RSS, KiB | 103,032 | 32,452 |
| Maximum separate parser-process RSS, KiB | — | 19,152 |

Runs used Debian 13 on an Intel Xeon Platinum 8573C, Python 3.12.14, SQLite 3.53.1, Git 2.56.1, and Poppler 25.03.0. V2 dependencies were installed at the reference's pinned versions. Each process started with a fresh SQLite database. OS page cache state was uncontrolled, and no network wait was timed. R and OCR were outside this workload. Warm application-cache and comparable CI costs remain `UNMEASURED`. The dynamic counts are not repository-wide import DAG counts.

```sh
AU_REE_REPLAY_ASSETS="$ASSET_LIBRARY" python -m unittest discover -s tests -p 'test_pipeline.py' -v
python tools/benchmark_resource_replay.py --v2-root "$V2_ROOT" --v2-python "$V2_PYTHON" \
  --assets "$ASSET_LIBRARY" --out work/migration/comparable_engineering_metrics.json
```

C added `src/au_ree_v3/{application,census,documents,observations}.py`, `tests/test_pipeline.py`, `tests/golden/synthetic_pipeline_expected.jsonl` and `tools/benchmark_resource_replay.py`. It modified the CLI, README, integration workflow, capability generator and matrix, this report, and the synthetic rejection catalog. No V3 files were merged, archived or removed. Pending capabilities include wider PDF/OCR and retrieval, entity resolution, GIS, ontology and ML, full Census and source-to-panel replay, and publication rollback.

## D · Frozen decision components and archive recovery

[C PR #3](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/3) tested head `b5e859538b093092694399db69f56d360d37a61c` and merged as `d35a55916754fd1adb02d564d6fcb143a0b1b692`. [Actions run 46065612817](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46065612817) passed static/unit in 14 seconds and exact PDF integration in 39 seconds of job elapsed time.

### 2026-11-05 decision component

V3 recomputed PROMETHEE II, SAW and fixed-anchor TOPSIS directly from 16 frozen historical matrices, criteria and scenario weights at `dcda87e81e1250d443f73c1984faaa7beaa19930`. Across 354 matrix cells and 553 scenario/project rows, all 1,659 score-and-rank pairs and 51 tie groups matched. Maximum absolute score error was `5.56e-16` against the declared `1e-8` tolerances. Deterministic criterion/domain removal matched all 2,457 ranks and rank changes, including 1,345 changes relative to the scenario baseline. Of 388 removal variants, 28 had no positive weight remaining and were omitted under the frozen rule.

Point and removal computations took 0.013 s and 0.042 s of pure Python time. Including local Git reads and comparison they took 0.390 s and 0.508 s. Observed peak process RSS from comparable local re-runs was about 23,172 KiB and 29,900 KiB respectively. The R runtime, equivalent two-version performance, warm application cache and CI cost comparison for these components are unmeasured. These results reuse existing benefit scores. Added comparable Project–criterion values, new cohorts and new formal observations are zero in this component.

All 538 required original fingerprints for the subset matched. G3 remains `NOT_RUN` for V3-generated cohorts, matrices, weights and keyed draws from those originals. The component uses the historical fixed-commit matrices, whose bytes differ in some cases from the later V2 fresh replay receipt. The later receipt keeps its own evidence scope.

### 2026-11-13 archive

The archived Census rehash remains `ARCHIVED_OUTPUT_VERIFIED`. The local source inventory reached 673/715 exact, 31 missing and 11 wrong editions. Three PDFs among the historical 19 missing assets were recovered by exact digest. The 42 currently unresolved assets directly link to 32 projects, 183 Census cells, 36 resource observations, 18 eligibility-gate rows and three shadow candidates. No direct MCDA criterion link was found. Indirect selection and panel impact remain unmeasured.

All 13 release-listed configuration files were recovered against the independent archival digests. The fixed science commit contained the exact `research.json` bytes. The sealed `summary_rules.json` digest matched the same fixed Git blob with CRLF line endings. The receipt records both hashes and the byte-preserving JSON interpretation. The composite runtime configuration verification and source replay remain unexecuted. G4 is `BLOCKED` by original-source availability. G5 and the publication CI tier remain `NOT_RUN`.

D adds `src/au_ree_v3/decision.py`, `src/au_ree_v3/migration/verify_decision.py`, `tests/test_decision.py`, `tools/recover_archive_configuration.py`, `tests/test_archive_configuration_recovery.py` and `docs/migration/archive_configuration_recovery.json`. It updates frozen verification, CLI, the capability matrix and its source, the workflow, README, the migration baseline test and this report. There are no V3 file consolidations, archives or removals in D. Run the decision commands in the [README](../../README.md). The local full suite with the frozen Mt Weld PDF passed 69 tests without skips.

The next source-to-panel unit is to reproduce the subset's criterion resolution, weight inputs and panel membership from the verified 538 assets, then run the newly verified ranking boundary against those outputs. GIS, technical disclosure, ontology and ML remain listed in the capability matrix.
