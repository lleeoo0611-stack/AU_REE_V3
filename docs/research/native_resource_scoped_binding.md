# 原生資源表格的截止品位與聲明範圍辨識

本輪在固定來源清冊 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd` 上重跑原生表格。定位、欄義與正式採納分開評估。原生候選輸出保留原始頁、行、欄與單位證據，仍待既有來源、時間、化學及研究用途驗證。新增正式科學觀測、選值、可比較準則鍵與完整面板皆為 0。 [機器可讀驗證](native_resource_scoped_binding_verification.json)

| 固定來源工作量 | 基準 | 本次 |
| --- | ---: | ---: |
| 已定位但欄義待查的出現紀錄 | 142 | 141 |
| 缺表格結構的來源與專案配對 | 15 | 14 |
| 已辨識原文欄義位置 | 168 | 169 |
| 原先缺結構工作所找出的獨立候選位置 | 16 | 19 |

157 個先前待查項目逐項保留處置。復得的一個欄義位置對應既有核閱觀測，另找回一個來源與專案配對的表格。搜尋或資料重刊不增加獨立觀測數。兩次工作輸出逐位元一致，收據 SHA-256 `76df03839b47448f09e4cc38aaf3005c118a671d1e6885fb370d262a7e3ac125`。

### 原件案例

- Yin 原報表 1 的 0.20%、0.40% 與 1.00% TREO 截止品位各有獨立列與原印單位。0.20% 列為 `29.98 Mt`、`1.04%`、`311,800 t`。第 11、16 頁對應詳表的 `312,300 t` 有精度區間衝突，原件也未綁定估算有效日。Gifford Creek 及合計 `40.82 Mt` 是另有語境的報導，維持隔離。
- Wudinna 第 3 頁總計的 `470` 回連第 1 頁同名、同版、同原報礦量與品位的原文，保留 `ppm` 的單位定位。表題的 `200 ppm TREO-Ce` 只用於截止品位。
- Goschen 第 12 頁下方聲明定位到 `94,200,000 t`。`In-Situ TREO + Y2O3` 的 `0.08`、`74,000` 保留修飾分析物拒收理由。原分類的矛盾與現行正式研究結果均依原規則處理。
- Jupiter 的 2025、2026 父欄分開回讀。Aileron 的兩個同頁 cutoff 區塊各保留原印門檻。North Stanmore 的無編號表及已核准的 Census 用途維持原結果。Mt Weld 的日級封面證據由展示用途的同聲明連結另行驗收。

### 分層留出評估

| 樣本與層次 | 基準 TP / FP / FN / TN | 本次 TP / FP / FN / TN | 本次 precision / recall |
| --- | --- | --- | --- |
| Stage 3 歷史家族，自動定位 | 0 / 0 / 5 / 3 | 1 / 0 / 4 / 3 | 100% / 20% |
| Stage 3 歷史家族，提供定位欄義 | 8 / 1 / 6 / 20 | 8 / 1 / 6 / 20 | 88.89% / 57.14% |
| Stage 3 歷史家族，端到端欄義 | 0 / 0 / 14 / 20 | 2 / 0 / 12 / 20 | 100% / 14.29% |
| Stage 5 新的六個家族，自動定位 | 0 / 0 / 5 / 4 | 0 / 0 / 5 / 4 | `UNMEASURED` / 0% |
| Stage 5 新的六個家族，提供定位欄義 | 1 / 0 / 15 / 25 | 1 / 0 / 15 / 25 | 100% / 6.25% |
| Stage 5 新的六個家族，端到端欄義 | 0 / 0 / 16 / 25 | 0 / 0 / 16 / 25 | `UNMEASURED` / 0% |

兩組的盲評獨立凍結。同一規則的歷史樣本端到端辨識增加兩筆。新的家族泛化增益為 `HYPOTHESIS_NOT_SUPPORTED`。舊的「Million Tonnes 被讀為 tonnes」問題仍保留在提供定位的候選結果。正式採納的獨立 precision 與 recall 為 `UNMEASURED`。 [完整計數與獨立收據指紋](native_resource_scoped_binding_verification.json)

### 工程與科學回讀

| 同範圍測試 | 基準 | 本次 |
| --- | ---: | ---: |
| 僅重讀既有位置，15 PDF／1,292 頁，壁鐘時間中位數 | 5.861 s | 6.079 s |
| 同一位置範圍的 CPU 中位數 | 5.959 s | 6.184 s |
| 同一位置範圍的峰值 RSS | 46,980 KiB | 47,404 KiB |
| 完整定位及搜尋，34 PDF／2,698 頁，壁鐘時間中位數 | 20.026 s | 20.304 s |
| 完整定位及搜尋的峰值 RSS | 72,524 KiB | 75,180 KiB |

完整範圍兩邊均搜尋 1,406 頁，新版另核對先前收據及處置。六份具名原件的另測冷／暖快取中位數為 1.098／0.123 秒。暖快取省去六次原生解碼和 126 頁。作業系統檔案快取未清除。此路徑不存在 SQLite 查詢，OCR 未執行。

隔離的原生程式碼相依圖維持 245 靜態邊、相同循環見證與三組重複 AST。於相同定位工作到達的動態邊維持 16，AST Max/P90 為 44/17→48/17。實測呈現小幅成本增加。局部驗證 23 個合成及回歸測試、5 個原件測試通過。完整本機程序執行 367 個測試，其中 340 通過、27 個因缺可選 fixture 跳過。這份隔離提交未執行遠端 CI。

兩個獨立程序以 `PYTHONHASHSEED=71`、`98` 從同一固定 CAS 重建八件科學產物，SHA 全部相符。兩份 SQLite 的 `integrity_check` 為 `ok`，`foreign_key_check` 無違規。以 Stage 4 封存候選逐格核對 4,743 個鍵及 13 組科學輸出，維持 19 個科學格、42／27／21 筆候選／已驗證／選值、6 個可比較鍵、矩陣、權重、排名與敏感度。 [逐次驗證](native_resource_scoped_binding_verification.json)

```sh
python -m au_ree_v3 research bind-native-tables \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --inventory work/scientific-admission/inventory-verified-a/source_case_inventory.jsonl \
  --inventory-receipt work/scientific-admission/inventory-verified-a/source_case_inventory_receipt.json \
  --sources work/research-yield/source-inputs/source_manifest.json \
  --assets work/fresh/source-store \
  --prior-binding work/census-stage3/binding/verified/expanded-1 \
  --out work/census-stage5/native-binding/replay
```

同工作量的位置效能比較加 `--located-only` 並省略 `--prior-binding`。正式科學等價核對使用固定 `au_ree_census_display_v1.json`、`resource_bindings_v4.json` 和同一份原件清冊。
