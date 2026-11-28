# Census display context and source edition recovery

The 2026-11-28 Asia/Taipei profile projects three independently corroborated issuer study scenarios into nine source-reported display facts across six Project-by-column positions. Each fact links to the exact captured edition, physical page, native quote, subject, and use role. The production scenario review and independent pinned layout oracle are shared with the existing context verification command.

| Project | Display role | Original rate and product | Period evidence |
| --- | --- | --- | --- |
| Jupiter | `FORECAST` | Approximately 8,300 tonnes per annum of contained TREO in MREC | Physical pp. 8 and 21 establish a life-of-mine annual average including ramp-up and scheduling. |
| Jupiter | `ENGINEERING_PLAN` | Approximately 9,300 tonnes per annum of TREO contained in MREC | Physical pp. 8 and 21 establish the nominal design rate at 8.0 Mtpa processing capacity. |
| North Stanmore | `FORECAST` | Approximately 29,000 wet tonnes per annum of heavy rare earth enriched flotation concentrate | Physical p. 62 identifies the PFS base case. The averaging window is unspecified. |

The Jupiter life-of-mine MREC total of 201,400 tonnes remains a rejected rate control. The extension records have no formal research, production, or MCDA use. The North Stanmore wet concentrate product retains its own unit and material basis.

The separate seven-source official geography expansion supplies eight `SHADOW` evidence-pane entries and no location cells. The entry for the Commonwealth `ENO=763335` preserves its 5 m reported positional accuracy and its disagreement in role with WA `S0237005`. The WA Central Lanthanide `S0001709` entry preserves the identified issuer subsite association and unknown positional accuracy. Native feature applicability dates remain unknown. Entries carry the verified public capture date separately from the unknown source publication date. The Mt Weld gold homonym, Mount Weld road, and Jupiter prospect controls remain excluded from the evidence pane.

## Historical source recovery

Seven public locators were acquired anonymously before the cutoff under `EXPANDED_MANIFEST_HISTORICAL_EDITION_RECOVERY`. Six valid capture receipts locked five distinct bodies absent from the fixed corpus. The fourth target's report landing returned HTML where the historical edition required PDF. The expansion holds zero matching historical editions and adds zero display facts. An offline replay reopened the expansion manifest, source bodies, current capture events, and temporal decisions from content-addressed storage.

| Historical target | Result |
| --- | --- |
| Wudinna | `SOURCE_UNAVAILABLE`. London Stock Exchange and issuer pages returned two distinct HTML editions. The exact-URL Internet Archive CDX lookup returned an empty index. |
| Yttria | `SOURCE_UNAVAILABLE`. The live Markit route, the common Aspect Huntley and Market Index PDF, and the Internet Archive replay returned three distinct PDF editions. |
| Mt Stirling–Yttria | `SOURCE_UNAVAILABLE`. The shared Markit and archive routes recovered neither this historical digest nor an exact Project-scope proof. |
| Splinter | `SOURCE_UNAVAILABLE`. The WAMEX landing returned HTML. Its document and digital-file list requests returned sign-in pages. |

The fixed corpus manifest SHA-256 is `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`. The separate official geography manifest SHA-256 is `226c08be42beea1f085760f3fc7a0c0647b77eeb1bd361cdc7414fd582efee23`. The historical recovery expansion manifest SHA-256 is `eba8121e65d5654ef9abc23dcfa2b2d03961d4af618e11c0794496dad537a544`, with result receipt SHA-256 `3a876957b3660921ebb212335dde765690fce4d5c66368cf9465c60762f6f846`. Exact expanded bytes and the acquisition proof objects are retained with the operational workspace CAS. Historical Git object digests served solely as lookup targets.

The local context projection took 1.679 s for forecast and 0.083 s for official evidence in one process with peak RSS 45,360 KiB. Live historical recovery took 7.104 s with peak RSS 48,276 KiB. These measurements cover only the stated operations.

## Replay

Run the context verification tests with the fixed manifest and CAS and the official fixture unpacked in a separate directory. The fixture archive is `tests/fixtures/government_point_expansion_v1.zip`. The fixed archive is the same 144-edition input used by the main Census verification.

```bash
PYTHONPATH=src python tools/recover_source_editions.py --verify \
  --profile-path config/research_profiles/au_ree_2026-11-28.json \
  --fixed-manifest "$FIXED_SOURCE_MANIFEST" \
  --bindings config/research_profiles/source_edition_recovery_v1.json \
  --store "$EDITION_RECOVERY_CAS" --out "$EDITION_RECOVERY_RECEIPTS"
```

Omit `--verify` and supply empty, separate CAS and receipt directories to perform a new bounded public recovery. Dynamic public endpoints may return newer editions, which receive new capture receipts and digest identities.
