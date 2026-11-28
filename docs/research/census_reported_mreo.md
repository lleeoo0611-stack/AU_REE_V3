# Wudinna reported magnet rare-earth oxide mass

The opt-in profile `au_ree_census_evidence_v1.json` enables a separately pinned Census formula. Existing profiles, historical archives, and MCDA rules retain their previous scientific roles. The formula records reported aggregate mass in the Census column `磁鐵稀土氧化物蘊藏量_kt`.

Wudinna's exact 26 October 2026 issuer PDF, SHA-256 `89467aedb62238c6a98902633fba1bcaf4ccd0f081beaa736facf71533ac3864`, records combined Garford and Pidinga Indicated and Inferred Mineral Resources effective 22 October 2026. Page 3's Total row gives 150 Mt ore, 470 ppm TREO, and 112 ppm named MREO. Page 12 states that densities represent dry in-situ tonnes. The page 15 companion table image corroborates the combined resource context and finer tonnage precision. Page 1 distinguishes the 200 ppm TREO excluding Ce open-pit cutoff and the US$5.27/t Pidinga ISR economic cut-over. Both belong to the observed resource statement.

`150 Mt × 112 ppm × 0.001 = 16.8 kt`, with a reported-precision interval of `[16.66925, 16.93125]`. The three already selected inputs keep their exact Observation IDs. TREO grade verifies that they belong to the same resource statement and is outside the calculation. The reported group defines Nd₂O₃, Pr₆O₁₁, Dy₂O₃ and Tb₄O₇. The aggregate establishes no individually measured contribution from those four oxides. The result is display-only for the approved Census purpose.

The independent Oracle re-reads the fixed original pages and the embedded image, computes the Decimal bounds, and checks the one approved store record and Census cell. It also recomputes the existing Wolverine shadows. Wolverine's 0.85 kg/t versus 840 ppm Dy₂O₃ and 5.68 kg/t versus 5,700 ppm Y₂O₃ have disjoint reported intervals. Unresolved chemistry remains in the shadow record. No cross-edition equivalence, complete HREE8 or 15-oxide total was established.

The new formula uses the existing temporal selection and SQLite DerivedValue transaction. The profile pins the reviewed source and formula registry. Existing TREO validation still runs for its own two inputs. Census and web validation recheck scope, source, material, chemistry and all three input links before exposing the result.

Run `python tools/verify_reported_mreo.py --help` for the independent official-original replay.
