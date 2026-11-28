# Census 歷史缺口與獨立來源版次查核

本輪以 `44bc122d1337434cb8f496d8d0d6d0dd2dc4d981` 的 Stage 4 主線為起點，沿用 2026-11-28 Asia/Taipei 截日。固定清冊保留 144 個原件 SHA 與 148 筆合格擷取，SHA-256 為 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`。

## 1,396 個歷史展示缺口

1,396 格來自 `a0fcf28adb72625f703421011a8de2d965cbbcb7` 的歷史診斷。以唯讀 V2 `2af6f611e51eb4ac9dda73419d1afeba70d1307e` 核對原清冊、方法和欄格鍵，另按 Project ID 與原件 SHA 查核固定來源。1,121 格各至少有一份精確同案版次，後續缺核准欄位證據，列 `EVIDENCE_INSUFFICIENT`。275 格在固定清冊缺可配對的精確同案版次，列 `SOURCE_UNAVAILABLE`。資料截至較新 Stage 4 的歷史差異另有 1,362 個 V2-only 格，使用獨立基準與收據。兩個版本的展示格數供缺口排序，正式觀測與 MCDA 分母沿用各自的研究契約。

| 最小待補證據 | 歷史格數 |
| --- | ---: |
| 同一資源聲明的氧化物列、成員、單位與分母 | 548 |
| 發行方具日期且明示完成的專案里程碑 | 263 |
| 原件明示礦床地質及正確範圍 | 166 |
| 核定 Project／Deposit、估算日、cutoff、級別與礦石或 TREO 原列 | 109 |
| 同一聲明已驗證輸入及核准資源公式 | 24 |
| 政府物件 ID、角色、CRS、位置精度及有效日 | 128 |
| 日期適用的官方上市或登錄證據 | 92 |
| 研究情境、產品終點與年量／期間定義 | 47 |
| 符合專案範圍的原件歸屬及公開擷取 | 19 |

逐格清冊保存 51 欄根因統計、來源版次、原始定位、日期與範圍線索、個案判斷及後續用途。V2 展示值僅按既存指紋核對。不同 `PYTHONHASHSEED` 重跑產生相同 CSV 與收據。Stage 4 的 Wudinna、Yttria、Mt Stirling–Yttria、Splinter 四份歷史指定版次均維持 `SOURCE_UNAVAILABLE`，七次原擷取已重新離線驗證。政府 `Splinter` 與 OD6 的 `Splinter Rock Rare Earth Project` 採用各自的 Project ID。

## 新公開原件的獨立清冊

四次匿名 HTTPS 請求在截日前完成，取得四筆可重讀擷取證據及三個原件 SHA。兩個 Barkly 端點回傳相同內容。Tower 的交易所與發行商通路回傳兩個不同 SHA，紀錄同一資源聲明。兩案均經原件重讀，來源版次與公開日期資格成立，正式用途條件仍待符合。

| Project 與發布版本 | 原件定位及用途判斷 |
| --- | --- |
| Barkly `AU-ad64122c217f0dbfabc8`，2026-11-27 發行 | 第 5 頁表 1 的 2026-11-26 聲明總列為 205 Mt、2,100 ppm TREO、420,000 t TREO、140,000 t MREO。第 38 頁證實乾基，第 5 頁列出 15 個 TREO 氧化物。JORC 2012 級別全為 Inferred，570 ppm cutoff 的對象為 MREO，故未達現行 Census 正式資源與 R1 門檻。 [發行方公告](https://barklyrareearths.com/announcements/VQdLg4-five-fold-growth-in-rare-earth-resource-at-barkly) |
| Tower `AU-a8258e4989c7bd95b7af`，2022-11-21 發行 | 第 2 頁表 1 合併 Indicated 與 Inferred 為 101 Mt、840 ppm TREO。原件的主要 cutoff 為 300 ppm TREO-CeO₂，另缺經核對的估算有效日。第 2 頁 Exploration Target 保留原用途。 [交易所原件](https://announcements.asx.com.au/asxpdf/20221121/pdf/45ht5fzls7pc7t.pdf) |

擴充來源新增 3 個 exact SHA、2 組原件聲明脈絡核對和 0 筆獨立正式驗證觀測、正式選值、Census 科學格、Project×criterion 或完整面板個案。本工作包的每新增正式值成本為 `UNMEASURED`，因正式新增量為零。擷取耗時 3.581 秒、峰值 RSS 41,316 KiB。離線核驗三份 PDF 的 42 個實體頁，耗時 0.313 秒、峰值 RSS 23,964 KiB。實際收據與擴充 CAS 路徑見機器收據。此工作包本地測試 5/5 通過，完整 CI 由整合 PR 記錄。[機器收據](census_source_version_verification.json)

## 重播

```bash
PYTHONPATH=src python tools/census_v2_coverage_diagnostic.py \
  --v2-root "$V2_READ_ONLY" --historical-gaps "$HISTORICAL_DIAGNOSTIC_CSV" \
  --sources "$FIXED_SOURCE_MANIFEST" --out "$NEW_HISTORICAL_GAP_OUTPUT"

PYTHONPATH=src python tools/recover_source_editions.py --verify \
  --profile-path config/research_profiles/au_ree_2026-11-28.json \
  --fixed-manifest "$FIXED_SOURCE_MANIFEST" \
  --bindings config/research_profiles/public_source_version_candidates_v1.json \
  --store "$SOURCE_EXPANSION_CAS" --out "$SOURCE_EXPANSION_RECEIPTS" \
  --native-review config/research_profiles/public_source_version_native_review_v1.json \
  --review-output "$NEW_NATIVE_ASSESSMENT_RECEIPT"
```

現行程式擴充既有唯讀診斷和獨立版次擷取工具。來源候選、固定原件綁定及驗證結果以功能命名保留在工作樹。原件 PDF 與逐格大型清冊留在操作環境的內容定址儲存及 `work/`。
