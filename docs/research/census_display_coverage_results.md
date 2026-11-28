# Census display coverage research delivery

Cutoff 2026-11-28 in Asia/Taipei. Fixed inventory 144 originals and 148 eligible captures. Starting main was `293cdfedea31874e0d757a7513695aaa77da30f0`. Candidate `a1805f68b22d73ab070be50f6d2a110edfcb30230e21778b6b62513ccbcc96ee` is tied to verified code `69a111dd56420d364c1ddf2547fe81888b4e51ed` and preserves the historical candidate `47bcd58e568dc912f78c887ad83377adc891f81a472b3e71a266cf2e5abd60dd`.

| Measure | Stage 3 | Current |
|---|---:|---:|
| Core populated cells | 1,122/4,743 (23.66%) | 1,138/4,743 (23.99%) |
| Source-verified core display cells | 30/4,743 | 46/4,743 |
| Meaningful Project reach | 9/93 | 12/93 |
| Direct science / approved derivations / descriptive cells | 16 / 3 / 11 | 16 / 3 / 27 |
| Numeric candidates / validated / selected | 42 / 27 / 21 | 42 / 27 / 21 |
| R1 / S1 keys and complete R1+S1 cohort | 4 / 2 / 2 | 4 / 2 / 2 |

The fixed originals support seven additional typed assertions in seven Projects across 7 new-to-field source editions. Five Project deposit-type assertions add ten cells. Two source-confirmed historical DFS completion assertions add six cells. Three Projects have their first meaningful display coverage. All prior numeric and descriptive values match, as do MCDA weights, matrices, ranks and sensitivity. The comparison is over the exact Project and field order and all 4,743 keys.

## Independent extension

The extension has 39/837 known cells (4.66%), 42 fact records tied to 14 scoped original subjects in 11 verified original editions and 10 Projects. Statement multiplicity stays linked to individual evidence rather than being aggregated. Five completion dates add sorting precision for existing or newly governed assertions. They introduce no additional independent assertions. Stage-specific figures preserve each resource statement's category, native cutoff, date and material basis, and each engineering statement's separate scenario and rate.

| Display field | Group | Populated Projects |
|---|---|---:|
| `resource_jorc_class_as_reported` | resource | 5/93 |
| `resource_estimate_effective_date` | resource | 5/93 |
| `resource_cutoff_as_reported` | resource | 6/93 |
| `resource_scope_or_deposit_name` | resource | 6/93 |
| `ree_grade_or_chemistry_basis` | resource | 6/93 |
| `study_effective_or_completion_date` | study | 5/93 |
| `forecast_product_and_basis` | forecast | 2/93 |
| `forecast_scenario_or_period` | forecast | 2/93 |
| `forecast_annual_rate_as_reported` | forecast | 2/93 |

Eight official geography records and one conflicting Goschen category are review-only. The fixed field grid gains zero coordinates, operating actuals or standard TREO annual rate. Two alternative Yttria PDFs and other public documents are among the separately retained editions. Seven historical-source attempts produced six captures, five unique new SHA responses, zero of four expected old SHAs and zero display gains from the expanded universe. The WAMEX document route returned non-PDF evidence and its gated document list was left unresolved.

## Source accuracy and remaining evidence

The exact original attribute case set had 5 TP / 0 FP / 0 FN / 3 TN for JORC geology, 1 / 0 / 0 / 1 for JORC study and 1 / 0 / 0 / 0 for scoped issuer history. An independent Oracle checked 50 anchors across ten originals. The separate native resource Oracle found one known date-link false negative (Mt Weld) and no false positives in its named-case comparisons. Goschen's printed table class and source summary remain in conflict. The chemistry label and THM cutoff stay as reported. Family generalization is unmeasured and OCR was not run.

## Measured maintenance and processing cost

Each of the two matched profile rows uses three interleaved fresh processes, with the same cutoff, files and work scope. The third is the additional Stage 4 workload. Sample ranges overlap for matched wall time. OS page caches were uncontrolled.

| Scope | Wall median s | CPU median s | RSS KiB | Modules | Static/runtime edges | AST Max/P90 | Cold → resource-cache PDF opens/pages |
|---|---:|---:|---:|---:|---:|---:|---|
| Prior code, fixed Stage 3 profile | 8.219 | 8.321 | 96,376 | 35 | 147/111 | 61/20 | 15/912 → 7/679 |
| Current code, same Stage 3 profile | 7.718 | 7.867 | 96,504 | 36 | 151/112 | 61/20 | 15/912 → 7/679 |
| Current code, expanded display profile | 11.006 | 11.147 | 96,548 | 36 | 151/115 | 61/19 | 22/1487 → 14/1254 |

The matched builds preserve one static cycle, one runtime cycle, one duplicate AST group and 5,145 traced SQLite statements. Save-time scientific and temporal rereads remain intact. The separate display-only compiler median was 8.524s wall, 8.665s CPU and 98,980 KiB. It decoded 16 PDFs / 736 pages cold and 5 / 250 with reused display documents, wrote zero SQLite rows and invoked zero GDAL/OCR decoders. Its 34 reached modules, static/runtime edges 142/86, AST 68/20, zero cycles and two repeated AST groups describe the new compiler scope. They are separate from the matched core metrics.

## Verification and availability

C4-0 through C4-6 and F0–F4 pass in their recorded scope. F5 and old edition recovery remain blocked, and historical O2 remains failed. 363 exact-environment tests passed with zero skips. Two physical local CAS copies each reproduced 8/8 scientific files and 4/4 display files, including the display receipt. The copies share the local failure domain. Original-byte corruption and extension-asset corruption were refused. Chromium verified the source PDF and capture, extension filtering/sorting/export, historic selection, mixed-role refusal, mobile layout and candidate-only navigation. Historical Census CSV, profiles, V2 and official publication pointers retain their prior identities.

| PR | Tested head | Main merge | Successful runner seconds |
|---|---|---|---:|
| [#40](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/40) | `121f3152c0d9` | `293cdfedea31` | 151 |
| [#41](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/41) | `5fd58b3ff08f` | `d74f74cf22a3` | 78 |
| [#42](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/42) | `bc117e47aeac` | `877c73f1a2a8` | 92 |
| [#43](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/43) | `4a1c6509897e` | `aa1e71544347` | 80 |

The CI records cover work completed before the final delivery PR. Monetary billing is unmeasured. The final integration receipt is written after the final CI and merge.

[Machine receipt](census_display_coverage_verification.json) · [File lifecycle](census_display_file_lifecycle.csv) · [Coverage baseline](census_display_coverage_baseline.md) · [Field contract](census_display_field_contract.md) · [Typed attributes](census_native_attribute_batches.md) · [Resource facts](census_resource_display.md) · [Forecast and government records](census_forecast_location_display.md)

## Replay

Use the candidate code, fixed profile, bindings and source hashes in the receipt. Restore the scientific side from either CAS with `research replay --offline`. For a display replay from the cold outputs, retrieve the exact archived `build_receipt_sha256` object into the rebuilt working tree before running `research build-display`. The compiler independently checks the archived receipt and all eight science artifacts. Include the exact forecast and expanded-government options when rebuilding the complete display. See `research build-display --help` and the two independent verifiers in `tools/`.

Serve the unpacked candidate ZIP over a local HTTP server and open `/?preview=1`. The same package retains the Stage 3 data version. The separate source recovery ZIP is rechecked offline with `tools/recover_source_editions.py --verify`.
