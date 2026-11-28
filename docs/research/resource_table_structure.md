# 稀土資源表格定位與欄義

以 `26fd781b1b039c85e7c4596bf11b3280584ad5a3`、PR #67 的逐格准入收據、V2 凍結版 `claims.json` 與兩組固定留出標註為基準。研究截日維持 2026-11-28（Asia/Taipei）。本輪量測原件全頁自動定位、欄位與單位綁定，以及原報資料可用於何種用途。

## 評估範圍

處置全部 691 筆歷史線索。以同 SHA 原件讀取 72 份來源、114 個指定頁面。固定回歸組保存原有 23 列、18 頁與 22 個標註數值。其餘歷史線索依文件家族、發行方及專案關聯分組，和固定組有關聯的來源獨立標記。每個方法先以原件頁面產生結構候選，再以歷史 marker 或封存留出標註計分。提供歷史位置的評估另列。未標註窗格的誤判率未量測，歷史變數名屬弱標籤，正式採納仍須原件、Scope、時間與用途驗證。

比較四條實際執行的路徑。Poppler 使用現行原生表格定位器。PyMuPDF 以原件頁座標分群，pdfplumber 依格線和文字對齊切分。CPU Table Transformer 固定 INT8 偵測及結構模型，只用模型框定位原件文字，數值仍由原件文字層取得。模型使用單執行緒 ONNX Runtime 1.21.1，權重、前處理、版本和 SHA 均由程式固定。[模型與權重清冊](../../src/au_ree_v3/table_transformer.py)

| 方法 | 固定組正例原報列的結構命中 | 自動原報數值 TP／FP／FN | 提供位置後 TP／FP／FN | 691 筆中唯一 marker 同列 | 原 260 筆重新唯一定位 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Poppler | 0／8 | 0／0／22 | 1／1／21 | 230 | 0 |
| PyMuPDF | 8／8 | 1／1／21 | 1／1／21 | 215 | 2 |
| pdfplumber | 8／8 | 1／1／21 | 1／1／21 | 182 | 0 |
| CPU Table Transformer | 5／8 | 0／0／22 | 0／0／22 | 78 | 0 |

結構命中可落在尚無欄義的列。PyMuPDF 找回的兩個歷史 marker 都位於同一個技術流程表列，資源欄義增益為零。PyMuPDF 可驗證的欄義開發標籤集中在 1 份原件，共 15 個位置。歷史獨立留出只有 3 個可用位置，另有 1 個未綁定欄義且能讀出表頭的位置。現有標籤覆蓋有限，表頭 TF-IDF 沒有開跑。現行定位器保留為全量預設，幾何法僅應用於已核對的候選展示，CPU 模型維持實驗。

主 CAS 的完整量測包含來源校驗及評分，Poppler 為 11.55 秒／82,428 KiB，PyMuPDF 為 12.06 秒／109,664 KiB，pdfplumber 為 77.00 秒／132,484 KiB，Table Transformer 為 85.75 秒／721,640 KiB。CPU 模型在副本的量測為 121.06 秒／719,304 KiB。共享主機的個別時間受同時執行程序影響。工程比較涵蓋同一套正式程式，模組數 73→76、靜態內部依賴 362→373，AST Max/P90 由 82／20 變為 82／19，循環維持 1、跨模組完全重複群維持 3。

## 正式用途與候選展示

Circle Valley 原件可獨立重現 98 Mt 和 890 ppm TREO，且可區分同一表內的 660 ppm TREO−CeO₂ 欄。印刷 cutoff 為 430 ppm TREO−CeO₂，Inferred-only 依現行正式規則維持拒收。原報化學式中的下標保留為分析物的一部分。編號腳註要求同一張表、同一列的標記，重複或衝突的 cutoff 保留未綁定。

Mt Clere Tower 在開發組。已核對的原報展示為 **101 Mt** 和 **840 ppm TREO**，同一表列的 `Total(1)` 對應 **300 ppm TREO−CeO₂** 腳註，並保留同表 Indicated、Inferred 列的原件位置。兩格原來都空白。候選展示會核對發行人擷取 SHA、公開證據、Project 名稱、表名、列首、欄名與印刷單位。正式 SQLite 選值及 MCDA 沿用各自規則。

在先前 54 筆歷史候選展示紀錄中，重讀新增 9 筆 cutoff 證據，涵蓋 4 份原件，並新增 19 筆原報分類證據，涵蓋 6 份原件。這些語境補強另行計數。

## 逐格與重播

網站候選的 4,743 格逐格比較從 1,181 增至 1,183 個展示值。Mt Clere Tower 兩格為原先 472 格外的新增路徑，沒有其他值變更或移除。原 472 格的雙 CAS 原件回讀及 SQLite 位元一致，正式首個關卡仍為欄義或用途 338、公開時間 78、JORC 類別 53、估算日 2、已通過 1。

候選研究 `a4b00db2670fd40d7c047a1af52d286c9f3700060ae953edfccf39c5cca0d57c` 的正式資料有 4,677 格一致、66 格僅調整版本血緣。正式科學格仍為 22、MCDA 可比較鍵 6、完整 R1＋S1 共 2 案、六構面完整 0 案。F0–F4 通過，F5 因同一故障域維持 `BLOCKED`。兩次離線重播的網站資料逐位元一致，Chromium 的雙版本互動測試通過。科學及候選網站採用程式 `56efa029c5c5ade51d1ac720b2bb12f1df889e22`，評估工具及四方法測量固定於 `42b6e0c96a79a0cd5a5c2a0d0c907ce4b0d314c1`。[機器收據](resource_table_structure_receipt_v1.json)

## 重跑

固定清冊與標註 SHA 列在機器收據。環境依實測版本選擇 `.[resource-tables]` 或 `.[resource-tables,table-transformer-cpu]`。下載模型後，程式會先核對兩個權重 SHA，再允許在離線模式使用。歷史原件可用先前取得的唯讀檔案庫。

```sh
PYTHONPATH=src python tools/compare_resource_table_structure.py \
  --method "$METHOD" --code-commit "$CODE_COMMIT" \
  --store "$CAS" --historical-assets "$V2_ASSETS" --v2-root "$V2_ROOT" \
  --manifest "$SOURCE_MANIFEST" 4ed939586a90708da79e95072eef363905e568e1463cb79a1c546ac379feff6a \
  --registry config/research_profiles/project_registry_2026-11-28.json 2262fa68e84e5ddedb72103bf7191fb09da95d1d3bad56b927d73af358c71e7c \
  --claims-sha256 d1cca97e5a3314f3778f48c6ec2af317bf9551f07e1d8371fae2e4191392c44e \
  --previous "$PR67_NLP_RECEIPT" 58e93f5faa48e5001d4bd9d9ee9ff2e86143fdfc117310b04fdff3eca672b586 \
  --holdout "$STAGE5_HOLDOUT" 208f59c6570f35e0e63e1378900d9bd872e492d8880eea844cbb6d5b7e820de0 \
  --holdout "$STAGE6_HOLDOUT" 9f92ea8f427bc24e3fefc200c82289748e7c79a0ace8320ac3a3e8e4027b33cf \
  --out "$RECEIPT"
```

Table Transformer 另附 `--transformer-models "$DETECTOR" "$STRUCTURE"`。候選網站使用既有 `research build-display`，加上 `--table-geometry-review config/research_profiles/resource_table_geometry_review_v1.json` 跑已核對的原報展示。相同規則從兩套 CAS 重跑時，程序會再次解析原始 PDF。
