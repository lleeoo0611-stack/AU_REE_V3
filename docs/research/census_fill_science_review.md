# Census 適用性、磁性氧化物揭露與衍生驗證

本次以 Stage 6 Candidate `8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3` 及其 147 份原件為基準。`census_member_disclosure: true` 啟用現行欄位契約 `approved_member_presence`。範圍維持 93 個 Project、51 欄與 2026-11-28 截日。

## T2 欄位適用性

獨立原件核對含 8 份具截日資格的 PDF、7 個案例及 32 個資源欄格，新增 `NOT_APPLICABLE` 0 格。Cape 與 Rankin Dome 原件明示各發行報告當時沒有資源估算。Southern Cross 於 2023-08-31 的聲明亦明示當時無資源估算。2025 年發行報告中的礦權放棄有獨立定位，仍缺可確認截至研究截日資源估算狀態的時序證據。North Stanmore 的 Target 1 無資源陳述適用於該子區，且同一原件另有保留的全計畫資源格。Pomme、Beachcomber 與 Buntine 是其他計畫或材料範圍。其原報聲明已保留在具 SHA、Capture SHA、URL、Temporal Decision 和頁碼的查核收據。

## T3 正式衍生與描述性揭露

Jupiter、North Stanmore 與 Nolans 的三個核准 TREO 衍生值，以及 Wudinna 的一個核准原報組合 MREO 衍生值，均依原件與獨立有理數算術逐項重現。Nolans 仍為 1,456 kt TREO，區間 1,415.25–1,497.25 kt，R1 維持 `INCOMPLETE_SELECTED_RESOURCE_BUNDLE`。新增正式衍生或 MCDA 輸入 0 格。

Wolverine 在實體第 6 頁 Table 5 的 Total 列逐項揭露 Pr₆O₁₁ 60、Nd₂O₃ 280、Tb₄O₇ 120、Dy₂O₃ 840 ppm，具同一原件、估算日、原礦、報告範圍和 cutoff。四項 Observation ID 及其原件定位支持 `四項磁性元素皆公布 = 是` 一個描述格。第 5 頁 Table 2 的 0.85 kg/t Dy₂O₃ 與第 6 頁的 840 ppm 依原報末位計出的區間不重疊。揭露格保留兩個原件位置與精度狀態。14 個 Wolverine 影子結果的點估及區間各自比對，Dy₂O₃、Y₂O₃ 和受影響的完整組合維持既有科學限制。Wudinna 的原報 MREO 組合保持其核准用途。

## 重跑

`BASELINE` 和 `CANDIDATE` 為各自的 Research Build 檔案夾。`SOURCES`、`PROFILE`、`ASSETS` 對應原件清冊、研究設定、內容定址儲存。`WEB_DATA` 包含兩個已核驗候選的 `releases/` 檔案夾。此處 `RELEASE_ID` 為候選封存 ID。

```bash
PYTHONPATH=src python tools/audit_census_applicability.py --profile "$PROFILE" \
  --sources "$SOURCES" --assets "$ASSETS" --baseline "$BASELINE" \
  --candidate "$CANDIDATE" --out "$OUT/applicability-oracle.json"
PYTHONPATH=src python tools/verify_census_derivations.py --compare-stage6 \
  --assets "$ASSETS" --baseline "$BASELINE" --candidate "$CANDIDATE" --out "$OUT/treo-oracle.json"
PYTHONPATH=src python tools/verify_reported_mreo.py --compare-stage6 --require-disclosure-gain \
  --assets "$ASSETS" --baseline "$BASELINE" --build "$CANDIDATE" --out "$OUT/oxide-oracle.json"
AU_REE_CANDIDATE_RELEASE_ID="$RELEASE_ID" node web/tests/browser.mjs "$WEB_DATA" --census-fill
```
