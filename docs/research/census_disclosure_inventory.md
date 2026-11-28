# Census 補值基準與揭露清冊

T0／T1 使用 `2026-11-28`、`Asia/Taipei`、93 個 Project 和 51 個核心欄位。Git 基準為 `aa92a35c3d5b3593c25b246edcd28ea36c4b0d9a`。運算版本為 `a7efe276563ff18b8b15c7a8d0d660ec8f0d8445`，封存候選為 `8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3`。固定清冊 144 份原件及 148 筆擷取。候選含上述固定清冊，共 147 份原件及 152 筆擷取，各 SHAs 與公開擷取收據均經現行驗證。93 案均有至少一份符合截日資格的原件。

兩份獨立本地 CAS 各自冷重播，兩次皆核對 8 個科學輸出。逐鍵比較 4,743 格全部相同。SQLite `integrity_check` 與 `foreign_key_check` 通過。核心值狀態為 1,141 有值、3,602 未知、0 不適用。可核驗展示共 49 格，18 直接科學、4 核准衍生、27 已核驗描述，共 13 案。9 個擴充欄另外計數，45／837 有值。R1 四鍵、S1 兩鍵、完整 R1＋S1 兩案、完整六構面零案。T0／T1 `PASS`、F0–F4 `PASS`、F5 `BLOCKED`、歷史 O2 `FAIL`。本清冊新增科學值、重分類核心值狀態及改變正式比較鍵均為 0。

## 真實缺值與歷史來源

| 未知格主要理由 | 格數 |
|---|---:|
| `SEARCH_HISTORY_UNKNOWN` | 3,476 |
| `SOURCE_NOT_ACQUIRED` | 98 |
| `EXTRACTED_FAILED_VALIDATION` | 14 |
| `CONFLICT` | 7 |
| `DISCLOSED_NOT_EXTRACTED` | 4 |
| `SEARCHED_NOT_DISCLOSED` | 2 |
| `PARTIAL_DISCLOSURE` | 1 |
| `NOT_SEARCHED` | 0 |

`DISCLOSED_NOT_EXTRACTED` 指已定位原件文字與量值，尚無該用途和範圍對應的型別化觀測。Mount Ridley 2026 年原件 p6 已定位 Inferred 資源資料。`SEARCHED_NOT_DISCLOSED` 僅限 Mt Weld 指定版次的 2.5% TREO 同用途資源表，另核對 2.8% TREO 儲量表。Wudinna p3 的 DyTb 群組屬 HREE8 部分揭露。引文從核准版次原件回讀。這些範圍限定的查核無法估計 93 案的公開揭露率。搜查收據不完整的 3,476 格維持 `SEARCH_HISTORY_UNKNOWN`。逐格保留全部核對註記和證據角色，主要理由以科學衝突、擷取失敗、已定位揭露、部分揭露、查核範圍內未揭露、歷史原件取得失敗、明示未查依序呈現。[原件查核範圍與引文](census_disclosure_search_review_v1.json)

V2 的科學版本 `0dff6beb5aca52e565c37289e3ab295737e76942`、claims 指紋及 `2af6f611e51eb4ac9dda73419d1afeba70d1307e` 封存輸出均核對。2,500 個 V2 有值格中，1,812 格全數連到同 Project 的 exact SHA，47 格僅部分連到，455 格完全未連到，186 格無舊 SHA。受未連到的歷史版次影響者合計 502 格，其中 280 格現為未知。缺件清冊含 68 案的 134 個 Project×expected SHA 鍵，合計 57 個不同 SHA。同 URL 的異版次另列實際指紋，未能確認 SHA 對應關係的多 URL 僅作線索。`SOURCE_NOT_ACQUIRED` 限本次基準中無匹配版次且有可核對的原件取得記錄。此清冊未執行網路要求。現有候選來源清冊已記錄 37 宗 `MIME_MISMATCH` 與 1 宗 `HTTP_404`。

4,743 格的歷史展示對照為 1,359 格僅 V2 有值、434 格兩版不同、695 格文字相同、12 格僅數值格式不同、2,243 格皆空。這項比較只量測欄面，科學範圍與有效日需依原件另證。V2 有 1,650 個 `NOT_APPLICABLE_NO_MRE`、57 個 `NOT_APPLICABLE_STAGE`、8 個 `NOT_APPLICABLE_UNLISTED_DEVELOPER`。對應 1,715 格的現行狀態皆為 `UNKNOWN`。歷史狀態用於搜尋與核查，現行的正式不適用須依欄位用途具肯定證據。

## 重跑方式

使用封存 `work/census-stage6/verified/` 的同一份版本規格分別讀取 `cas` 與 `replica-cas`。先執行 `research replay --offline` 各一次。比較前將封存 `build_receipt.json` 原件放入第一份重播的 `rebuilt/`，`research compare` 以封存的 `build/` 對重播後的 `rebuilt/` 執行。同一工作區的既有路徑可直接代入如下命令。

```bash
PYTHONPATH=src python -m tools.census_disclosure_inventory \
  --workspace /workspace/AU_REE_V3 \
  --v2-root /workspace/AU_REE_V2_reference \
  --replays /workspace/AU_REE_V3/work/census-fill/t0/cold-a /workspace/AU_REE_V3/work/census-fill/t0/cold-b \
  --comparison /workspace/AU_REE_V3/work/census-fill/t0/comparison \
  --out /workspace/AU_REE_V3/work/census-fill/inventory-new
```

`census_gap_inventory.csv` 提供全量逐格和歷史精確來源對照，`census_disclosure_distribution.csv` 提供總數、逐案、逐欄及逐格狀態。`missing_sources.csv` 提供預期原件、已知 URL、實際核對版次、擷取結果、受影響欄與優先工作。`projects_without_sources.csv` 無符合條件的列，保留欄頭。`v2_not_applicable_cells.csv` 提供歷史狀態與本版證據對照。`census_fill_inventory_receipt.json` 釘選各檔指紋。進一步尋回來源另出收據，基準檔保留發布時的版本範圍。兩次指定不同 Python 雜湊種子，六項輸出逐 byte 一致。[驗證收據](census_disclosure_inventory_receipt.json)

本工具共用現行 Census 投影政策、研究比對、來源版次、CAS 及 PDF 讀取，新增依賴為 0。兩次本地冷重播分別為 15.869 與 16.032 秒，user CPU 為 11.383 與 11.350 秒，system CPU 為 4.307 與 4.359 秒。冷重播峰值 RSS 為 `UNMEASURED`。含指紋驗證、原件抽查及輸出的清冊測量為 4.300 秒 wall、3.270 秒 user、1.009 秒 system，峰值 RSS 85,844 KiB。本文件、機器收據、範圍限定的原件查核、離線清冊工具及測試皆為本輪新增。未移除既有檔案。

## 歷史方法與取得來源

將上方命令最後一行的 `--out ...` 換成 `--provenance`，既有工具會把 `census-historical-provenance-v1` JSON 印至標準輸出。報告使用同一 Stage 6 封存、固定的 V2 Census 和逐格 provenance，以及 profile 釘選的來源目錄。它逐格取 V2 已記錄的原報、計算或描述方法，再以 Project 與原件 SHA 連到符合截日資格的 V3 擷取。擷取方式取自公開擷取收據。來源通路取自來源目錄的同 Project、同請求 URL 記錄。未登錄的來源通路標為 `NOT_IN_PINNED_CATALOG`。

2,500 個歷史有值格涵蓋 68 種方法與 193 個原件 SHA。2,314 格有 SHA、URL 和定位，1,425 格有頁碼。295 個歷史 Project×SHA 群組中，161 組有符合資格的同案擷取，15 組僅在其他 Project 有合格連結，119 組缺合格同版原件。1,859 格至少有一筆同案同版擷取，連到 146 筆 `ANONYMOUS_HTTPS_GET` 收據，其中 144 筆 PDF、2 筆 ZIP。格數按鍵去重，擷取按收據 SHA 去重，同一個格可在不同取得方法中各計一次。一個 Project×SHA 群組也可有多個來源通路。報告保存各群組的原件指紋、歷史方法計數及已核對的擷取收據引用。
