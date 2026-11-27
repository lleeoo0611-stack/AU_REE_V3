# AU_REE_V3 資料產出與可比較性

研究截日為 **2026-11-28，Asia/Taipei**。本輪基準提交為 `b6cdab87b2435f66ce295bdb3bbb48e4772edd5c`。固定 93 個 Project ID 和 123 個來源 SHA 均保留。前一版候選 `d055f0726cff7bf783731bd3e698d3334a214fbcf593b8f934dfe089204a5a6b` 經 SHA 讀回及冷建驗證。

## 研究成果

| 指標 | 前版 | 本輪 | 變化 |
| --- | ---: | ---: | ---: |
| 唯一來源 SHA | 123 | 144 | +21 |
| 候選 Observation | 24 | 38 | +14 |
| 已驗證 Observation | 12 | 23 | +11 |
| 正式選值 | 6 | 17 | +11 |
| Census 正式科學格 | 6 | 12 | +6 |
| R1 Project×Criterion | 2 | 4 | +2 |
| S1 Project×Criterion | 0 | 2 | +2 |
| 可比較 Project×Criterion | 2 | 6 | +4 |

Census 比對全部 4,743 格。原有 6 個正式科學值在新版本重建選值及發布血緣，數值保持一致。另有 6 個新正式格、93 個 Project ID 格及 4,638 個 `UNKNOWN` 格。5 筆組成觀測供 S1 推導，無對應 Census 欄。

| 新增專案 | 正式 Census 值 | 可比較準則 |
| --- | --- | --- |
| Wudinna | 150 Mt、0.0470% TREO（原文 470 ppm）、70.400 kt TREO | R1 70.400 kt、S1 23.829787% |
| Goschen Area 1 | 94.200000 Mt、0.08% 原位 TREO＋Y₂O₃、74.000 kt | R1 74.000 kt |
| Browns Range Wolverine | 四個同聲明 whole-ore 氧化物分別為 Pr₆O₁₁ 60、Nd₂O₃ 280、Dy₂O₃ 840、Tb₄O₇ 120 ppm | S1 13.541667% |

兩個 S1 的 TREO 分母含相同 15 種氧化物，包含 Y₂O₃。Wudinna 的 112 ppm MREO 為原文總列數值，四種組成由原文定義，單位由同資源聲明的原生 PDF 圖表、圖像 SHA 及精度區間交叉核對。兩組 R1 新值保留各自的 JORC 分級、估算日和 cutoff。Goschen 正式選值來自 bulk ore 表，重礦物產品另有證據角色。

淨增 4 組的結果為 `EVIDENCE_INSUFFICIENT`，挑戰目標為 +10。R1 可比較專案對由 1 增至 6，S1 由 0 增至 1。

## MCDA 與 GIS

| 面板 | 完整案例 | 結果 |
| --- | ---: | --- |
| `issuer_resource_composition` | 2 | `PASS`，Wudinna 第 1，Wolverine 第 2 |
| `issuer_resource_study` | 0 | `INSUFFICIENT_COHORT` |
| `issuer_resource_infrastructure` | 0 | `INSUFFICIENT_COHORT` |
| `resource_infrastructure_pilot` | 0 | `INSUFFICIENT_COHORT` |
| `six_domain` | 0 | `INSUFFICIENT_COHORT` |

SAW、TOPSIS、PROMETHEE II 在三個既有權重情境均產生同一雙案名次。移除 S1 或 Strategic 構面時，Wolverine 因 R1 較高而排名第一。獨立十進位重算核對 18 筆分數及名次、24 筆準則與構面移除結果，最大分數誤差約 `1.704e-16`。面板沿用凍結門檻、權重和效用。PR #4 的數學核心已在主線，該 PR 的歷史流程仍用於舊矩陣診斷。

西澳兩個政府礦權快照以原始 SHA、原生 feature ID、`EPSG:4283`／`EPSG:7844`、測量狀態及截日判定重建 7 個礦權法定邊界，另有 5 個未找到唯一圖徵及 2 案缺發行人礦權連結。可驗證的代表性礦址和基礎設施距離為 0；I1–I4 維持候選或缺值。

## 來源、驗收和研究版本

- 148 個取得版次通過時間資格重算，涵蓋 93 案、144 個唯一 SHA。原件為 141 份 PDF（7,051 頁）、1 份 HTML、2 份 ZIP。未發生整份解析失敗。三頁原生文字空白，OCR 找到兩頁候選，一頁無法辨識。368 筆檢索線索保留候選。
- 原先 32 筆 MIME 不符與 1 筆 404 中，30 筆找到同 SHA 官方替代原件，2 筆僅有同案官方連結，1 筆仍無已驗證替代。追加取件的 26 次請求裡有 21 次取得及 5 次 MIME 不符。來源覆蓋與科學選值分開計算。
- 6 個設計回歸案例核對 25 個有標註候選欄位，TP/FP/FN 為 25/0/0，錯誤正式採納 0。獨立留出實跑 1 個鈮資源反例並正確拒收，另外 2 案 `NOT_RUN`，留出精確率、召回率及整體準確率 `UNMEASURED`。Yangibana 化學基準及 Yin 估算時間／表列衝突仍待證據。Sandy Mitchell 與 Aileron 的兩份探勘性核對原件另存，未納入本發布輸入。
- A 來源索引及原件擷取、B 語義與空間證據、C Census/MCDA 與離線重播、D 本地備份讀回及故障測試均 `PASS`。F0–F4 `PASS`，F5 `BLOCKED`。非本輪必要的歷史完整重播 `NOT_RUN`。
- 新研究候選 `24bb0056a0655757a14fd11244fcee207e8a9b88f3d576f7e86c6341b97b3974` 固定科學提交 `d5349613a2f24fc8808f001b68aa6818ce29b713`。兩個本地儲存各核驗 310 件物件，冷建兩次各比對 8 個科學輸出。第二次僅用本地副本，亦未提供發布索引。前版候選另冷建一次，指標回滾演練通過。正式指標維持原狀，實際獨立儲存帳號及故障域仍待配置。

## 工程量測

同樣三種既有版面與原件、每版三次擷取的中位數由 0.290 秒變為 0.266 秒，峰值 RSS 由 34,592 KiB 變為 34,620 KiB，六次的 Observation 輸出同 SHA。規模增加後，新候選實建 2.14 秒，封存 8.50 秒，兩次冷建 6.15、5.61 秒，整個驗證程序峰值 RSS 92,904 KiB。舊等規模封存讀回由每端 803 次降至 537 次；同範圍冷建中位數由 4.16 增至 4.78 秒，頁面快取未控制。

既有 Census/MCDA 重複的資源判定由兩處收斂至一個共用規則，來源與發布使用同一版次時序檢查。納入新組成政策後，三個相關模組的內部匯入邊由 11 增至 15、AST 分支類節點由 134 增至 180，包含新功能的成本。分數求解核心 SHA 不變。長期維護成本 `UNMEASURED`。

| CI 執行 | 單元與靜態 | 固定原件整合 |
| --- | ---: | ---: |
| 前版 PR #9 | 13 秒 | 48 秒 |
| A PR #10 | 10 秒 | 27 秒 |
| D PR #11 | 18 秒 | 53 秒 |
| B PR #12 | 19 秒 | 62 秒（含 GDAL 安裝及測試） |

上表為各次 GitHub runner 實際 job 時間，網路及 runner 變異未控制。計費成本 `UNMEASURED`。C 的 CI 與手動發布重播收據記錄在對應 PR。C 固定提交的本地測試執行 168 項，全部通過。

## 收據與重現

所用來源原件、實際收據與大型證據清冊位於工作區 `work/yield/`。組合來源清冊為 `work/research-yield/source-inputs/source_manifest.json`，SHA `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`。逐格 Census、逐準則與面板、發布和冷建收據位於 `work/yield/candidate/delta/`。另有 [機器可讀摘要](data_yield_comparability_receipt.json)、[檔案生命週期](data_yield_file_lifecycle.csv)、[備份操作](replica_operations.md) 和 [模組責任稽核](engineering_responsibility_audit.json)。

```bash
profile=config/research_profiles/au_ree_2026-11-28.json
sources=work/research-yield/source-inputs/source_manifest.json
store=work/fresh/source-store
release=24bb0056a0655757a14fd11244fcee207e8a9b88f3d576f7e86c6341b97b3974
previous=d055f0726cff7bf783731bd3e698d3334a214fbcf593b8f934dfe089204a5a6b
au-ree-v3 sources inventory --profile "$profile" --manifest work/fresh/acquisition-final/source_manifest.json \
  --alternate-manifest work/research-yield/source-retry/source_manifest.json --store "$store" \
  --baseline-criteria work/fresh/build-c/decision/mcda_inputs.json --out work/review-inventory
au-ree-v3 geography tenements --bindings config/research_profiles/wa_tenement_bindings_v1.json \
  --sources "$sources" --store "$store" --out work/review-geography.json
au-ree-v3 research replay --offline --release-root work/yield/candidate/archive --release-id "$release" \
  --store "$store" --out work/review-replay
au-ree-v3 research compare --baseline work/fresh/build-c --current work/yield/candidate/build \
  --baseline-release-id "$previous" --current-release-id "$release" \
  --release-root work/yield/candidate/archive --store "$store" --replay-run work/review-replay --out work/review-delta
```

後續優先補有效日與不同資源聲明的化學核對、增加含正例的獨立留出樣本、以正式礦址和政府設施圖層驗證 I1–I4，並核驗獨立故障域上的原件與冷建。D1、T1 及六構面研究仍需完成證據採納。
