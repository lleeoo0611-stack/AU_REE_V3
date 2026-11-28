# 磁性氧化物原件表頭查找

使用現成候選索引與原生 PDF 位置，定位同頁分別列出 Pr₆O₁₁、Nd₂O₃、Tb₄O₇、Dy₂O₃ 的定量表格。結果保留來源 SHA、物理頁、表頭原始欄位、印刷單位行與附近數值列。重複公式位置一併留供辨別單項和合計。數值尚待逐欄綁定，Scope、材料及正式用途須經既有證據契約驗證。新增觀測、正式選值、Census 值及可比較鍵皆為 0。OCR 與模型推論未執行。 [機器收據](oxide_table_retrieval_receipt.json)

## 固定原件評估

以 Stage 6 擴充來源建置索引。147 個原件中 145 個支援原文解碼，2 個容器未索引，共 58,723 個區塊。從八個不同 Project 與報告家族的 exact-SHA 公開 PDF 標註 127 頁。三個定量表格正例各來自獨立發行方，另有 124 個負頁。頁面級比較固定每份原件至多三個相異實體頁。

| 方法 | TP／FP／FN／TN | 精確率 | 召回率 |
|---|---:|---:|---:|
| 既有 S1 BM25 關鍵字 | 1／7／2／117 | 12.5% | 33.3% |
| 候選原生氧化物表頭 | 3／0／0／124 | 100% | 100% |

12 個預先定義的單項表頭全部命中。另找回一個原印的合計欄重複公式，依單項表頭目標計為額外命中。原件回讀逐一核對表頭、數值見證與單位行。表頭 TN、數字擷取精度及正式採納皆為 `UNMEASURED`。BM25 未命中此評測目標的頁面仍可能含其他 S1 線索。

標註直接依原件並在實作前凍結。設計者兼任標註者，實作前了解此樣本的版面。這批資料用來驗證已記錄原件上的查找效果，取樣範圍不足以評估未見報告的泛化表現。

## 同範圍工程量測

沿用同一份唯讀索引，兩個方法各執行三次，每次八個 Project 查詢並交錯次序。量測包含索引回讀與寫收據。查詢本身未解碼 PDF，原件回讀在計時外進行。每個方法的八件候選結果均在三次執行逐件位元一致。舊版 `aa92a35` 與新版預設 BM25 的同一實際 CLI 收據也逐位元一致。

| 指標 | BM25 | 原生氧化物表頭 |
|---|---:|---:|
| wall 秒中位 | 0.516 | 0.755 |
| CPU 秒中位 | 0.515 | 0.748 |
| 峰值 RSS KiB 中位 | 21,804 | 22,380 |
| 主要 SQLite SELECT | 45 | 19 |
| SQLite FTS 內部 trace callbacks | 749 | 0 |

單個同 Project CLI 查詢的可達架構量測比較舊版、新版預設與新方法。模組數依序為 9、11、11，靜態相依邊為 14、18、18，執行期相依邊為 4、4、5。AST 最大分數皆為 32，P90 為 8、15、15。三組均未發現循環或跨模組重複函式群。此次未新增第三方相依。候選來源、路由、表格與歷史提示的 31 項測試通過，遠端 CI 尚未執行。

## 重建

```sh
python -m au_ree_v3 sources index-evidence --manifest "$FIXED_MANIFEST" \
  --store "$EXACT_SOURCE_CAS" --out "$INDEX_DIR"
python -m au_ree_v3 sources search-evidence --purpose S1 --strategy oxide-table \
  --index "$INDEX_DIR/candidate_text.sqlite" --project-id "$PROJECT_ID" --out "$REVIEW_DIR"

PYTHONPATH=src python tools/evaluate_oxide_table_retrieval.py --strategy oxide-table \
  --index "$INDEX_DIR/candidate_text.sqlite" --store "$EXACT_SOURCE_CAS" \
  --panel "$AUTHORIZED_SEALED_PANEL" --panel-sha256 "$PANEL_SHA256" \
  --code-commit "$PINNED_CODE_COMMIT" --out "$MEASUREMENT_DIR"
```

封存原件標註及逐例收據依授權存取。測試下一批未見來源時，優先以獨立盲註檢查泛化，並量測合計欄、重複表頭及跨頁的精確數值綁定。

## 分組 NLP 原件列評估

`grouped-nlp` 在記憶體中訓練模型並輸出彙總 JSON。實驗辨識已定位的原件是否為稀土相關資源表列。以唯讀 SQLite 複本重接觀測、候選、驗證及原件錨點，從 9 份 exact-SHA PDF 重綁 55 筆，解碼 255 頁。篩除敘述、位置不唯一或不符合驗證條件的資料，並將同一表列的多項數值去重。原件部分提供 9 個正例，另加入 Stage 3 的 8 個原件標註和 3 個填值輪的分析試樣反例。

訓練集含 15 個原件家族、20 列。固定 Stage 5／6 留出集共 14 個家族、23 列，含 8 個正例。檢查 Project、exact SHA、報告家族、已知發行方及數字遮罩後完全相同的原文片段。沿用凍結的標註，因此這是可重跑的回歸評估。報告家族之外的相似版面泛化尚未量測。

| 留出比較 | TP／FP／FN／TN | 精確率 | 召回率 | F1 |
|---|---:|---:|---:|---:|
| 字元 TF-IDF 與邏輯迴歸 | 8／11／0／4 | 42.1% | 100% | 0.593 |
| 同樣已定位原件的詞彙規則 | 8／0／0／15 | 100% | 100% | 1.000 |
| 既有原生自動定位 | 0／0／8／15 | `UNMEASURED` | 0% | 0.000 |

執行與重綁通過，模型候選用途門檻為 `FAIL / HYPOTHESIS_NOT_SUPPORTED`。錯誤類型包括儲量、試樣及其他分析物。1,823 個字元特徵，訓練及推論約 0.189 秒，包含原件回讀的整體時間約 4.042 秒。正式觀測或採納增加 0，精確數值和欄義擷取及正式採納指標為 `UNMEASURED`。模型和推論資料未落盤，研究資料庫與 Census／MCDA 仍按原契約產生。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B tools/evaluate_oxide_table_retrieval.py grouped-nlp \
  --code-commit "$PINNED_CODE_COMMIT" --store "$EXACT_SOURCE_CAS" \
  --manifest "$FROZEN_SOURCE_MANIFEST" 4ed939586a90708da79e95072eef363905e568e1463cb79a1c546ac379feff6a \
  --bindings config/research_profiles/resource_bindings_v6.json 8194d0bfe95041e0ef1f4c90810b74361d0eccd8bf0bef045f381a89d267b0be \
  --archive-sqlite "$AUTHORIZED_SEALED_SQLITE" 0a6b91607ec25358ae767ec8bde2fcc101a9e93684282444237cdd177d006859 \
  --train-rows "$SEALED_STAGE3_ANNOTATIONS" 7060bee87ee9e7a13a5094cd90c4152b4a20db798e85f2cf242f470f5dc94235 \
  --assay-negatives "$SEALED_FILL_OXIDE_ANNOTATIONS" c69eba89aba8e7863806238142f13d85107a503153d91611699cf3854f922b71 \
  --holdout "$SEALED_STAGE5_ANNOTATIONS" 208f59c6570f35e0e63e1378900d9bd872e492d8880eea844cbb6d5b7e820de0 \
  --holdout "$SEALED_STAGE6_ANNOTATIONS" 9f92ea8f427bc24e3fefc200c82289748e7c79a0ace8320ac3a3e8e4027b33cf
```

資料組合 SHA-256 為 `1b6d1ece9a7dde6c6b7eb4c81617ff0139e087fb6040cacaf32bf006be888468`。標註和原件依既有封存權限讀取。

可給同一命令加上 `--recovered-sqlite PATH SHA256 --recovery-registry PATH SHA256`。擴張資料從已執行原件回讀的候選 SQLite 讀入，透過外鍵連接 Project、已核驗 SHA 與原件錨點。訓練只納入有歷史定位、原報表頭及稀土資源角色的唯一列。同頁同列的多個數值做去重，排除留出原件、Project、已知發行方與相同特徵。兩個模型使用同一留出組評估，收據分別記錄有無額外 SQLite 資料的結果。
