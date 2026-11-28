# Census R1／S1 科學證據缺口

以合併主線 `c8bce8a893358a63bf2c587e44e0bc3f84523db7` 開工。核對 Stage 5 候選 `bdb9e868e5cb2a514e608231db35cdb27c3e77b2fc9427f7dbb444409f511c78` 的研究程式 `33587f23eda8243ad7713f2a5524f93ac9b2ce65`、研究時區與截日、144 份原件、148 筆合格擷取、決策政策及九欄展示封存收據。兩份本機 CAS 各重新讀回 314 個封存物件，冷重播各比對八項科學產物。SQLite 完整性與外鍵檢查通過。S6-0 通過，F0–F4 通過，F5 因共用本機故障域維持 `BLOCKED`，歷史 O2 保留 `FAIL`。[機器收據](census_criterion_evidence_receipt.json)

93×2 共 186 個準則鍵逐一重算。R1 仍有四鍵，S1 仍有兩鍵，兩案完整 R1＋S1 面板，六構面完整樣本為零。89 個 R1 缺完整同一聲明的正式資源組，對應 89 個 S1 先決條件未達。Goschen Area 1 及 Mt Weld 已有 R1，缺 S1 組成選值。兩案為優先對象，其次是不同發行人 Hastings 的 Yangibana R1。現有 53 筆候選中 37 筆已驗證、31 筆正式選值，按既有用途過濾後有 27 筆可供 MCDA 評估。可供評估的觀測須另通過逐準則用途檢查。

六個既有比較鍵的數值、原報來源、範圍、時點、選值、完整語境與衍生路徑均與凍結資料一致。Stage 5 SQLite 中沒有持久化的 Semantic Assessment，本次另在唯讀審計計算六筆語意診斷，得到 3 筆直接可比較、2 筆衍生可比較、1 筆語境不足。Goschen 的診斷缺明示 TREO 成員，既有治理政策的 R1 仍為 74.000 kt。兩種結果分開保留，未回寫選值或變動原決策政策。

| 優先 | 準則 | 精確缺口及拒收條件 |
| --- | --- | --- |
| 1 | Goschen Area 1 S1 | 2025 原件 p12 的原礦總量及 TREO+Y₂O₃ 不能證成四物種的全礦分母。原表 `M+Ind+Inf` 與前文 `Measured and Indicated` 分類不同。2026 公告 p23 的礦砂組成與跨區合計含不同物料與 cutoff。 |
| 2 | Mt Weld S1 | 2.5% TREO Resource 的四氧化物全礦組成未在已驗證的 p2／p9 原表建立。p23 Table 7 的 Pr／Nd／Dy／Tb 是 2.8% TREO Ore Reserve，需保持儲量用途。 |
| 3 | Yangibana R1 | Hastings 2023-10 Table 2 的 100% Project 估算報 29.93 Mt、0.93% 及 276,955 t。原件 p15 說明 0.24% TREO cutoff。含量落入原報精度區間，仍需確證同一估算的完整 TREO 成員與全礦材料分母。 |
| 4–5 | Wudinna、Wolverine S1 回歸 | Wudinna 原件 p3 與 p15 影像版次核對後，112／470 得 23.8297872340…%。Wolverine 現有路徑由四個已選 ppm 原值除 9,600 ppm 得 13.5416666667…%。 |

Wolverine Table 2 與 Table 5 的 Dy₂O₃ 名義原報區間分別為 [845, 855] 與 [839.5, 840.5] ppm，Y₂O₃ 為 [5,675, 5,685] 與 [5,699.5, 5,700.5] ppm。兩組各自不重疊。保留已封存 S1 的明示原表路徑，新增用途需同一聲明的獨立原表或發行方勘誤。重讀 6 份精確 PDF、原文核對 26 處、解碼範圍 82 頁。既有公開勘誤尋回未執行。本單元新增獨立觀測、正式值、比較鍵及完整樣本皆為零。[原文審查](criterion_original_evidence_v1.json)

## 重現

以 `research replay --offline` 及相同候選 ID 各從 `work/census-stage5/final/cas` 與 `replica-cas` 重播。然後執行：

```sh
WORKSPACE=/workspace/AU_REE_V3
A="$WORKSPACE/work/census-stage6/baseline"
PYTHONHASHSEED=112 PYTHONPATH=src python -m tools.census_criterion_evidence \
  --workspace "$WORKSPACE" --replays "$A/cold-a" "$A/cold-b" --out "$A/ledger-check"
AU_REE_STAGE6_WORKSPACE="$WORKSPACE" PYTHONPATH=src \
  python -m unittest discover -s tests -p test_census_criterion_evidence.py -v
```

產物為 `criterion_evidence_gap_ledger.jsonl` 與 `criterion_evidence_gap_receipt.json`。兩次清冊程序的 hash seed 為 112、641，兩件產物逐位元相同。冷重播現場壁鐘為 14.767／14.848 秒，屬併行新程序資料。RSS、計費及整體依賴變化未在本工作量測。此次新增獨立唯讀工具、測試、原文審查及兩份文件，正式程式與歷史檔案保留。
