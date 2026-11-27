# T1v2 選冶技術影子試驗

`technical-processing-performance-v2` 將來源中的製程量測分成 T1a 回收／萃取／產率、T1b 產品品質、T1c 製程強度與 T1d 證據適用性。正式 `technical-disclosure-transparency-v1` 保留既有七項揭露規則。前一版冷建 Decision 輸出中，93 個專案的 T1v1 都是 `UNKNOWN`，原因為尚無正式選值 adapter。兩個版本衡量不同構面，現有資料無法估算同一目標的版本間精確率或召回率。

使用 2026-11-28 Asia/Taipei 研究截止日及原始取得清冊 SHA `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`。每次執行重新讀回 144 個 SHA，將人工核對的量測、單位及背景片段逐一綁到同版原件的實際 PDF 頁。每筆輸出有原文、頁碼、表列欄位、原字串和單位、試驗範圍、進料、產物、分母、程序、端點、尺度、化學集合、時間決策和用途評估。未確定的條件保留缺值。正式原件清冊及研究資料庫維持唯讀，影子資料庫另存。

| 實測 | 開發回歸 | 獨立留出 |
| --- | ---: | ---: |
| 原件 SHA 讀回 | 144 / 144 | 144 / 144 |
| 錨定觀測／公司或報告家族 | 15 / 8 | 2 / 2 |
| T1a／T1b／T1c／T1d 觀測 | 10／2／2／1 | 2／0／0／0 |
| 量測與時間驗證通過 | 6 | 2 |
| 時間證據未達驗證 | 8 | 0 |
| 科學衝突 | 1 | 0 |
| 語義配對 | 47 | 1 |
| 可用獨立專案配對 | 0 | 0 |
| 經覆核的配對真負例／誤判可比 | 5 / 0 | 1 / 0 |
| 正式採納／排序 | 0 / 0 | 0 / 0 |

Yin 的 85.9% 浮選階段 TREO 回收與 31.22% 精礦品位是兩種量。Cowalinya 的 24.8 kg HCl/t 浸出進料無法與 Nolans 的 828.7 kg H₂SO₄/t 精礦工程設計值比較。Jupiter 的 71% 中試選礦回收和 58% 全流程模型回收各有用途。原報告另列 51% 質量產率、0.14% TREO 進料及 1.3% 精礦品位，按同一段報導的物料關係計算，最有利的顯示精度仍導致超過 100% 的推算回收。影子試驗保存原值和衝突。

獨立留出由開發規則作者以外的人核對。Wolverine 84% 為 ore-composite variability 試驗中的選礦 TREO 回收，Goschen Central 85% 為規劃的整體回收率，同一百分比單位無法構成比較。獨立合成契約另核對同條件跨專案萃取率的正例，以及改為最終煅燒產品回收的反例。實際留出只有一對已知負例，整體精確率、召回率及自主文件擷取能力仍為 `UNMEASURED`。

O3 適用於本影子試驗。實際 cohort 為 `INSUFFICIENT_COHORT`，正式 T1v2 維持 `NOT_RUN`。兩次開發資料重跑的原件清冊、觀測及用途配對逐位元一致。沒有同用途的有效獨立專案群組可執行效用函數或權重敏感度分析。

使用 `technical_bindings_v1.json` 與 `technical_processing_oracle_v1.json` 重跑開發回歸，或使用 `technical_holdout_bindings_v1.json` 與 `technical_holdout_oracle_v1.json` 重跑獨立留出。`technical_pilot_receipt.json` 記錄時間、狀態及雜湊，`technical_observations.jsonl` 和 `technical_pairs.json` 記錄觀測與比較，`technical_oracle.json` 記錄回歸結果。使用 `AU_REE_TECHNICAL_MANIFEST` 與 `AU_REE_TECHNICAL_CAS` 提供實際取得清冊及原件儲存根目錄，可啟用測試中的完整原件回歸。
