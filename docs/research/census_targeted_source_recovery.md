# Census 缺件尋回與同版時序驗證

本工作包從 `aa92a35c3d5b3593c25b246edcd28ea36c4b0d9a` 起步，沿用 2026-11-28 Asia/Taipei 截日及 Stage 6 研究輸入的 147 個 SHA、152 筆取得事件。變更風險列 `high`，因尋回工具會建立可供時間判定的擷取血緣。先對 134 個缺失的 Project＋SHA 關係、57 個唯一目標 SHA 比對 20 份不同指紋的既有 V3 清冊與工作區 CAS。CAS 有 6 個目標的原始位元組，清冊中有 1 個合格且同 Project 的配對，即 Yangibana 的修訂 DFS。其他 Project 的 11 個配對僅保留尋回線索。V2 的快照、時間欄與相關結果作唯讀對照。

## 實際取得及科學用途

三份獨立清冊合計 5 次已入收據的公開請求、5 次 HTTP 200 PDF 擷取、3 個不同原件 SHA。兩個原件 SHA 的 3 次重取通過日期資格，使用已查核的一組 Yangibana 和兩組 Yttria 截日前擷取憑證。回讀後的分層結果如下。

| 原件 | 取得及時序 | 原件科學查核 |
| --- | --- | --- |
| Yangibana 修訂 DFS，`99233befa81a1e193848122f00dfaa25079d0b2adbc2f19ef96326ca63c38f2a` | `RECOVERED_EXACT`。新取得已跨入 Taipei 11-29，使用 Stage 6 R1 擴充清冊的同 SHA 原始擷取，時間合格。 | 第 7 頁同總列掛在 Ore Reserves 標題下。第 32 頁把六主要礦床和其他已報礦床分別繫於 `0.24% TREO` 與 `0.20% NdPr`。維持 `SCIENTIFIC_CONFLICT`。 [ASX 修訂 DFS](https://announcements.asx.com.au/asxpdf/20260713/pdf/071n8v54p9pdql.pdf) |
| Lyons 12&13 歷史附錄，`9c9df3bfd3cca3c86946638121c09cd07117d1bea12737ecfc06dbf8b7c14334` | 交易所回傳內容符合歷史 SHA。實際取得時間為 `2026-11-28T16:19:20.986353+00:00`，對應 Taipei 11-29。V2 保留快照與歷史日期，仍缺符合現行契約的同版截日前回應收據，列 `SOURCE_AFTER_CUTOFF`。 | 第 8 頁的 2023-10-25 表冠明示 Inferred，0.2% TREO cutoff。12&13 列為 0.69 Mt、0.33% TREO、0.15% NdPr、2,266 t TREO，乾基。保留與 Lyons 11 和總列的分隔。時序與核准分類兩項均受阻。 [ASX 原件](https://announcements.asx.com.au/asxpdf/20240315/pdf/061k5t4brnh2bl.pdf) |
| Yttria 合法替代版次，`339bb9ebcf737b2c5b30e0970aca49f31bf462a4384353ca1743f1ca85f6b811` | 新查的發行商通路及 Aspect Huntley 各回傳相同替代版。兩份 Stage 4 精確擷取證明最遲於 11-28 已公開，11-29 重取時間依各自收據保存。歷史指定 `73936d12…` 持續 `SOURCE_UNAVAILABLE`。 | 第 2 頁總列 15.0 Mt、490 ppm TREO。第 2、18 頁說明主要 cutoff 為 200 ppm TREO−CeO₂。仍缺經驗證的估算有效日。維持 `EVIDENCE_INSUFFICIENT`。 [發行商 PDF](https://wcsecure.weblink.com.au/pdf/ASR/02795766.pdf) |

其他歷史目標 Wudinna、Mt Stirling–Yttria、Splinter 延用既有確切缺件收據。本輪沒有重查這三條未出現新線索的路徑。不同 Project 的同版或原件快照不代替其專案連結或公開證明。

只用兩份時間合格的取得清冊與 `sources.combine_acquisition` 建立獨立候選資料範圍，讀回原件與引用證據並核對既有 152 筆取得事件逐筆一致。驗證後範圍為 149 個 SHA、155 筆取得事件，候選清冊 SHA-256 `97bf6d725343d25dc1532e781bd6f16d5c8f4f6c2e10550b2a7c37d4144da3da`。本工作包新增正式觀測、Census 值及 MCDA 鍵皆為 0，獨立來源清冊留作後續用途審查。

## 取回方式與驗證成本

`tools/recover_source_editions.py` 擴充 `--prior MANIFEST CAS`，依輸入明示的清冊 SHA、原件 SHA 和原始收據驗證早期證據。只移入這次相同位元組所用的合格證明。保留舊擷取時計與新取得時計。較晚回傳的不同 SHA 依自身原始證據判定。原本的 Source、DocumentCache 及 Temporal Gate 責任分工延用。正式採納和資料模型無變更。分開的來源方案與查核設定保存三份收據的可重跑輸入。既有來源與研究發布均保留參照。

三份擷取收據耗時 3.550、3.489、3.805 秒。整組離線重播三份 PDF、58 頁，wall 0.684 秒、CPU 0.687 秒、峰值 RSS 33,540 KiB，獨立 hash seed 下三份報告逐位元一致。候選來源清冊合併驗證耗時 1.036 秒，峰值 RSS 49,976 KiB。6 項本地測試全通過，涵蓋時區午夜、相同 SHA、不同 SHA 和舊收據損壞反例。CI 由整合工作記錄。 [機器收據](census_targeted_source_recovery_verification.json)

## 重跑

```bash
PYTHONPATH=src python tools/recover_source_editions.py --verify \
  --profile-path config/research_profiles/au_ree_2026-11-28.json \
  --fixed-manifest "$FIXED_144_SOURCE_MANIFEST" \
  --bindings "$SOURCE_EDITION_ACQUISITION_BINDINGS" \
  --store "$CAPTURE_CAS" --out "$CAPTURE_RECEIPTS" \
  --native-review "$SOURCE_EDITION_REVIEW_BINDINGS" \
  --review-output "$NEW_NATIVE_REVIEW_RECEIPT"
```

操作路徑 `work/census-fill/sources/` 有取得、原件確認、逐筆 `blocked_sources.csv`、獨立來源清冊與 CAS。首次來源尋回可拿掉 `--verify`，使用 `--prior` 指定方案中的已固定清冊、對應 CAS，並另提供空白輸出和新儲存路徑。離線驗證從新 CAS 讀回其已有的證明與清冊。
