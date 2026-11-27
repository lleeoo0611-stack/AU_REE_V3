# B · 新原件觀測與准入

新資料來自 V3 實際取得的原始 bytes、核對後的同 SHA 公開取得收據，以及版面和科學範圍規則。來源完整性、時間、材料及化學相容性、Scope 和選值各有紀錄。不同礦體、JORC 類別、估算及 cutoff 的觀測可以共存。缺少優先規則的競爭聲明保留衝突，合規的同一來源值按現有規則自動選取。

F2 在三種鎖定 PDF 版面 **PASS**。獨立測試對照原件的頁碼、列、原始 token、量值、時間證據與選值。

| 原件 | 可核驗結果 |
|---|---|
| Mt Weld 2024 | 陳述與 Total 表互證，選取 106.6 Mt、4.12% TREO、4,389 kt TREO。 |
| Wolverine 2025 | 分類列與 Total 表，保留六筆次範圍觀測供查閱，選取三筆核准總值。 |
| Mangaroon–Yin | 比對三組 cutoff 表與後頁詳細表。同 scope 的礦量、品位與不同含量記號留作候選及衝突收據。PDF 沒有可核對的估算有效日。 |

實際三原件得到 24 筆候選、12 筆通過觀測驗證和 6 筆正式選值。原件活體取得所記錄的時間按 Asia/Taipei 研究日判定。相關科學輸出來自此輪 PDF 重新擷取。終版研究發布由 D 驗收。

共用文件快取提供具 SHA 與實體頁定位的原生 PDF，以及具 HTML 區塊或 CSV 記錄定位的檢視。可配置 OCR 會固定引擎與語言模型 SHA，所得片段維持候選。真正可自動進入科學流程的解析範圍為上述三種原生 PDF 版面。已另以第一輪 LSE HTML、Mt Weld PDF 及固定政府 CSV 做檢視冒煙測試，冷／暖快取分別為 6.30／0.30 ms、52.46／2.29 ms、113.51／3.50 ms。輸入規模及解析方式不同，各數字只描述其自身工作負載。

B 新增版本化資源綁定、`resource_extraction.py` 與 `fresh_observations.py`、文件與觀測測試、鎖定的原始取得及獨立預期收據。修改 `documents.py`、CLI、README、CI 與能力進度。舊 Golden 預期值與 V2 均維持不變。從 [README](../../README.md) 執行 `research extract` 重現。通用技術揭露、任意版面 OCR 正式准入和政府 GIS 投影仍待驗證。

B 經 [PR #7](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/7) 整合為 `e0c006d73a3ac8d0e12cec5c92020d3c900e9c38`，測試 head `583da0e21e2a6b1464f25386c8b8354beb9c134c` 的 [CI](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46071615384) 通過，含三份同 SHA 原件。
