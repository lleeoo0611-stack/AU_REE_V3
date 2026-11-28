# 原生資源表格的分類、尺度與分母證據

本輪辨識器維持候選用途。它核對原生表頭、印出的截止品位與物料註解，再將各自的頁、行、欄與原始 token 寫入候選證據。固定清冊下未增加正式採納、比較鍵或完整面板。既有正式選值規則與科學結果維持等價。 [驗證收據](native_resource_context_binding_verification.json)

## 實際原件與待查處置

- Yin 第 11 頁 Table 4–6 的 3 列，以及第 16 頁 Table 9–10 的 5 列，由原文分類表頭或明示 `Inferred Resource`、截止品位及數值欄連結。第 16 頁 Gifford Creek Carbonatite 的 0.70% TREO 列為 `10.84 Mt`、`1.00%`、`108,000 t`，與同頁 Yin 的 0.20% 列分開定位。Yin 封面 `311,800 t` 與詳表 `312,300 t` 保留各自原報，估算有效日仍待補。
- Goschen 第 12 頁的 `Rare Earth Oxides (2)` 與同頁編號 2 註解將 14 個原報數字位置明確綁定為原位 THM 百分比。下方 `94,200,000 t` 的物料列具不同表頭及材料語境。未將該氧化物面板用作 S1 的全礦組分。
- 分層的 `Tonnes` 與 `(Millions)` 依實體欄位連結，保留原印尺度。合成測試核對這個階層；Mt Weld 的實際 `Million Tonnes` 維持 `Mt`，Wolverine 的第 6 頁不同範圍各自保留原報組分。陽性原件例及儲量反例均未改變正式用途。

在相同 34 份原件的待查工作清冊內，155 項逐一重跑。待綁定 141 項、缺結構 14 項、已定位的獨立欄義位置 169 項均維持原數。具名的 Yin／Gifford Creek 詳表增加 8 列候選，屬於另外的原件頁對照。不同頁的重刊由其聲明範圍鑑別，未計為新增獨立觀測。固定清冊 SHA-256 為 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`。兩次工作產物逐位元一致，收據 SHA-256 為 `5bf2280ad6f31829472caea7f63528fbb0fbde2f137bef9f2744458f090a0816`。

## 獨立分層評估

| 面板 | 自動定位 TP / FP / FN / TN | 給定定位欄義 TP / FP / FN / TN | 端到端欄義 TP / FP / FN / TN |
| --- | --- | --- | --- |
| Stage 3 歷史五家族 | 1 / 0 / 4 / 3 | 8 / 1 / 6 / 20 | 2 / 0 / 12 / 20 |
| Stage 5 六家族 | 0 / 0 / 5 / 4 | 1 / 0 / 15 / 25 | 0 / 0 / 16 / 25 |
| Stage 6 新八家族 | 0 / 0 / 3 / 11 | 0 / 1 / 6 / 53 | 0 / 0 / 6 / 54 |

三組基準與本次的各層結果一致。Stage 6 正例欄位有 6 個，來自一個家族的 3 列；來源層級正例超出相應登錄 Project 的可正式採納範圍。Stage 6 泛化增益為 `FAIL`，原因 `HYPOTHESIS_NOT_SUPPORTED`。端到端無正向判定的兩個面板 precision 為 `UNMEASURED`。正式採納的獨立 precision 與 recall 為 `UNMEASURED`。評估完整性 `PASS`，凍結後只讀總計，保留既有單位誤判及新面板提供定位的誤判。逐組 precision、recall、分母及指紋見收據。

## 工程與重播

| 同範圍工作 | 基準 | 本次 |
| --- | ---: | ---: |
| 既有定位 15 份 PDF、1,292 頁，wall 中位秒數 | 5.7804 | 5.5975 |
| 既有定位 CPU 中位秒數 | 5.9156 | 5.6945 |
| 既有定位峰值 RSS KiB | 47,400 | 47,184 |
| 完整工作 34 份 PDF、2,698 頁，wall 中位秒數 | 20.2195 | 20.0633 |
| 完整工作峰值 RSS KiB | 72,712 | 75,092 |
| 同定位動態相依邊及可達靜態邊 | 16 / 35 | 16 / 35 |
| 同定位 AST Max / P90 | 48 / 17 | 49 / 17 |

既有定位交錯量測三次，完整工作兩次。完整範圍皆搜尋 1,406 頁，新版另核對前次處置收據。套件靜態相依邊 246、循環見證與三組完全重複 AST 保持原數。六份具名 PDF 的冷／暖快取中位秒數為 1.142／0.106，暖快取省去六次解碼與 126 頁，輸出 SHA 相同。作業系統未清除檔案快取，OCR 未執行，原生候選路徑的 SQLite 查詢為 0。沒有本模組新增正式值，每新增正式值成本 `UNMEASURED`。

兩個新程序用同一固定 CAS 及 `PYTHONHASHSEED=71`、`98` 完成研究重建，八件科學產物 SHA 皆一致。兩份唯讀 SQLite 均通過 `integrity_check`、`foreign_key_check`。與封存 Stage 5 的 4,743 格及 13 組科學輸出比較一致，維持 53／37／31 筆候選、驗證與選值，R1／S1 合計 6 鍵。含原件的完整本機測試 383 項通過，0 跳過，wall 106.37 秒，峰值 RSS 188,276 KiB。隔離工作樹未執行遠端 CI。

```sh
python -m au_ree_v3 research bind-native-tables \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --inventory work/scientific-admission/inventory-verified-a/source_case_inventory.jsonl \
  --inventory-receipt work/scientific-admission/inventory-verified-a/source_case_inventory_receipt.json \
  --sources work/research-yield/source-inputs/source_manifest.json \
  --assets work/fresh/source-store \
  --prior-binding work/census-stage5/native-binding/verified/expanded-1 \
  --out work/census-stage6/native-binding/replay
```

既有位置效能比較使用 `--located-only`。正式等價重跑使用 `research build`、`au_ree_census_display_v1.json`、`resource_bindings_v5.json` 和同一原件清冊。
