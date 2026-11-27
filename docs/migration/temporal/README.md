# Temporal evidence governance

The policy identifier is `temporal-evidence-v1`. `contracts.temporal.evaluate_temporal` binds an exact source SHA-256, immutable evidence references, date roles and a daily research cutoff into a reproducible decision ID. Source bytes, timing and scientific comparability have separate outcomes. A later download may preserve eligibility when a reviewed public capture or publisher edition record proves the same SHA was available by the cutoff. An earlier publication date without such evidence leaves a candidate. A known later first publication is ineligible.

| Input | Treatment |
| --- | --- |
| Declared original publication | Retained as declared. An incompatible exact-edition witness produces a conflict. |
| Earliest proven public access for this edition | Earliest conservative date supported by exact-SHA evidence. A coarse interval qualifies once its latest possible date is within the cutoff. |
| Original acquisition and session reacquisition | Operational times, compared in UTC. A new validation retains its own reacquisition event without replacing the immutable Source. |
| Legacy `available_date` | Retained. Proven V2 registry-selection dates are classified as operational. Unclassified dates later than the recorded acquisition are flagged. |
| Historical observation effective or event date | Required for historical measurements or events. A date fully after the cutoff is ineligible, and a period that spans it stays a candidate. |
| Forecast and engineering-plan dates | Retained as future scenario dates. Actual Census, formal MCDA input and publication guards disallow using these roles as actual observations. |

A typed proof is an assertion made by reviewed ingress. Experimental local bindings record the asserted reference and verify the PDF bytes without fetching the cited proof. A formal Store additionally requires an operator-approved set of exact evidence IDs, pinned by the release, and checks it again on consumption. Operators must independently verify the evidence artifact and same-edition witness before allowing its ID. The current native-PDF CLI remains experimental. Its selected values have temporal decision lineage, but it does not seal or publish a release. Existing SQLite v1 rows retain their original payloads through schema v2 migration and remain archive-only until replayed under the new gate.

## Frozen Golden audit

The audit joins fixed V2 observation, source-selection and bundle acquisition receipts, the Census release and the verified Phase 22 lock. It independently checks the expected JSONL fingerprint against the Oracle manifest. In pinned V2 acquisition code, static PDF `available_date` denotes a registry/acquisition day. The later selected-source ledger and separately retained release-source timestamp represent different acquisition episodes. Seventeen contradictions were resolved at the level of provenance and date role. Both dates remain verbatim in the historical reference. The earlier capture independently proves the same edition could be accessed before the cutoff for those 17 rows.

| Count | Outcome |
| --- | ---: |
| Frozen observations audited | 93 |
| Legacy availability after cutoff | 83 |
| Historically selected among those 83 | 69 |
| Mixed acquisition episodes with an exact earlier witness | 17 |
| Temporal eligibility supported across the 93 | 27 |
| Historically selected among those 27 | 6 |
| Candidates lacking earlier exact-edition evidence | 66 |
| New formal promotions | 0 |

The 66 candidates were all selected in the frozen V2 release. They comprise 18 January Wolverine, 17 North Stanmore, 16 Wimmera and 15 Splinter observations. They have recorded post-cutoff acquisitions and no earlier exact-edition capture in the reviewed evidence. This is an evidence gap, and the audit preserves the historical selection. Three of the 69 late-availability selections are Yin and have an earlier exact witness. The 14 other mixed-episode rows are unselected Wolverine alternatives.

The broader 2026-11-13 recovery inventory evaluated 765 locked source references. Its PDF-only snapshot proof adapter found 93 eligible source references, 665 candidates and [7 unresolved chronology conflicts](source_conflicts.json). These are source-reference counts and cover a wider scope than the Golden observation audit. Source eligibility and current asset integrity are independent. Non-PDF capture attestations and alternative historical publication records have no approved resolver in this work.

```sh
au-ree-v3 migration audit-temporal --v2-root "$V2_ROOT" \
  --expected tests/golden/golden_expected.jsonl \
  --oracle-manifest tests/golden/oracle_manifest.json --out work/migration/temporal
au-ree-v3 migration build-golden --v2-root "$V2_ROOT" --assets "$ASSET_LIBRARY" \
  --out work/migration/golden-review
```

The [audit receipt](temporal_audit.json) and [93 case dispositions](temporal_dispositions.jsonl) are fixed comparison artifacts. Regenerating Golden produces the original expected bytes and a separate time audit. Archived V2 output and source references are read-only.

## Science and engineering checks

The SHA-locked Mt Weld document remains eligible. Its same-edition public capture is dated 2026-11-02 and the declared publication is 2024-08-05. Six observation/Census comparison records had identical scientific values before and after governance. Late reacquisition of the same bytes in the same SQLite database creates a new temporal decision and retains the original. Source cases with exact bytes but inadequate or late public proof produced no accepted cells.

The resource path formerly used two cutoff calculations, one in application binding and one in SQLite validation. Both now call the single gate, also used by frozen recovery and Golden auditing. The matched-output profile observed 7→9 internal modules, 9→14 dynamic cross-module edges, AST branch score p90 16→20 and no cross-module duplicate-body groups in either run. The increase reflects evidence and release-lineage checks. On five fresh processes per revision, preliminary median full-replay time was 149 ms before and 126 ms after. Python peak RSS was about 18 MiB in both cases. This is a small offline fixture with uncontrolled OS page cache; it provides no throughput claim for national replay.

```sh
AU_REE_REPLAY_ASSETS="$ASSET_LIBRARY" python -m unittest discover -s tests -v
python tools/benchmark_temporal_governance.py --base d35a55916754fd1adb02d564d6fcb143a0b1b692 \
  --assets "$ASSET_LIBRARY" --out work/migration/temporal/benchmark.json
```

## PR #4 review

PR #4 head `b5015579933f73c2990718b1b9b2f47d9a04f2af` was re-tested from a separate, read-only checkout. All 69 tests passed with the exact PDF. Its frozen-matrix point and leave-out checks matched 553 and 2,457 rows respectively. Those entry points consume archival derivative matrices and weights, which stay in the historical comparison scope. They supply no formal V3 MCDA inputs. This governance PR targets latest `main` independently and preserves PR #4. A three-way preflight identified CLI dispatch and workflow-step overlaps to reconcile if both changes are later integrated.
