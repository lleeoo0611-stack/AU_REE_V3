# 本體論輪次工程驗證

固定參照 `ac86ab43ec4671e177ce1c71556f88e1425fb633` 與整合程式 `9ef642e34d42613cacff1348b3c71c51105c840d`，共同使用 144 個可讀且時間合格的來源 SHA、相同研究 profile、資源綁定與既有正式 MCDA 政策。由 Git 依提交匯出程式與設定。兩版各重跑三次取得耗時與 RSS，另做一次 cProfile 追蹤。每次均使用全新 Python 程序、SQLite 與 DocumentCache。OS 頁面快取未控制，執行交替順序一致。

## 同功能比較

| 正式研究建置 | 基準 | 整合程式 |
| --- | ---: | ---: |
| 中位執行時間 | 1.776 s | 1.882 s |
| Python 中位峰值 RSS | 55,468 KiB | 56,088 KiB |
| 實際到達模組 | 21 | 22 |
| 執行時跨模組呼叫邊 | 43 | 43 |
| 到達模組間靜態 import 邊 | 56 | 59 |
| 實際呼叫且有 AST 定義的函式 | 159 | 161 |
| 同定義分支複雜度上限／第 90 百分位 | 79／20 | 79／20 |
| 跨模組完全相同的已呼叫 AST 函式體群 | 0 | 0 |

各次逐格 Census 值、狀態及科學證據欄一致。候選／驗證／選值為 38／23／17，正式科學值 12 格，可比較準則鍵 6 個。完整案例矩陣、權重、分數、名次和敏感度完全一致。比較時排除新執行建立的 release 和 selection 身分，保留來源 SHA、Observation ID、時間判定與分析欄位。小樣本耗時波動須保留解讀限制。相同工作量的 CI 及費用為 `UNMEASURED`。[建置實測](engineering_comparison.json)

T1 影子管線另以相同整合提交實跑三次及一次剖析。每次核驗 144 個原件、定位 15 筆候選，0 個可比較配對與 0 個完成排名的面板，輸出雜湊一致。中位 3.095 秒、峰值 RSS 81,644 KiB。形式上有 11 個到達模組、14 條動態邊、16 條靜態邊及 108 個到達 AST 函式。歷史等功能基準為 `UNMEASURED`。[T1 影子工程收據](technical_engineering.json)

歷史重播、現行研究建置、DocumentCache、用途中介與正規化各具獨立責任。正式準則運算與影子用途審查保留原來的對照界線。靜態檔案審計查到一組重複的 canonical SHA 包裝函式。此結果只涵蓋完全相同的 AST 函式體。[檔案責任和相依清冊](responsibility_review.json)

## 重現

在同一 V3 repo 中保留指定提交，並提供上一輪封存的 manifest 和 CAS。以下從 repo 根目錄執行，參數中的輸出資料夾使用新名稱。

```bash
python tools/engineering_benchmark.py --base-repo . --current-repo . \
  --current-ref 9ef642e34d42613cacff1348b3c71c51105c840d --out work/engineering/formal
python tools/technical_benchmark.py --base-repo . --current-repo . \
  --current-ref 9ef642e34d42613cacff1348b3c71c51105c840d --out work/engineering/technical
PYTHONPATH=src python tools/ontology_responsibility.py \
  --baseline-code work/engineering/formal/baseline-code --current-code work/engineering/formal/current-code \
  --out work/engineering/responsibility_review.json
```
