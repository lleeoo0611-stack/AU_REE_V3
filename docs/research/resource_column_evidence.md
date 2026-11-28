# 原件欄義核對與弱標籤實驗

以 PR #68 合併提交 `f2bd40619a139b5a659f8fdded945d15b601b9e6` 為基準。固定 2026-11-28（Asia/Taipei）研究截日與原來源清冊。生產候選展示與測試固定於 `fa869ab4c70eb66d875092b9e7d81c63f8b5dc50`，診斷及模型固定於 `849c065bbd6e77eecea7d8bb0f82e5ca67d95372`。

## 260 筆歷史定位失敗

逐筆重讀 34 份同 SHA 原件、48 個指定頁面。137 筆可獨立重現原件表名、列首、表頭、單位與印刷數字的位置。這 137 筆包含 112 筆 marker 分布在同一表的不同區域、18 筆跨已核對的印刷表名、6 筆列 marker 不足但原件座標可核對，以及 1 筆數值等值格式。跨表 marker 可用於導覽，數值位置仍按個別原件欄列保存。

其餘 123 筆分為 marker 歧義 61、數字列證據不足 15、敘述文字 6、marker 分布於不同數值列 7、列關係未解 34。此組指定頁面沒有觀測到缺少文字層或近似數值差異。精度或捨入差異保留原報位數，格式相等以完整數字等值判定。137 筆的位置重現保留為證據，正式用途另以現行採納契約驗證。雙 CAS 診斷內容一致，耗時 71.51／73.57 秒，峰值 101,540 KiB。

## 152 格的確認表

歷史准入收據的數字路徑共有 152 格、756 條既有原件路徑，去重後是 11 份 PDF 中的 55 個欄位位置。產生 55 張確認卡與 12 張本機裁圖，保留表名、表頭、單位、列與相關語境。4 格屬直接原報欄位，132 格需要完整衍生輸入，16 格須核對語境或揭露事實。四個直接欄位的既有路徑都缺對應的同列目標值。從本機 CAS 另外驗證的新位置見下節。

本機確認表可篩選、勾選欄義、標註不確定或多重語境並匯出 CSV。匯入按確認表摘要及逐卡原件定位指紋核對。實測用 Splinter、Mt Ridley 與 Barkly 原件核對 5 筆標籤，Chromium 匯出後在雙本匯入，結果逐位元一致。確認表生成約 3.21／3.09 秒、峰值 86,044 KiB。腳本實測瀏覽器操作中位數為 0.49 秒，人工判讀耗時未量測。原件裁圖保存在本機工作目錄。

## 弱標籤與擴大留出

從 215 筆原件唯一同列線索重讀，145 筆可唯一定位原件數字欄。發行人、報告家族、相同 Project 的所有版本共同分組。排除 55 筆凍結留出關聯及 2 筆重複位置後，使用 88 個位置訓練，涵蓋 22 份 PDF、17 個群，80 個位置可抽到表頭。其餘 70 筆分別為不支援或衍生變數 34、未提供可核對的數值 marker 22、值重複 13、無相符原件出現 1。

擴大留出有 120 個獨立實體列、12 份同版原件、10 個預先保留群。依原件表頭及單位另行核對，含 229 個資源角色位置、796 個其他欄位對照。模型只使用自行解析的表頭、單位與數值欄相對位置。固定 23 列／22 值另以已定位欄位回歸。表內指標為資源角色的 micro 分數。

| 評估 | 方法 | TP／FP／FN | Precision | Recall | F1 |
| --- | --- | ---: | ---: | ---: | ---: |
| 獨立 120 列 | 表頭＋單位規則 | 67／46／162 | 59.3% | 29.3% | 0.392 |
| 獨立 120 列 | 表頭 TF-IDF＋位置 | 125／587／104 | 17.6% | 54.6% | 0.266 |
| 固定 22 值 | 表頭＋單位規則 | 10／0／12 | 100% | 45.5% | 0.625 |
| 固定 22 值 | 表頭 TF-IDF＋位置 | 16／4／6 | 80.0% | 72.7% | 0.762 |

擴大留出集的模型誤判含 500 個其他欄誤標為資源角色，另有 87 筆誤標成另一資源角色。16 個原報去鈰品位位置中，12 個被模型判成普通 TREO。模型維持影子用途，固定組的提升保留為局部結果。雙 CAS 的逐項、各角色及總評分一致。兩次全部量測約 24.84／23.69 秒，峰值 103,608 KiB。

## 進入候選展示的原報數字

Redlings 由首頁唯一的 Inferred Mineral Resource 聲明，連到同一份發行公告中的 Table 1 及 Table 2。兩表的 **800 ppm TREO** cutoff 列與聲明一致，讀回 **11 Mt、1,130 ppm TREO、12,430 t**。原報類別維持 Inferred-only，列為候選展示。其三格位於既有 472 格准入清冊外。

Koppamurra 在原 152 格範圍內。原件第 8 頁 Table 2 的第一個 Total 列、Material (Mt) 欄讀回 **243 Mt**。同頁延續表的另一個 Total 有獨立定位。原件科學審查核對第 28 頁 Table 3 的同一列，讀得 **325 ppm TREO−CeO₂** cutoff，不符合純 TREO cutoff 的正式准入用途。四個新位置均由候選展示流程重讀，正式表仍標 `NO_SELECTED_MRE`。機器收據分別記錄每格的驗證位置、已知後續門檻和正式路徑未執行狀態。

## 逐格與重播

候選研究 `10a879673d744d186b29254fc37321486328effc11b15289e569d8b187c25b68` 的網站展示從 1,183 增至 1,187 格。比較全部 4,743 格，僅上述四格由空白變成有值，沒有移除。原 472 格的正式准入漏斗在兩套 CAS 重跑一致，仍有 338 格卡在欄義或用途。正式科學格維持 22，MCDA 可比較鍵維持 6，完整 R1＋S1 維持 2 案，完整六構面仍為 0。正式 Census 比較為 4,677 格一致、66 格僅更新版本血緣。

兩次冷重播、獨立 MCDA 計算核對與 Chromium 雙版本互動均通過，網站資料逐位元一致。F0–F4 通過，F5 因缺少獨立故障域維持 `BLOCKED`。科學建置 13.45 秒，兩次冷重播 18.44／18.07 秒。本輪套件模組 76→77、內部相依 373→377，AST Max／P90 由 82／19 變為 82／20，循環和跨模組完全重複群仍為 1 和 3。

## 重現

`tools/diagnose_resource_claim_locations.py` 讀固定 V2 repo、先前 PR #67 的 NLP 收據與精確 CAS。`tools/build_resource_column_oracle.py` 根據現有 PR #68 發行人切分與 [原件留出標註計畫](../../tests/fixtures/resource_column_annotation_plan_v1.json) 重建擴大留出。兩個 holdout 與其 SHA 維持 [PR #68](resource_table_structure.md) 的固定入參。使用 `tools/evaluate_resource_column_roles.py --benchmark PATH SHA256 --oracle PATH SHA256` 搭配相同固定來源、Project 登錄、V2 及 holdout 入參；各輸入指紋保存於機器收據。

```sh
PYTHONPATH=src python tools/resource_column_review.py prepare \
  --funnel docs/research/historical_cohort_admission_receipt_v1.json 161858ee934a02798560b08db6662d9189908ad16baf9ecc1b0900e84644dd75 \
  --candidate-sqlite "$CANDIDATE_SQLITE" --store "$CAS" --out "$QUEUE"

PYTHONPATH=src python tools/resource_column_review.py import \
  --queue "$QUEUE/queue.json" "$QUEUE_SHA" --labels "$LABEL_CSV" --out "$ANNOTATIONS"
```

候選網站使用現有 `research build-display --table-geometry-review config/research_profiles/resource_table_geometry_review_v2.json`。源 PDF SHA、已核准同版公開證據、原文 scope、欄位與數值均從原件重播。[機器收據](resource_column_evidence_receipt_v1.json)
