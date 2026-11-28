# 固定樣本與擴大樣本的科學差異

工作包 C 沿用既有 93 案、51 欄、現行 R1／S1 准入及正式 MCDA 政策。變更範圍為差異收據及驗證，風險為中。既有 SQLite 寫入流程與正式評分器持續提供正式結果。

## 基準

凍結候選為 `bdb9e868e5cb2a514e608231db35cdb27c3e77b2fc9427f7dbb444409f511c78`，研究程式為 `33587f23eda8243ad7713f2a5524f93ac9b2ce65`。基準起始合併提交為 `c8bce8a893358a63bf2c587e44e0bc3f84523db7`。原件清冊 SHA-256 為 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`，正式 MCDA 政策 SHA-256 為 `b8dd516b6f6a765f3a656630d91315061ff0a27bf2c57bef41c92a2dc80abf19`。

| Project | R1，kt TREO | S1，% |
| --- | ---: | ---: |
| Wudinna | 70.400 | 23.829787234… |
| Wolverine | 70.500 | 13.541666667… |
| Goschen Area 1 | 74.000 | UNKNOWN |
| Mt Weld | 4389 | UNKNOWN |

基準獨立核對 4,743 格、837 個 Project×criterion 鍵及三個權重情境。Wudinna 在兩案合格樣本的三種方法及三個權重情境皆位居第一。其他四個面板為 `INSUFFICIENT_COHORT`。原有兩次冷重播各驗證八件科學產物。另以既有獨立 verifier 重讀 Stage 4 至 Stage 5 的正式新增值及九欄展示證據。

## 比較方式

- `research compare` 枚舉原 4,743 個鍵。逐格保存舊／新值、狀態、證據及版次欄位。可比較收據保留新增、修改、退回及未知原因變動。
- 各面板保存封存矩陣、candidate 在原完整樣本的矩陣、candidate 的完整合格矩陣。固定樣本保留原成員，若任一成員缺少現行合格準則，固定比較回傳 `BLOCKED` 並保留缺失 Project ID。未達最小樣本門檻的面板保留 `INSUFFICIENT_COHORT`。
- 同樣本效果呈現原有 Project 的 utility、分數、名次及敏感度差異。母體效果以 candidate 的固定矩陣對比 candidate 的完整矩陣。SAW 與固定理想 TOPSIS 的個案分數由自身 utility 及權重決定，名次會受新樣本影響。PROMETHEE II 的淨流量使用全部樣本的成對比較，擴大樣本後分數亦可改變。
- 獨立程式以 50 位 Decimal 重算 utility、權重、SAW、TOPSIS、PROMETHEE II 及逐項逐構面移除敏感度，並查核回報的效果屬於正確樣本。指定絕對分數誤差上限為 `5e-14`。合成三案用於驗證母體效應及防止漏報，與真實案例分別記錄。
- 獨立程式沿用科學數值 verifier 的 SQLite 唯讀 integrity／foreign-key、正式採納查詢與同版原件讀回，分別辨識候選、已驗證的獨立 Observation ID、正式選值鍵、公式及比較鍵。基準自我比對的增益假說為 `NOT_RUN`。實際研究候選會分別檢驗 R1／S1 淨新增一鍵和完整面板達三案兩項假說。

## 重播

```bash
PYTHONPATH=src python -m au_ree_v3 research compare \
  --baseline "$baseline" --current "$candidate" --out "$delta"

PYTHONPATH=src python tools/verify_panel_cohorts.py \
  --baseline "$baseline" --candidate "$candidate" --delta "$delta" \
  --policy src/au_ree_v3/fresh_decision_policy_v1.json \
  --sources "$candidate_source_manifest" --cas "$candidate_cas" \
  --out "$panel_oracle_receipt"

PYTHONPATH=src python -m unittest discover -s tests -p test_research_delta.py -v
```

原件讀回使用 candidate 的確切來源與取得收據，兩個來源母體的 SHA 保留在收據。可加上封存版及離線重播參數驗證歷史候選。工作區基準收據位於 `work/census-stage6/comparison-baseline/`。

## 擴大來源的實測

工作區擴大清冊試建置含 147 個唯一來源 SHA、152 筆合格擷取。Nolans Bore 的 2017-06-07 同一聲明在物理第 2 頁明示 56 Mt、2.6% TREO、1% TREO cutoff。獨立 verifier 重讀兩個原件 token 和正式採納，依現行 Census 公式得到 1,456 kt TREO，原報精度傳遞區間為 1,415.25 至 1,497.25 kt。既有獨立公式核對延伸支援百分比輸入與原生 Observation ID，並驗算兩端精度界限。

試建置新增兩個正式 Observation／選值鍵及三個核心科學格。另有六格來源清冊展示欄更新，分別是 Nolans 與 Dubbo 的來源名稱、URL、摘要。Nolans 的直接含量依據缺失，R1 仍為 `INCOMPLETE_SELECTED_RESOURCE_BUNDLE`，S1 仍為 `SAME_STATEMENT_RESOURCE_REQUIRED`。六個可比較鍵重新綁定版次識別碼，科學值、原件血緣及兩案樣本維持原結果。固定及完整矩陣的權重、分數、名次與敏感度一致。兩項研究假說均為 `HYPOTHESIS_NOT_SUPPORTED`。擴大清冊試建置收據位於 `work/census-stage6/comparison-expanded-pilot/`。

舊／新比較器對相同凍結輸入，各交錯執行三次。全量比較 wall 中位數為 1.333／1.034 秒，CPU 中位數為 1.332／1.027 秒，峰值 RSS 中位數為 95,944／95,960 KiB。比較模組 AST 最大分支分數均為 68，重用評分流程後，面板健全性檢查由 25 降為 17。量測範圍是 4,743 格比較及收據輸出。擴大來源採納及候選建置另行量測。

## 檔案清冊

| 檔案 | 處置 |
| --- | --- |
| `src/au_ree_v3/research_delta.py` | REFACTOR。重用既有評分函式，輸出同樣本輸入效果與擴大樣本效果。增加權重對照。 |
| `tools/verify_panel_cohorts.py` | 新增。獨立 Decimal 算法、全格證據核對及採納差異 Oracle。 |
| `tools/verify_scientific_yield.py` | REFACTOR。沿用核准公式版本，獨立驗算 ppm／百分比與原報精度界限。 |
| `tests/test_research_delta.py` | REFACTOR。覆蓋三案母體效應、權重變更、退回及漏報拒收。 |
| `docs/research/census_comparable_cohorts.md` | 新增。記錄方法、基準及重播命令。 |
