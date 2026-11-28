# Census 第二階段基準

從 `074e00daf69360cd71cab2554f651b4b6e76a668` 以固定來源實際重建。另一程序以封存程式 `d114c388440ad7db9b1b3de25d3e7646d4a84953` 離線重播研究候選 `de50379f50e49fdd74ac536deb51b8b03dade8f442565644564c5cef1f2a17ef`，並讀回八件科學輸出。研究截日是 2026-11-28（Asia/Taipei）。

| 核驗項目 | 實測值 |
|---|---:|
| Project × Census 欄 | 93 × 51 |
| 原件 SHA／合格擷取事件 | 144／148 |
| 有值格／原件報告科學格／正式衍生格 | 1,108／16／0 |
| 候選觀測／驗證／正式選值 | 42／27／21 |
| 可比較準則鍵／R1＋S1 完整面板 | 6／2 |
| 與封存完全相同的格／只有發布證據識別更新 | 4,723／20 |

Census CSV 和原始觀測 JSONL 逐位元組相同。現行重建的發布識別不同，故個別選值及收據指紋不同。逐格值、來源、觀測、估算日及作用範圍核對一致。MCDA 輸入、排名、權重與敏感度的科學內容一致。F0–F4 在上述範圍通過。既有 F5 維持 `BLOCKED`，歷史 O2 為 `FAIL`。

## 逐格漏斗與範圍

4,743 筆漏斗逐格保存正式原件報告值、描述性證據與缺值。顯示、登錄與來源描述性證據為 1,092 格。另有 7 個影子值及 5 個公式衝突連至既有空白欄格。原 Census 空白和狀態不受輔助標籤改變。另有 28 格尚未定位及 3,595 格證據不足。`NO_SELECTED_MRE` 保持 `UNKNOWN`，真正不適用為 0。來源統計依原始 Observation ID 去重，並另記每來源的投影格及每 Project 的缺值。

上輪定位的 298 個唯一數字位置保留 103 個已釐清位置及 225 筆未綁定位置。225 筆包含缺表頭或單位 210、對位歧義 13、原生格不唯一 2。19 個 Source × Project 項尚無辨識結構。此結論經核對上輪清冊、綁定收據及各檔 SHA，沒有再次批次回讀原件，也不當作本輪新增觀測。

兩組既有留出標註按 PDF、發行人與 Project family 凍結，3 與 4 個原件家族且部分重疊。工作 A 保存先前召回 1/10、1/31 和現行凍結標註指紋，未執行新的綁定評估。新評估使用相同標註且分組計算。候選綁定與正式用途分別驗證。

## 重現

在此分支的程式工具中，以工作環境固定的原件 CAS 和收據路徑重生輸出。先按 `scientific_value_admission.md` 的既有命令，分別從 `074e00d` 建立 v4 Research Build，以及針對上述封存 ID 執行離線冷重播。以下命令使用兩個獨立輸出作為可檢查輸入。

```bash
ROOT=/workspace/AU_REE_V3
PYTHONPATH=src python tools/census_stage2_baseline.py \
  --workspace "$ROOT" \
  --main-build "$ROOT/work/census-stage2/baseline-main-074e00d" \
  --archived-cold-replay "$ROOT/work/census-stage2/archive-cold-a" \
  --out "$ROOT/work/census-stage2/stage2-baseline-new"
```

- `stage2_baseline.json` SHA-256 `fa1c4d1387931ee17cc86df7d454568301b01f901431084bc0fad20accf875c6`。
- `census_gain_funnel.csv` SHA-256 `39cec1b97b4644e096e8761da77ab6e2f38d420aaa6c79da38dba9b00388e626`。
- `frozen_holdouts/` 保存兩份位元組相同的標註副本。收據包含各別標註與家族的 SHA-256。

本單元新增讀回與驗證工具、基準說明及測試。生產選值、投影規則與既有封存沿用原版。
