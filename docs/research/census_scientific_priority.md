# Census 科學證據優先清冊

固定 main 為 `44bc122d1337434cb8f496d8d0d6d0dd2dc4d981`。Stage 4 候選 `a1805f68b22d73ab070be50f6d2a110edfcb30230e21778b6b62513ccbcc96ee` 的封存程式是 `69a111dd56420d364c1ddf2547fe81888b4e51ed`。以 2026-11-28（Asia/Taipei）與原設定重核 144 份原件及 148 次合格擷取。兩份本機 CAS 各冷重播 8 項科學與 4 項展示產物，指紋全數相符。展示重播 Python hash seed 為 5151、5252。獨立驗證從 11 份 PDF 核對 42 筆原文。SQLite 完整性與外鍵檢查通過。兩份 CAS 共用本機故障域，F5 維持 `BLOCKED`，歷史 O2 保留 `FAIL`。[基準與收據](census_scientific_baseline_receipt.json)

固定 4,743 格有值 1,138，逐格核驗展示 46，其中 16 直報、3 核准衍生、27 經驗證描述，覆蓋 12 個 Project。擴充欄單獨為 39／837。正式數值候選、驗證與選值為 42／27／21，R1 四鍵、S1 兩鍵，完整面板兩案。基準工作新增獨立原件觀測、正式值、Project×Criterion 及完整面板均為零。

4,743 個鍵逐格重對帳。V2 的 2,500 個歷史顯示值中，696 字面相同、10 僅格式不同、432 值不同、1,362 僅存在於 V2。其餘 2,243 鍵兩側皆空。舊報告的 1,396 是早期 V3 科學投影基準，本次清冊以後續 Stage 4 封存候選為準。歷史值只保留指紋及 exact SHA 定位，仍須符合 V3 科學驗證。

## 回讀優先序

3,948 筆輸入映射合為 2,518 個回讀鍵。每鍵保存 Project、原件 SHA、版次、聲明範圍、估算日期、截品、分析物及原文定位。缺少主張範圍、日期或欄義時保持未知。待回讀原件附帶候選用途、下一步證據、阻礙原因與條件式影響範圍。109 個優先鍵已有原文數字定位，而且目標 Project 有待驗證的科學準則。當中 9 筆是早期曾讀回但證據不足的 Observation，重讀不增加獨立觀測數。針對各鍵的準則影響是檢查上限，不可相加為增益。

142 筆待綁定出現紀錄及 15 個無完整表格工作逐項承接。Yin 的有效日與含量衝突、Yangibana 的化學定義及 Wolverine 報告範圍均保留待證。四份舊版截至 Stage 4 七次公開尋回皆無合格 exact SHA。Wudinna 與 Yttria 的 V2 唯讀 Git 線索與 V3 公開取得失敗分別保存。待查清冊不擴大固定母體，也不寫入選值或正式發布指標。

## 重播與成本

從兩份已封存的 Stage 4 CAS 分開執行 `research replay --offline`。分別恢復 exact `build_receipt_sha256`，以 `69a111d` 的 `research build-display` 及各自固定科學、地理 CAS 重播展示。第一次展示重播 A 指向固定科研 CAS，缺少獨立地理物件而拒絕。改用已鎖定的地理 CAS 後通過，同一份地理清冊指紋驗證相符。[Stage 4 完整重播選項](census_display_coverage_results.md)

科研冷重播壁鐘 15.027／15.469 秒，成功的展示冷重播為 7.042／8.797 秒。另在相同輸入的一次配對實測中，重用展示 DocumentCache 使 PDF 開啟及頁數由 16／736 降至 5／250，壁鐘 6.677 → 2.858 秒，CPU 6.795 → 2.885 秒。程序 RSS 高水位為 100,872 → 119,432 KiB，暖樣本為同一程序的累計高水位。兩次均無 SQLite 敘述，產物指紋相同。作業系統頁面快取未控制，個別讀取器仍有獨立快取。清冊組裝本身讀取已驗證收據，新增 PDF 解碼為零。

```sh
WORKSPACE=/workspace/AU_REE_V3
A="$WORKSPACE/work/census-stage5"
PYTHONPATH=src python -m tools.census_scientific_priority \
  --workspace "$WORKSPACE" --v2-root /workspace/AU_REE_V2_reference \
  --science-replays "$A/cold-a" "$A/cold-b" \
  --display-replays "$A/display-cold-a" "$A/display-cold-b" \
  --out "$A/priority-rebuilt"
```

輸出為 `census_scientific_baseline_receipt.json`、`census_scientific_gap_grid.csv` 與 `census_scientific_priority_queue.jsonl`。此工作新增審計工具、測試、報告和機器收據。凍結的 profile、現有科學模組、不可變釋出與 V2 歷史資料均依既有版本保留。
