# Census Stage 3 results

The fixed corpus contains 93 Projects, 51 Census fields, 144 exact source SHA-256 objects and 148 eligible captures. The cutoff is 2026-11-28 in Asia/Taipei. The starting main was `2fc644dc3fadca64cf9cb75db921bd728ea4160a`. The immutable research candidate `47bcd58e568dc912f78c887ad83377adc891f81a472b3e71a266cf2e5abd60dd` runs on code `1c44cab28602a5bcf15745f11f293dfb2b3f3aee` and preserves the prior candidate `b0f1389f395c7eff4a239c0214c0bb518f2ba504f84c71f3be948404debec1da`.

| Fixed-source result | Prior | Current |
|---|---:|---:|
| Populated Census cells | 1,115 | 1,122 |
| Direct scientific cells | 16 | 16 |
| Governed derived scientific cells | 2 | 3 |
| Validated descriptive cells | 5 | 11 |
| Numeric candidates / validations / acceptances | 42 / 27 / 21 | 42 / 27 / 21 |
| Comparable Project×Criterion keys | R1 4 / S1 2 | R1 4 / S1 2 |
| Complete issuer R1+S1 cohort | 2 | 2 |
| Formal GIS points / formal forecast values | 0 / 0 | 0 / 0 |

Three additional typed assertions yielded two validated selections. Two DFS assertions in the 2026 Austrade original add six eligible display cells for Avonbank and Copi. The third assertion remains unselected. Wudinna contributes one Census-only reported MREO derived cell at 16.8 kt, with a source precision interval of 16.66925–16.93125 kt. All three are separate Project IDs. No new numeric material observation or comparable key was accepted. All four other MCDA panels remain insufficient. Matrices, weights, scores, ranking and sensitivity outputs match the archived study.

Every one of the 4,743 cells was compared. Seven state/value/origin changes were verified. 27 cells have new release-related lineage and preserve their earlier source and value. The other 4,709 cells match.

The fixed-backlog replay resolved one old reviewed overlap from 143 unbound dispositions and rediscovered six candidate rows in four of 19 structure jobs. 142 former dispositions and 15 structure jobs remain open. The two historical source editions found in the V2 read-only Git hold remain outside the fixed cohort. Two unavailable editions remain labelled as such. The old blind panels remain 2/10 and 2/31 with zero false positives. In five isolated new families the locator recorded 0 TP, 0 FP, 5 FN and 3 TN. With supplied row locators field typing recorded 8 TP, 1 FP, 6 FN and 20 TN. End-to-end field typing recorded 0 TP, 0 FP, 14 FN and 20 TN. Fixed-case gains did not generalize to the isolated sample. Formal admission precision and recall are unmeasured. OCR was not run.

The separately sealed seven-response geography expansion examined 91 native rows and reviewed 11 point proposals. Eight lack sufficient evidence and three were rejected for identity or role. There are zero authorized sites and zero I1–I4 additions. The three original engineering scenarios were independently replayed in their existing shadow roles. The fixed-source comparisons exclude the expanded manifest.

## Cost and dependency measurements

Each matched row uses three alternated cold Python builds on the same inputs. The Stage 3 profile expands the scientific workload on the same 144 sources. The local OS page cache was uncontrolled. The geometry detector's separate matched scope decodes 15 PDFs / 1,292 pages on each code revision. Its median wall was 5.516 → 5.467 seconds, with peak RSS 48,608 → 48,200 KiB. Expanded discovery used 34 PDF opens, 2,698 decoded pages and 1,406 searched pages at 18.348 seconds median. The matcher AST Max/P90 moved from 87/16 to 44/14 for its module. Runtime pipeline statistics follow.

| Scope | Wall median s | CPU median s | Peak RSS median KiB | Reached modules | Static / runtime edges | AST max / P90 | Static / runtime cycle groups | Duplicate AST groups | Cold → resource cache PDF calls / pages | SQL statements |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| Stage 2 fixed profile | 6.111 | 6.089 | 96,380 | 34 | 139 / 100 | 61 / 20 | 1 / 1 | 1 | 12 / 645 → 4 / 421 | 4,053 |
| New code and the same fixed profile | 4.585 | 4.642 | 96,012 | 35 | 147 / 103 | 61 / 20 | 1 / 1 | 1 | 12 / 645 → 4 / 421 | 4,054 |
| New code and the Stage 3 evidence profile | 7.365 | 7.428 | 96,464 | 35 | 147 / 111 | 61 / 20 | 1 / 1 | 1 | 15 / 912 → 7 / 679 | 5,145 |

The static and runtime cycle groups retain Store's governed reread of authoritative observations during save and use. Removing this path would weaken evidence checks. The MREO validator directly reuses the native unit evidence and has no new dependence on the MCDA policy. The same-profile timings support no speedup claim. Matched module checks use three samples and local workload varied during measurement. Stage 2 PR #34 used 96 successful runner-seconds. Units A–E used 522 successful runner-seconds across five PRs. Jobs and elapsed runner time for each head are recorded below. Monetary billing is unmeasured.

## Remaining scientific bottlenecks

Native headers for multilevel tonnage and non-resource tables are the main fixed-source extraction bottleneck. The existing error assigns tonnes to a reported Million Tonnes token when manually supplied a locator. Its row was absent from end-to-end retrieval and no resulting candidate entered formal selection. An independent family check is required to establish general recall improvement. Wolverine's Dy and Y precision intervals still disagree and no complete oxide composition was established. An official GIS representative point still needs matching role, location precision and date evidence. The existing engineering forecasts retain their distinct materials, design assumptions and periods.

## Verification

S3-0–S3-5 and F0–F4 pass with their recorded scope. S3-1 validates deterministic dispositions and independently measured families. The isolated-family recall gain hypothesis failed. F5 remains blocked and historical O2 remains failed. 340 exact-environment tests passed in 65.929s, with zero skips. Two latest cold runs matched all eight scientific artifacts, with clean SQLite integrity and foreign keys. The separate local copy read back through the same filesystem. Chromium verified the source link, three formula dialogs, 11 typed attribute dialogs, preserved historical values, language switch, version switch, corruption rejection and candidate-only behavior. The underlying site and ranking logic stay byte-stable.

| PR | Reviewed head | Main merge | Successful runner seconds | Successful jobs |
|---|---|---|---:|---|
| [#35](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/35) | `0bbc05b6dd74` | `7f7721d325d5` | 95 | [Static and unit](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46118881214/job/140898737124) · [Exact resource integration](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46118881214/job/140898737314) |
| [#36](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/36) | `ed21bc1d9ca8` | `785aafd1c468` | 99 | [Static and unit](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119413496/job/140900381307) · [Exact resource integration](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119413496/job/140900381144) |
| [#37](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/37) | `623a4ce2cc02` | `fa935dbca1c4` | 84 | [Static and unit](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119608107/job/140900984508) · [verify](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119608082/job/140900984246) · [Exact resource integration](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119608107/job/140900984263) |
| [#38](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/38) | `b0a02326e8fe` | `0b93d64b6514` | 134 | [Static and unit](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119851143/job/140901749736) · [verify](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119851098/job/140901749633) · [Browser interaction and release integrity](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119851076/job/140901749327) · [Exact resource integration](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46119851143/job/140901749520) |
| [#39](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/39) | `d23f61ed52ab` | `1c44cab28602` | 110 | [Static and unit](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46120035903/job/140902315462) · [Exact resource integration](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46120035903/job/140902315763) |

[Machine verification receipt](census_stage3_verification.json) · [File lifecycle](census_stage3_file_lifecycle.csv) · [Evidence gaps](census_gap_inventory.md) · [Native table recovery](native_resource_table_recovery.md) · [Attributes](census_attribute_breadth.md) · [MREO formula](census_reported_mreo.md) · [Separate government and forecast evidence](government_point_expansion_and_forecast_context.md)

## Reproduce the fixed-source candidate

Use the code commit, profile, input manifest and binding SHA in the verification receipt. Serve the preview locally at `/?preview=1`. The bundle has both immutable versions and a preview pointer. The default view requires a formal pointer and stays unavailable for this candidate.

```bash
export PYTHONPATH=src
python -m au_ree_v3 research replay --offline \
  --release-id "$RELEASE" --release-root "$EMPTY_INDEX" --store "$CAS" --out "$REPLAY"
python tools/verify_census_stage3_release.py \
  --release "$ARCHIVE/releases/$RELEASE.json" --build "$BUILD" --baseline "$STAGE2_BUILD" \
  --cas "$CAS" --replica-cas "$LOCAL_COPY" --replays "$COLD_A" "$COLD_B" --out "$AUDIT"
node web/tests/browser.mjs "$WEB_DATA" --stage3
```

Set `AU_REE_STAGE3_CAS` to the local source CAS to exercise the issuer PDF link against SHA-verified original bytes in Chromium. The browser route serves stored bytes and checks the real navigation target. The candidate site requires no live issuer response to replay. The visible derived dialog links to page 3. The reviewed dry-basis page 12 and companion image at page 15 are retained in the archival derivation proof.

The two copies used for the final cold replay have separate files and share the same filesystem. This validates restoration and integrity within the available environment. Independent failure-domain replication and formal publication are F5 work.
