# Census 核心 51 欄展示基準

Stage 4 固定 main 為 `293cdfedea31874e0d757a7513695aaa77da30f0`，Stage 3 候選為 `47bcd58e568dc912f78c887ad83377adc891f81a472b3e71a266cf2e5abd60dd`。原候選由兩份本機 CAS、兩個新程序各離線冷重播，兩次均確認八件科學產物。93 個 Project 的 144 個原件 SHA 和 148 次合格擷取全部重新核對。兩份 CAS 位於同一檔案系統，F5 維持 `BLOCKED`，歷史 O2 維持 `FAIL`。[基準收據](census_display_coverage_baseline_receipt.json)

4,743 格中有值 1,122 格（23.66%）。30 格具有核准用途與逐格來源，其中 16 格直報、3 格正式衍生、11 格經驗證描述，分布於 9 個 Project（9.68%）。17 個不同原始 Observation 支持直報或衍生展示，另有 4 個獨立文字斷言及 3 筆受控公式結果，共涉及 8 份原件。行政身分、來源清冊及由既有資源主張產生的標誌排除在展示覆蓋指標。原 51 欄與研究等價，數值候選／驗證／正式選值 42／27／21，MCDA R1 四鍵、S1 兩鍵、完整面板兩案，矩陣、權重、排名及敏感度一致。此單元新增值為零。

V2 唯讀取得的 field provenance 有 2,500 格歷史顯示記錄。工具對齊完整 4,743 個鍵，核對字面相等、數值格式等價、顯示值差異、僅有 V2 或 V3 顯示與兩側缺值。可重播收據只保留歷史顯示值的指紋、來源定位線索和比較分類。舊顯示不能提供 V3 科學或展示值；只有重新核對現有合格版次才能採納。

來源依固定原件回讀、官署或交易所原始資料、特定版次尋回與科學衝突拒收安排優先序。清冊保留候選原文定位、待補的用途／Scope／時間證據與同版歷史線索。來源缺版、已有原件而尚缺欄位索引、只有原文引句及科研衝突各有獨立狀態。142 筆原件出現層級的待綁定與 15 個無完整結構的工作項沿用 Stage 3 定位與處置；不計作原始觀測。每份原件的增益上限為可查核的獨立 Project／斷言目標。例如同一 DFS 的三個投影欄仍只對應一個斷言。尚未查核的上限不作本輪新增值。

## 重播

先在固定 main 使用 `au_ree_census_evidence_v1.json`、`resource_bindings_v4.json` 和固定資料清單執行 `research build`。兩次 `research replay --offline` 分別讀取 `work/census-stage3/final/cas` 和 `work/census-stage3/final/replica-cas`，均指定歷史候選 ID 及獨立空索引，再執行下列指令。[Stage 3 候選重播](census_stage3_results.md)

```sh
SOURCE_WORKSPACE=/workspace/AU_REE_V3
BASELINE="$SOURCE_WORKSPACE/work/census-stage4"
PYTHONPATH=src python -m tools.census_display_coverage \
  --workspace "$SOURCE_WORKSPACE" --v2-root /workspace/AU_REE_V2_reference \
  --current-main "$BASELINE/current-main" --replays "$BASELINE/cold-a" "$BASELINE/cold-b" \
  --out "$BASELINE/coverage-rebuilt"
```

核對 `coverage_baseline_receipt.json`、`core51_coverage_by_role.csv`、`project_display_reach.csv`、`source_attribute_work_queue.jsonl`、逐格 `core51_v2_v3_cell_comparison.csv` 和逐來源型別 `source_evidence_review_bounds.csv`。兩次清冊生成的六份產物內容一致。清冊重用已保存的原件解析收據，未重新解碼 PDF。新增本工具、測試及兩份文件；既有權威資料、程式模組和封存保留。
