# 原件數字待查清冊

固定研究設定為 2026-11-28（Asia/Taipei）。清冊核對 PR #24 查找產物、PR #25 路由與獨立身分收據的 SHA-256，再從 V3 CAS 重讀原件。原生數字保留 PDF 實體頁、行、欄、完整字串及同頁表頭。相同來源座標使用相同 `source_position_id`，仍保留各 Project 的查核範圍。排序以實體來源、Project 與表格標題位置合併工作，跨頁與聲明身分待實證才合併。

本次清冊涵蓋 47 筆專案範圍資料列和 337 筆數字位置的專案關聯，對應 298 個唯一原件數字位置。33 個已知綁定以舊 V3 Observation、Candidate、Validation 及 Evidence Anchor ID 標記，供去重。19 個原件已讀且結構未辨識的工作項目單獨列入查核。所有項目維持候選，科學採納新增 0 筆。

41 個實體表格工作單元依可見同頁表頭、可能的直接含量欄及鄰近語境訊號排序。附近出現 JORC、材料、日期、截品或化學詞彙只提供查核線索。數字量別本階段標為 `UNBOUND_TABLE_NUMBER`。語境、欄義及關聯仍須送後續原件解析與驗證。

| 缺版次 | 固定 V3 母體之外的結果 |
| --- | --- |
| Wudinna，`e41fa365…` | 固定 V2 Git 有相同 SHA 的 HTML 原件，來源由倫敦證券交易所公布 |
| Yttria，`73936d12…` | 固定 V2 Git 有相同 SHA 的 PDF 原件，來源為 ASX 揭露 |
| Splinter，`428c8f75…` | 已鎖定西澳政府 WAMEX 入口，既有回讀收據未取得原件 |
| Mt Stirling–Yttria，`bb46f35a…` | ASX 入口既有回讀取得不同 SHA，保留為獨立版次 |

四個缺版次共影響 35 個歷史定位提示。固定 V2 Git 的 2 份同 SHA 原件未加入本輪 V3 案例母體。後續若納入，須為原件另建 V3 取得收據與時間證據。新 SHA 不可替代歷史版次。

## 重跑

執行目錄為 AU_REE_V3。輸出須使用新資料夾。以下路徑對應前輪產物。`committed-a` 的實際來源由 [PR #25 驗證收據](native_value_recovery_verification.json) 鎖定。

```sh
python -m au_ree_v3.source_case_inventory \
  --verification docs/research/native_value_recovery_verification.json \
  --routing work/census-completion/committed-a \
  --discovery work/census-completion/discovery \
  --sources work/research-yield/source-inputs/source_manifest.json \
  --assets work/fresh/source-store \
  --v2-root ../AU_REE_V2_reference \
  --out work/scientific-admission/inventory-new
python -m unittest discover -s tests -p test_source_case_inventory.py -v
```

`source_case_inventory_receipt.json` 保存輸入及四份輸出的 SHA、逐來源核對狀態與 `SY0` 結果。全量 `SY1` 量別綁定由後續工作驗證。
