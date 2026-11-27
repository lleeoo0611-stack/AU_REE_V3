# Census 固定基準與 V2 方法診斷

CW0 在主線提交 `40309ea006d2eaace3fe4bbf33beccca492261de` 重新建置通過。固定截日為 2026-11-28（Asia/Taipei）。148 筆來源取得事件涵蓋 144 個可讀且時間合格的唯一 SHA。原始 Census 93 案 × 51 欄共有 4,743 格，其中 105 格有值。93 格是 Project ID，12 格是正式科學值。候選、驗證與正式選值分別為 38、23、17 筆。R1 與 S1 合計 6 個可比較鍵，組成面板有 2 案。

不可變候選 `24bb0056a0655757a14fd11244fcee207e8a9b88f3d576f7e86c6341b97b3974` 另在本輪由原件離線冷建，8 個輸出 SHA 均相符。候選與 CW0 主線重建的正式數值、逐格狀態及科學來源欄、準則、權重、比較母體、排名與敏感度一致。歷史 O2 仍為 `FAIL`，F5 仍為 `BLOCKED`。[CW0 收據](cw0_baseline_verification.json)

## V2 封存覆蓋與方法

唯讀檢查 `2af6f611e51eb4ac9dda73419d1afeba70d1307e` 的 Census、狀態與來源清冊，逐格核對 4,743 個鍵與保存值。封存科學版本是 `0dff6beb5aca52e565c37289e3ab295737e76942`。2,500 格有值、1,715 格歷史不適用、528 格適用但缺值。兩個舊覆蓋口徑分別為 2,500／3,034（82.40%）及 2,500／3,028（82.56%）。

V2 有值格依欄位角色分組為身分、行政及公司 827、來源與說明 372、地質與里程碑 429、位置 128、預測及產品 47、資源與組成 697。這些計數來自欄位所屬投影群組。資源與組成群組同時涵蓋報告觀測、公式衍生值與完整性指標。研究量值仍須依 V3 的資料層、來源及用途核驗。

與 CW0 的總表相比，2,500 格中有 105 格顯示值相符，其餘 2,395 格缺正式可用的 V3 值。相符含 93 個身分及 12 個數字顯示值。同一數值的來源、時間或語境可能不同，診斷不判定新舊研究科學等價。1,650 格舊 `NOT_APPLICABLE_NO_MRE` 全數對應 CW0 的 `UNKNOWN`。逐格報告保留原值及新值指紋、來源交集、方法與缺值原因。在固定來源清冊內，1,859 格可找到相同 Project ID 及原來源 SHA，455 格所引用的版本不在本輪清冊，186 格沒有原件 SHA。版本可對上仍需通過欄位用途及科學驗證。1,432 格引用 847 個不同歷史 Observation ID，693 格引用 417 個不同 Attribute ID。這些計數是歷史清冊的連結數，無法換算成獨立的實測點。[V2 診斷總表](cw0_census_historical_diagnostic.json)

## V3 metadata 投影後的差異

提交 `d185b401741f7650e8cca496047e5309937de529` 的 4,743 格有 1,100 格有值，其中 93 格是 Project ID、995 格是本輪新增 metadata，12 格是原有正式科學值。其餘 3,643 格為 `UNKNOWN`，無 `NOT_APPLICABLE`。新增 metadata 包括 363 格登錄快照、279 格行政映射、260 格來源憑證及 93 格有界展示摘要。

V2 的 2,500 格有 698 格顯示相符，402 格有 V3 的獨立條目依據而改變，1,400 格缺可採用的 V3 值。402 格包含 186 格中英文行政區顯示、93 格展示摘要、49 格 Project-level 網址及 74 格來源名稱。1,650 格舊 `NOT_APPLICABLE_NO_MRE` 仍為 `UNKNOWN`。12 個既有科學格的來源、定位、時間、範圍及原始單位與值皆相同，12 個識別碼已依新 Release 與原 Validation ID 重新計算核對。38 筆既有觀測、6 個既有可比較準則的全部證據欄、5 個面板的輸入與所有排名及敏感度均相同。[A 的逐格診斷收據](census_metadata_historical_diagnostic.json)

## V3 科學投影後的差異

提交 `b6094405ba5b88d5b2235850aa937721a8f31fb0` 的兩次獨立固定輸入建置產生完全相同的診斷收據。相較 metadata 投影，僅增加 4 格 `有TREO資源估計`，使有值格達 1,104。這四格每格連至 3 筆同 Project、來源 SHA、Statement、Scope 與有效日的已選觀測，且各自具明確原件定位及合格時間證據。欄位用途屬 `EVIDENCE_REFERENCE`，不進入 MCDA。原有 12 格正式數值、38 個觀測紀錄、6 個可比較鍵及面板結果維持相同，正式衍生值增加 0 格。

V2 歷史有值格在提交 B 上有 702 格顯示相符、402 格更新且有依據、1,396 格缺可採用的 V3 值。其他三類為 0。獨立的更正候選 `a0fcf28` 重跑保持相同的逐格顯示、適用性、正式科學及 MCDA 診斷。本輪 3,639 格 `UNKNOWN` 中仍包含歷史 1,650 格 `NOT_APPLICABLE_NO_MRE`，0 格列為 `NOT_APPLICABLE`。[B 的逐格診斷收據](census_science_historical_diagnostic.json)

## 100 條規則的處置

`integrated-summary-v1` 清冊有 100 條參數。逐條保存來源檔與鍵、SHA、靜態程式引用、相關欄位的實際歷史填值數、證據要求、風險及工程處置。7 條為 KEEP，43 條為 MERGE，36 條為 REFACTOR，12 條為 ARCHIVE，2 條自 V3 正式路徑 REMOVE。移出的兩條是容易將具名或進行中研究推成已完成里程碑的個案捷徑。兩條規則在舊來源清冊指向 30 格開發階段代碼或類別，其中 22 格把具名或進行中研究作為分類依據。兩條規則指向同一批格，合計仍為 30 格。原歷史紀錄保留。

現行工程參照的 `summary_rules.json` SHA-256 為 `1d8d7a4a83657eaf81550886423f997313fcf5fb2139608d7607d4b02d9cf753`。舊發布收據固定為 `bddde0aebe2b80dd58c7e4345b07bc20da8dd1aff34679cf59dd77abff28211e`。清冊同時列出 `summary_resource_fields`、`summary_template_fills`、`summary_stage_fields`、`summary_locations`、`summary_forecasts` 等固定實作的行號。11 條規則可由歷史方法或個案 ID 歸屬具體格數，包括州別映射 279 格、來源限定的模板 37 格、摘要 93 格。制度、參數與來源同時變動時，單條歷史規則的因果貢獻仍為 `UNMEASURED`。[逐規則清冊](census_v2_rule_inventory.json)

使用者後續提供的 ZIP 已用於網站互動參照。投影診斷使用固定 V2 與 V3 歷史清冊及本輪實際產物。

## 重跑診斷

`tools/census_web_baseline.py` 使用本輪重建及冷重播產物，檢查不可變候選與 CW0。`tools/census_v2_coverage_diagnostic.py` 使用既有 checkout 的固定 V2 Git tree，並依新建置收據 CI 前後重查 2,500 格。指定同一建置的來源清冊時才計算原件 SHA 交集。以下指令在 V3 根目錄執行，輸出目錄須是新目錄。

```bash
python tools/census_web_baseline.py \
  --repository . --current work/census-web/cw0/build \
  --replay work/census-web/cw0/cold-baseline \
  --out work/census-web/cw0-recheck.json

python tools/census_v2_coverage_diagnostic.py \
  --v2-root ../AU_REE_V2_reference \
  --v3-build work/census-web/b-corrected/build \
  --v3-previous work/census-web/a-pinned-build \
  --v3-baseline work/census-web/cw0/build \
  --sources work/research-yield/source-inputs/source_manifest.json \
  --out work/census-web/census-diagnostic-recheck
```

診斷器已用改動 Anchor ID 與 Release-derived Selection ID 的複本做反例。反例重新計算外層建置 SHA，前者報告所改欄位並回傳失敗，後者拒絕無效選值識別碼。正式建置與 V2 封存樹皆未異動。
