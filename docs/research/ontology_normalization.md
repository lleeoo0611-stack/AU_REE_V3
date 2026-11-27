# 科學語義、正規化與 T1 影子研究

研究截日為 **2026-11-28（Asia/Taipei）**。固定基準提交是 `ac86ab43ec4671e177ce1c71556f88e1425fb633`，前一不可變 V3 研究候選為 `24bb0056a0655757a14fd11244fcee207e8a9b88f3d576f7e86c6341b97b3974`。本輪使用同一份取得清冊，SHA-256 為 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`。93 案有 144 個原件 SHA 及 148 筆合格取得紀錄。歷史取得事件另有 37 次 MIME 不符及 1 次 HTTP 404，由清冊分別保留。本輪固定來源缺失和讀回 SHA 失敗均為 0。

## 科學結果

| 正式資料 | 基準 | 本輪 | 差異 |
| --- | ---: | ---: | ---: |
| 候選／已驗證／正式選值 | 38／23／17 | 38／23／17 | 0／0／0 |
| Census 正式科學格／總格 | 12／4,743 | 12／4,743 | 0 |
| R1／S1 Project × Criterion | 4／2 | 4／2 | 0／0 |
| 完整 R1＋S1 cohort 案數 | 2 | 2 | 0 |
| 其他完整 cohort | 0 | 0 | 0 |
| 凍結面板分數、排名及敏感度差異 | — | 0 | 0 |

逐格比較覆蓋 4,743 格。臨時重建的 12 個正式格僅有 `release_id`／`selection_id` 變化，科學值、來源 SHA、觀測、證據及評分相同。38 筆正式流程觀測 JSONL 逐位元組一致，SHA-256 為 `0c5958adcb70c2798f72aa5ed56fcfe2ee05f7a2d672bfc614b3fb14077a5ce3`。

`ontology-use-contract-v1` 共用既有 Source、Temporal、觀測及 Store 邊界。用途判定跟隨同一個原件、觀測及錨點。精確單位及化學計量保留 `Decimal` 來源 token、有效位數或顯示區間、材料分母、原子量資料版本和公式證據。R1 新衍生值留在影子研究，S1 原有的元素或氧化物成員證據依現行選值規則保留。[語義契約](semantic_use_contract.md)

| 消融 | 固定來源上的新增工作 | 正式可比較鍵 |
| --- | --- | ---: |
| A · 現行規則 | 38 筆既有觀測，逐鍵重算 `criterion-comparability-v1` | 6 |
| B · 原件重讀 | 38 個既有錨點可定位。定位 Barkly 估算生效日 2025-11-17、NdPr cutoff 與乾全礦分母 | 6 |
| C · 精確正規化 | 17 個既有選值中 15 個取得正規化收據，2 個 Goschen 化學成員上下文不足。Barkly 的 3 個原件量另可換算，保留來源化學及估算類別 | 6 |
| D · 語義用途 | 六個既有鍵的影子用途評估為 3 個直接、2 個衍生證據、1 個上下文不足。七個跨案資源／組成影子配對皆不相容 | 6 |
| E · 技術、開發、地理 | 兩組 T1 都無跨案可比群組。D1 保留 3 筆日期合格里程碑，GIS 保留 7 筆合法圖徵，均為影子 | 6 |

Wudinna 原表 470 ppm 在同一材料及化學基準下換算為 0.0470%。Wudinna、Wolverine、Mt Weld 的同 Scope 影子 contained-TREO 分別為 70.5000、70.080、4,391.920 kt，三個推導區間皆與原件的直接報告顯示區間相交。Goschen 的 `In-Situ TREO + Y2O3` 成員及重複加總條件未能證明。Barkly 同文件 40 Mt、2,100 ppm、82,000 t 的數字可各自正規化，但 NdPr cutoff、分母映射和正式 Science/Selection 證據限制其 MCDA 用途。各組資料保留逐筆用途、拒絕理由與原件位置。

獨立 R1 留出固定四個來源 SHA、四個專案以及各自報告家族。9/9 個人工預先標註數值 token 命中。逐觀測正規化資格的 TP/TN/FP/FN 為 3/6/0/0，R1 影子用途為 1/3/0/0。North Stanmore 的 `278.802 Mt × 482.64 ppm` 約得 134.560997280 kt，原件沒有對應直接含量。Koppamurra、Nolans 缺同估算版本的生效日，Luni 的 Nb₂O₅ 測量目標不同。直接報告 R1 的留出結果為 0/4/0/0，沒有正例。直接用途中整體 precision／recall、S1 留出及自主抽取 precision／recall 為 `UNMEASURED`。足量且跨公司可比較的有效正例仍限制升格。

T1-v2 使用 `technical-processing-performance-v2`。開發與留出共錨定 17 筆觀測，T1a–d 各保留觀測種類和流程條件。15 筆開發觀測中 6 筆量測和時間已驗證、8 筆證據不足、1 筆有物料平衡衝突。留出兩筆各有原件錨點及時間資格。兩筆獨立原件中，一筆為實測、一筆為工程規劃，角色判定 TP/TN/FP/FN 為 1/1/0/0。跨案用途配對為 0/1/0/0。合成 Oracle 另測試明示同群正例和流程終點反例。可比較 T1v2 cohort 為 0，沒有可執行的 T1 權重敏感度。舊 T1 揭露規則仍在正式政策，93 案的 v1 正式入值皆為未知。[T1 影子結果](technical_t1_v2_pilot.md)

D1 的里程碑只映射各自的項目／階段範圍。GIS 保留官方來源核對的 CRS、精度和法定權屬角色，正式 I1–I4 皆缺合格位置或足夠的基礎設施證據。候選全文索引核對 144 個 SHA，索引 142 個可索引原件及 57,521 個有位置的片段，另兩份 ZIP 由地理流程處理。全語料查詢找回固定留出 2/5 個錨點。人工定向 locator 另外找回 Nolans 2/2 與 Luni 的 Nb₂O₅ 表格，檢索整體 precision／recall 仍 `UNMEASURED`。[D1、GIS 與檢索收據](geography_and_candidate_retrieval.md)

## 驗收

| 驗收 | 實際狀態 | 範圍 |
| --- | --- | --- |
| O0 | PASS | 語義契約、來源與觀測可追溯，正式資料逐筆等價 |
| O1 | PASS | 固定原件上的正規化、影子衍生與獨立正反例 |
| O2 | FAIL | 同來源消融已完跑。正式增益 0，新目標為 4 |
| O3 | PASS | 原件與獨立 Oracle 的 T1 影子試驗。正式 T1v2 為 NOT_RUN，研究 cohort 不足 |
| O4 | PASS | 固定來源、規則、提交的影子重建及凍結原件重播 |
| F0／F1／F2／F3 | PASS | 同輸入正式重建的登錄、來源、觀測與資料投影 |
| F4 | PASS | 既有 V3 候選離線復原，核對固定八個產物 |
| F5 | BLOCKED | 尚缺經授權之獨立故障域的讀回、身分與離線復原驗證 |

既有正式研究候選維持原 ID。其冷建耗時 7.22 秒，八個產物 SHA 全部一致。兩次影子冷建重現 23 個產物，349 個封存物件可依 SHA 讀回。影子版 ID 為 `9adf50117ea5fbee51f9146d52777f979f7b664a03ac8388d6603da94ed40620`。第三次只用封存 CAS 及 Shadow ID 還原，23 個指紋一致。完整本機測試執行 223 項，全部通過。兩次影子冷建及第三次封存還原的運算收據包含環境與耗時。[研究收據](ontology_gain_receipt.json) [本輪檔案生命週期](ontology_file_lifecycle.csv)

## 工程與後續證據

同一份 144-SHA 正式建置三次中位為 1.776 → 1.882 秒，Python 峰值中位為 55,468 → 56,088 KiB。執行時跨模組邊為 43 → 43，靜態 import 邊為 56 → 59。AST 分支分數上限／第 90 百分位維持 79／20，正式路徑的完全重複函式體群仍為 0。小樣本及未控制的 OS 頁面快取限制效能推論。

資源消融的三次耗時為 2.305／2.311／2.176 秒，Python 峰值約 59,492 KiB，六個科學 JSON 指紋一致。T1 影子中位 3.095 秒、81,644 KiB。這兩項新增工作負荷的歷史等功能成本為 `UNMEASURED`。

本輪功能 PR 的 GitHub Static and unit 為 10–19 秒，Exact resource integration 為 41–70 秒，結果均為成功。真實整合工作包含不同原件和步驟，歷史等工作量 CI 成本為 `UNMEASURED`。[工程量測與可重現命令](ontology_engineering.md)

`main` push 執行 Static and unit。PR 額外執行 Exact resource integration，包含 exact Wolverine PDF 錨點。完整 144 SHA、同版捕獲證據、獨立留出和冷重播在保留的工作區原件執行。GitHub 變更時應將兩個 PR job 保持為必要檢查。runner 計費資料未量測。現行 V3 面板沿用主線 MCDA 核心，PR #4 的其他歷史功能可作唯讀診斷。

下一輪證據缺口是同化學集合、同用途與同 cutoff 的 R1 正例，T1 同實驗端點和分母的跨公司原報告，具原生 ID 的官方 GIS 礦床點及有效基礎設施快照，以及真正獨立的備份復原證據。

## 以封存重播

在已具備相同 Python、SQLite 和本機文件解析版本的環境中，使用輸出收據的 Shadow ID。重播只讀取封存 CAS，另建恢復目錄，並逐件比對科學 SHA。初次冷建另外核對前一個正式候選的八個產物。

```bash
PYTHONPATH=src python tools/verify_semantic_release.py replay \
  --shadow-store work/ontology-v1/release-verification/shadow-store \
  --shadow-release-id "$SHADOW_ID" --out work/ontology-v1/replay-from-shadow
```
