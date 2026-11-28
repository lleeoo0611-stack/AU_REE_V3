# 原件科學數值准入與研究候選驗收

固定基準為 `704de116e0d1bb5acc067855ab023826b5ee7d24`，研究截日為 2026-11-28（Asia/Taipei）。以相同 93 案、144 個原件 SHA 和 148 筆合格擷取事件新建研究候選。正式選值是候選研究內通過規則的數值。候選的發布狀態仍為 `RESEARCH_CANDIDATE`，F5 `BLOCKED`。

## 科學增量

| 指標 | 固定基準 | 新候選 | 淨變化 |
|---|---:|---:|---:|
| 候選觀測／通過驗證／正式選值 | 38／23／17 | 42／27／21 | +4／+4／+4 |
| Census 有值格／正式科學格 | 1,104／12 | 1,108／16 | +4／+4 |
| 合規的正式衍生格 | 0 | 0 | 0 |
| 可比較 Project × Criterion | 6 | 6 | 0 |
| R1＋S1 完整面板 | 2 | 2 | 0 |

| Project ID | 正式資源量 | 正式 TREO 品位 | 聲明與原件 |
|---|---:|---:|---|
| Jupiter `AU-dac3c8e40326b8244cdb` | 1,700 Mt | 0.1700% | 2026-09，Indicated＋Inferred，乾基全礦石，1,000 ppm TREO 截品，實體第 4 頁 Table 2、2026 欄。原值 1.7 Bt、1,700 ppm，兩位有效數字。[ASX 原件](https://announcements.asx.com.au/asxpdf/20261012/pdf/07560pwzkdgsrg.pdf) |
| North Stanmore `AU-216d0983f6892f9b58f9` | 278.80 Mt | 0.048264% | 2026-08，Measured＋Indicated＋Inferred，乾基全礦石，300 ppm TREO 截品，實體第 97 頁。原值 278.80 Mt、482.64 ppm。第 136 頁兩個原生圖的 15 種氧化物加總區間為 482.575–482.725 ppm，與原表 482.635–482.645 ppm 區間相交。[ASX 原件](https://announcements.asx.com.au/asxpdf/20260818/pdf/072w8qbl7kfy0n.pdf) |

四值使用 `approved-census-resource-observation-v1` 和現有 Temporal Evidence Gate。兩案的公式含量留在 Shadow，正式含量欄仍為 `UNKNOWN`。R1＝4、S1＝2，既有兩案的 SAW、TOPSIS、PROMETHEE II、權重與敏感度透過獨立 Decimal 計算核對。其餘四個面板樣本不足。4,743 格差異含 4 個新增正式格、52 個缺值理由或內容變更、16 個證據收據更新與 4,671 個無差異。原有 12 格正式值和 17 個選值觀測身分完整重現。

## 原件漏斗、拒收與外推限制

清冊核對 144 個原件 SHA。389 個同 SHA V2 線索及 35 個缺版次線索保持證據定位用途。四個歷史缺版次中，兩份由唯讀 V2 Git 取回同 SHA 歷史原件，另兩份在固定查核中無 exact SHA。Splinter 的 WAMEX 回應缺合格文件，Mt Stirling 的同網址取得不同 SHA；兩份屬不同版次的檔案保持獨立。資料收據保留這些查核的執行範圍。本輪未重跑缺版次網址。

原件批次重新解析 15 份 PDF、1,292 個實體頁，逐項處理 47 個範圍化 Total 列和 298 個原件數字位置。112 筆範圍化欄義可確認，對應 103 個原件位置。排除 2 筆同一原件儲存格的既有重複識別後，候選清單有 110 筆、102 個原件位置。33 筆既有候選維持身分，19 個 Source × Project 尚無可確認的表格結構。225 筆範圍化定位保留未綁定狀態，其中 210 筆欠表頭或單位、13 筆定位歧義、2 筆非唯一原生格。兩份新准入聲明各由獨立的 exact-SHA 配方補齊上下文。自動批次欄義收據、正式觀測與來源線索分別有可核對的分母。

優先案的逐案科學紀錄包含 Redlings 的 Inferred-only、Mount Ridley 的 Ce₂O₃ 或含量一致性疑點、Cummins Range 的 P₂O₅ 截品、Deep Leads 的美元截品和 TREO−CeO₂、Cowalinya 的 Er₂O₃ 重列、Koppamurra 的 TREO−CeO₂ 和日期角色、Nolans 的估算範圍或日期證據，以及 Salazar 缺同一估算的有效日。另有固定原件 Barkly 含量精度衝突和 Wolverine Dy₂O₃ 的 0.85 kg/t 對 840 ppm 衝突。Wolverine 既有 S1 保留 840 ppm 與原評估分數。Nolans 外加原件在獨立清冊驗證 145 SHA／149 擷取事件，原件日期鏈分別落在 2015-10-29 和 2015-10-30，未進入 144 份研究母體。

兩組在開發前凍結的欄義留出樣本分別為 TP／FP／FN＝1／0／9 及 1／0／30，召回 10% 和 3.2%。兩組均只產出一個正確預測，精確率分母為 1。樣本有發行人家族重疊，收據分開保存。正式用途的獨立留出樣本三案皆欠有效日，兩用途各拒收三案，未出現誤收。正式用途精確率及召回率均為 `UNMEASURED`。六個受控真實原件科學反例和一個原件物件損毀也完成隔離驗證。

## 重播、版本與網站

SY0、SY1、SY2、SY3、SY4、SY5 及 F0–F4 通過本輪核驗。歷史 O2 維持 `FAIL`，封存 O4 另以固定 CAS 重播通過。F5 缺獨立故障域。新舊候選各以自己的固定輸入及程式封存，兩次獨立冷重播均核對 8 個科學輸出。瀏覽器逐一驗證四個新證據視窗、舊版缺值、版本切換及錯誤包隔離。受控回滾移動預覽指標，兩個封存指紋維持一致。正式發布測試回報 `REPLICA_UNAVAILABLE`，正式網站建置拒絕缺發布授權的候選。

- 舊候選 `200a950a0c257bca4698c6fe71bfab9fc3f724f55980bb658a54cca1f4b7311b`。
- 新候選 `de50379f50e49fdd74ac536deb51b8b03dade8f442565644564c5cef1f2a17ef`。
- 逐格差異、原件精度區間、Observation ID、Scope、規則、兩次重播 SHA、封存與候選預覽檔指紋均由機器可讀收據索引。[驗收收據](scientific_value_admission_receipt.json)

## 工程量測

同 144 原件與 v3 配置的三次交錯中位耗時 1.869 → 1.880 秒，Python 峰值 RSS 61,132 → 61,704 KiB。動態相依邊 56 → 57、實際到達模組的靜態匯入邊 74 → 87，AST 分支分數上限／P90 為 79／20 → 119／20。完全重複 AST 函式體群維持 1。相同工作量的科學簽章一致。新增 v4 原件配方的完整驗證中位耗時 2.042 → 3.405 秒、峰值 RSS 61,384 → 93,624 KiB。獨立儀器量測記錄 PDF 開啟 6 → 8 次、原生頁 49 → 224 頁、SQLite 敘述 2,153 → 2,587 次。

同一解譯器重用 DocumentCache，完整 v4 固定流程冷／暖為 3.381／1.547 秒，PDF 開啟與解碼由 8 次／224 頁降為 0／0，8 項科學輸出完全一致。抽查三個留出來源的相同 11 頁，舊原生路由／新欄位辨識冷程序約 0.168／0.160 秒，暖快取約 0.030／0.029 秒。相鄰修訂量測有波動。所有工程量測的 OS 頁面快取皆未控制。工程審查可將工作集中於泛化召回與准入管線的最大分支；長期維護成本及 GitHub 計費皆為 `UNMEASURED`。A、B、C 的靜態／原件 CI 工作秒數分別為 25／70、21／50、20／63。固定 V2 CI 任務受觸發條件限制，這些 PR 執行為 `NOT_RUN`。本機固定原件 288 項測試通過，零略過。

## 重現與檔案生命週期

本輪以短 PR 依序匯入來源清冊與缺版、表格綁定、用途准入、發布驗證。原有程式與資料契約依機器收據列出的責任修改；新添通用欄位、清冊和獨立查核，以及 v4 配置、原件擷取收據、正反例與研究收據。無檔案刪除或合併。歷史原件、v1–v3 設定、先前網站與封存維持原指紋。[來源清冊](source_case_inventory.md) · [檔案逐筆清單](scientific_value_admission_receipt.json)

在持有固定 CAS 和先前路由產物的環境，先以 `python -m au_ree_v3.source_case_inventory` 產生清冊。再以 `research bind-native-tables` 搭配同一 manifest、inventory JSONL 和清冊收據。研究重播使用 `d114c388440ad7db9b1b3de25d3e7646d4a84953`、`config/research_profiles/au_ree_2026-11-28.json` 和 `resource_bindings_v4.json`。

```bash
python -m au_ree_v3 research build --profile "$profile" --bindings "$bindings" \
  --sources "$manifest" --assets "$asset_store" --code-commit "$code_commit" --out "$build"
python -m au_ree_v3 research archive --profile "$profile" --bindings "$bindings" \
  --sources "$manifest" --store "$archive_store" --repository . --build "$build" --out "$archive"
python -m au_ree_v3 research replay --offline --release-id "$candidate" \
  --release-root "$unused_index" --store "$archive_store" --out "$cold_a"
python -m au_ree_v3 research replay --offline --release-id "$candidate" \
  --release-root "$unused_index" --store "$archive_store" --out "$cold_b"
python tools/verify_native_resource_values.py --baseline "$old_build" --current "$build" \
  --manifest "$manifest" --assets "$asset_store" --out "$value_receipt" \
  --oracle tests/fixtures/native_resource_v1/expected_census.json
python -m au_ree_v3 research export-web --profile "$profile" --research-build "$build" \
  --release-id "$candidate" --release-root "$archive" --out "$web_data"
python -m au_ree_v3 research build-site --mode candidate --data "$web_data" \
  --release-id "$candidate" --out "$preview"
python -m au_ree_v3 research verify-site --mode candidate --site "$preview" --out "$site_receipt"
```

封裝預覽可解壓後以 `python -m http.server` 在該目錄本機提供靜態檔，透過 `?preview=1` 查看版本。完整檔案路徑與 SHA 由機器收據記錄。最新一輪 CI 和 PR 由研究整合紀錄追蹤。[A 的 PR #26](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/26) · [B 的 PR #27](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/27) · [C 的 PR #28](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/28)
