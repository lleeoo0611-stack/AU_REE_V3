# Census Project attribute coverage

The opt-in profile `config/research_profiles/au_ree_census_attributes_v2.json` adds two completed feasibility studies using the frozen cohort of 144 source SHA-256 hashes. The new readings use the existing Census display purpose and keep the original date precision.

| Project | Reported event | Native evidence |
| --- | --- | --- |
| Avonbank Mineral Sands Project | DFS completed in 2021 | Austrade prospectus, physical page 46, names the Avonbank Project and the completed study in a single sentence |
| Copi Mineral Sands Project | DFS completed in January 2024 | Same prospectus, physical page 47, identifies the Copi DFS and its completion month before discussing future enhancements |

The two Projects share the same official document edition. The publication notice on physical page 2 states March 2026. The exact anonymous public capture is dated 28 November 2026 in Asia/Taipei, within the research cutoff. The Census renders a historical study milestone with the cited date and Project scope. [Australian Critical Minerals Prospectus](https://international.austrade.gov.au/content/dam/austrade-assets/international/documents/reports/critical-minerals/australian-critical-minerals-prospectus-march-2026.pdf)

The exact Goschen Area 1 report identifies a “2023 Definitive Feasibility Study” and describes it as completed. The completion verb has no attached event date. Dubbo contains a 2013 completion date in the Project history section. The unambiguous named Project anchor is beyond the existing locality bound. Both remain unselected. The two Donald phase-level studies and the Koppamurra timing and scope controls retain their earlier outcomes. The Browns Range JORC completion date appears in a separate table cell whose native page text interleaves the explanation column, so its completion date requires a layout-aware read before this policy can use it.

## Frozen comparison

- The same 4,743 Census keys yield six new descriptive cells across two Projects. Each independent study produces a stage code, a category, and a historical display label.
- The prior five Mt Weld and Balranald values retain their wording, source SHA, locators, anchors, validations, and temporal decisions. Their selection and cell lineage identify the new opt-in profile.
- The result carries four selected attributes across four Projects and 11 descriptive cells. The Goschen reading adds one unselected assertion. Dubbo stops before typed assertion.
- The 16 direct and two derived numeric cells, 42 numeric candidates, 27 validated observations, 21 selections, and six comparable Project×Criterion keys are preserved. The normalized MCDA inputs, panels, scores, and order agree. The original numeric observation artifacts agree byte for byte.

## Reproduce

Build the v1 and v2 opt-in profiles with the same committed code, `resource_bindings_v4.json`, fixed manifest SHA `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`, and verified CAS. Run the independent command below with the two output directories.

```bash
python tools/verify_census_attribute_breadth.py \
  --before "$attribute_v1_build" --after "$attribute_v2_build" \
  --assets "$source_assets" --out "$attribute_oracle_receipt"
```

The verifier checks the complete keyed Census diff, independently re-reads original PDFs with Poppler, verifies the Austrade public response, checks historical refusals, and compares all eight build artifact fingerprints with their receipts. Comparison of MCDA documents verifies and removes release-specific profile and linked artifact fingerprints before testing exact content equality. Release archival and cold replay follow the existing research commands.

## File lifecycle

| File | Disposition |
| --- | --- |
| `config/research_profiles/au_ree_census_attributes_v2.json` | Added opt-in profile with immutable component fingerprints |
| `config/research_profiles/census_attribute_bindings_v2.json` | Added versioned case references while preserving the previous references |
| `config/research_profiles/development_milestone_bindings_v2.json` | Added exact Project scope, source captures, physical pages, quotes, and date roles |
| `tools/verify_census_attribute_breadth.py` | Added the independent scope, source, and frozen Census comparison |
| `tools/verify_census_attributes.py` | Reused existing PDF and artifact checks in both independent verifiers |
| `tests/test_project_attributes.py` | Added exact-source and profile-version regressions |
| `docs/research/census_attribute_breadth.md` | Added scientific evidence and replay notes |
