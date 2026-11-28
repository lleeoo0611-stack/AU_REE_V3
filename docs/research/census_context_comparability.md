# 座標與產量的正式比較資格

本輪重讀兩套獨立來源清冊。固定 144 個來源內的兩份發行人 PDF 支持三個工程情境。獨立的七份政府回應含四份點位查詢及 91 筆原生列，支持八筆 SHADOW 地理候選。本項正式值、獨立 Observation、可比較 Project×criterion、完整面板的淨增均為 **0**。

## 官方地理

獨立讀回直接從 CAS 驗證原始回應及擷取收據 SHA，解析 ArcGIS／WFS 原生欄位，核對 feature ID、角色、座標軸順序、CRS、datum、米制精度及研究時區內的公開擷取日。由另一個 Poppler 程序額外重讀 Mt Weld 發行人原件第 3 頁，確認 CLD 的具名礦床關聯。

| 原生記錄 | CRS／datum | 准入判定 |
| --- | --- | --- |
| WA S0001709 Central Lanthanide | EPSG:7844／GDA2020 | 原件確認 CLD 具名子址。缺少米制精度與點位測量／適用日期。 |
| WA S0237005 Mt Weld REE Resource Group | EPSG:7844／GDA2020 | 缺少獨立具名礦址連結、米制精度與點位日期。 |
| GA ENO=763335 Mt Weld REE Resource Group | EPSG:4283／GDA94 | 記有 5 m 精度並引用 S0237005。GA 標記為 mineral deposit，WA 標記為 Mine。角色衝突與適用日期缺口阻止兩者作同一代表點。 |
| WA S0240894 North Stanmore、S0242502 Jupiter | EPSG:7844／GDA2020 | 原生礦床點及專案代碼可讀。缺少發行人對該點的連結、米制精度及點位日期。 |
| GA 2419 Aileron、2282 WIM 150、2303 Copi | EPSG:4283／GDA94 | 原生礦床點可讀。缺少專案對接、米制精度及點位日期。 |

三個拒收控制為 Mt Weld 同名金礦、Mt Weld 道路、Jupiter prospect。固定來源的合法礦權仍保留原生邊界角色。WA 的 extract_da 表示資料擷取，GA Critical Minerals 的 2025 年 12 月敘述屬圖層時間。兩者皆缺少對該點的測量或適用日期。新檢查要求點位日期與同來源的 HISTORICAL_MEASUREMENT Temporal Decision 一致，保留原件的年、月或日精度。

I1–I4 距離為 NOT_RUN。本批缺少可驗證的代表起點，現行比較器亦將 I1–I4 回傳 CRITERION_ADMISSION_ADAPTER_NOT_MIGRATED。研究輸入的 372 個 I1–I4 位置均維持未知。後續距離研究需要同研究設定下的代表起點及具明確道路、鐵路、電網或港口角色的官方端點。

公開 GA Operating Mines 圖層可供位置查找，其可讀欄位缺少每筆位置精度與測量日。GA 1:250,000 地形礦點圖層另外提供圖徵可靠日期與平面精度欄。WA 核准圖說提供 Mt Weld 廠區及礦坑位置關係。圖面角色與現行代表點用途各自保存。 [GA Operating Mines](https://services.ga.gov.au/gis/rest/services/AustralianOperatingMines/MapServer/0) · [GA 地形礦點](https://services.ga.gov.au/gis/rest/services/Topographic_Base_Map/MapServer/238) · [WA Mt Weld 核准圖說](https://www.der.wa.gov.au/our-work/licences-and-works-approvals/lwa-applications/item/download/17568_ad177f8c002f41ee0fc4de6114e3bd79)

另冊完成一次 Mt Weld 名稱查詢及六個約 ±0.02° 的專案空間窗查詢。七個匿名公開擷取包含兩個不同 SHA。僅 Mt Weld 空間窗回傳一筆 UFI BB11701602，原名留白，備註為 diggings，可靠日期為 1983-01-01，平面精度 100 m，回應 CRS 為 EPSG:3857。該點缺少 Project／CLD 對接證據，維持查找線索。其餘六份回應無符合查詢的原生列。另冊的來源清冊 SHA-256 為 ad2292df954252a7107e22b964bada570eada306e74445587d94998a6a645fdb，與固定及八筆 SHADOW 候選清冊分開。

## 工程情境與 MCDA

| 原件 | 數值及角色 | 正式用途缺口 |
| --- | --- | --- |
| Jupiter，第 8、21 頁 | 8,300 tpa，MREC 內含 TREO。含爬坡及排程影響的 LOM 年均預測。 | 缺少實際營運年度的觀測。 |
| Jupiter，第 8、21 頁 | 9,300 tpa，MREC 內含 TREO。名目 8.0 Mtpa 處理規模的工程方案。 | 名目設計與預測期間不同。缺少實際營運觀測。 |
| North Stanmore，第 62、66、143 頁 | 29,000 wet tpa，PFS 基準情境濕重浮選精礦。 | 年均區間未交代，缺少同一產品的 TREO 含量與換算依據。原件明示專案尚無生產。 |
| Jupiter，第 21 頁 | 201,400 t，LOM 的 MREC 總產品量。 | 期間及產品基準皆不符合年度內含 TREO 指標。 |

在相同來源、profile、選值規則與方法下，比對全部八件研究產物 SHA，涵蓋治理選值、MCDA 輸入、面板矩陣、權重、SAW／TOPSIS／PROMETHEE II 結果及敏感度。R1 四鍵、S1 兩鍵及兩個完整 R1+S1 專案維持一致。其餘面板樣本不足，狀態維持 INSUFFICIENT_COHORT。

## 重播

```bash
PYTHONPATH=src python tools/verify_context_expansion.py \
  --profile config/research_profiles/au_ree_census_display_v1.json \
  --fixed-sources "$fixed_manifest" --fixed-assets "$fixed_cas" \
  --expanded-sources "$geo_manifest" --expanded-assets "$geo_cas" \
  --point-bindings config/research_profiles/official_geography_bindings_v1.json \
  --forecast-bindings config/research_profiles/production_forecast_bindings_v1.json \
  --forecast-oracle tests/fixtures/production_forecast_context_oracle_v1.json \
  --baseline-build "$frozen_build" --reviewed-build "$same_input_build" \
  --out "$context_receipts"
```

geo_manifest 與 geo_cas 可使用 tests/fixtures/government_point_expansion_v1.zip 解壓後的資料。收據 context_comparability.json 保留兩次建置的指紋、逐案原始欄位、拒收原因與範圍內 MCDA 的零差異。工作區實跑產物置於 work/census-stage5/context/。

可追加 `--topography-sources "$probe_manifest" --topography-assets "$probe_cas"`，從另冊的原始回應重播地形礦點查詢及零採納收據。

## 檔案清冊

| 檔案 | 處置 |
| --- | --- |
| src/au_ree_v3/geography.py | 修改。以現行 Temporal Gate 的日期精度核對點位有效日期及歷史測量角色。 |
| tools/verify_context_expansion.py | 修改。沿用現有重播入口，新增原生政府回應獨立讀回與同設定科學比較。 |
| tests/test_context_expansion.py | 修改。驗證固定候選、拒收控制及 CRS、角色、精度、日期變更反例。 |
| tests/test_infrastructure_compatibility.py | 修改。涵蓋公開文件日、跨截日精度與同一原件的點位測量日。 |
| docs/research/census_context_comparability.md | 新增。記錄範圍、來源缺口及重播方式。 |
