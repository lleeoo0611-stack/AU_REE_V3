# 官方原生位置核對

截至 2026-11-28（Asia/Taipei），本輪在 93 案的 Stage 6 基準上重新核對四案官方原生位置及發行人範圍。新增正式座標 0、GIS 代表點 0、I1–I4 可比較鍵 0。基準 186 個緯度／經度格及 372 個 I1–I4 鍵皆為 `UNKNOWN`，候選 GIS 有 0 個要素。此次取得的證據保留在獨立探查清冊和離線收據，距離計算狀態為 `NOT_RUN`，原因為 `EVIDENCE_INSUFFICIENT`。

研究基準候選為 `8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3`，運算提交為 `a7efe276563ff18b8b15c7a8d0d660ec8f0d8445`。工程基準為 `aa92a35c3d5b3593c25b246edcd28ea36c4b0d9a`。獨立探查清冊與既有 147 版來源清冊維持各自的 SHA-256。V2 歷史地理投影只用於找到政府入口和原件線索。舊座標和推定質心不進入本次結果。[實測收據](native_government_location_verification.json)

| 案例與定位 | 已核對 | 仍缺的正式用途證據 |
| --- | --- | --- |
| Koppamurra，SARIG 11871 | Deposit，圖徵有 GDA2020 地理軸與 MGA Zone 54，原始 `Horizontal Accuracy` 詞元為 `20`，定點方法為 Google Earth image。發行人 PFS 物理第 6 頁提供專案名稱與區域脈絡。 | 確切版次截日前公開證據、圖徵適用日、精度單位及代表性範圍。原件的 2024-09-30 屬資源估算日期。 |
| Franklyn，SARIG 10098 | Prospect，圖徵有同種 CRS 與原始精度詞元 `50`。iTech 原件物理第 2 頁明示 Franklyn Prospect 位於 EL 6160。 | Prospect 的原生角色不符合既有礦址／礦床代表點用途。圖徵適用日與精度單位也未揭露。 |
| Nolans，NTGS MODAT 2641 | 原生 CSV 實體第 3447 行，`Deposit`、MGA Zone 53、軸序 Easting／Northing，`LOC_ACCURY` 為 `100 to 200`。Stage 6 Arafura 同版物理第 17 頁明示 Nolans Bore deposit 與 EL 28473。 | CSV 沒有明示該投影座標的 datum、精度單位或適用日期。發行人原件物理第 14 頁的 MGA94 說明適用於發行人自己的測量資料。 |
| Dubbo，NSW Resources Toongi | 官方網頁把 Dubbo Project 定位至 Toongi 地區。Stage 6 發行人原件物理第 24 頁獨立確認 Toongi deposit。 | 政府網頁範圍是區域敘述，沒有原生點、CRS、準確度、適用日或代表性。 |

以 GDAL 3.10.3 獨立把兩個 SARIG 原生 MGA 點按 `EPSG:7854 → EPSG:7844` 轉換，明定 Easting／Northing 輸入及 Longitude／Latitude 輸出。四個軸與資料原報十進位值的最大絕對差為 `6.23e-8°`。這是兩組座標表達的數值一致性。原始小數位數及此差值各自存證，兩者都無法填補原始資料的水平準確度。NTGS 歷史詞彙資料列了 GDA94 及 AGD66，實際 CSV 缺少同版明示，因此本輪保留原生數值及分區，未進行 datum 轉換。[SARIG 11871](https://minerals.sarig.sa.gov.au/MineralDepositDetails.aspx?DEPOSIT_NO=11871) · [SARIG 10098](https://minerals.sarig.sa.gov.au/MineralDepositDetails.aspx?DEPOSIT_NO=10098) · [NTGS MODAT 中繼資料](https://www.ntlis.nt.gov.au/metadata/export_data?metadata_id=2DBCB771209106B6E040CD9B0F274EFE&type=html) · [NSW Resources](https://www.resources.nsw.gov.au/invest-nsw/investment-pathways/nsw-priority-metals/rare-earth-elements)

八個匿名官方入口涵蓋五案，六個 HTTP 200 原件已按 SHA 和公開取得收據保存。所有六個實際取得事件皆為 2026-11-29 Taipei，現有時間 Gate 回傳 `CANDIDATE / PRE_CUTOFF_PUBLIC_EVIDENCE_MISSING`。資料服務入口另提供 SARIG 的 CC BY 4.0 說明。SARIG WFS 取得 `HTTP_404`，GSSA Report Book PDF 的代理連線回傳 403，未取得該官方 PDF。第六份取得原件為 GA 歷史 Nolans 地質文獻，保留在探查清冊，未作座標採納。既有 11 筆 WA／GA 查核（8 個候選、3 個非常用角色對照）的同輸入結果與主線逐位元相符。[SARIG 資料服務](https://energymining.sa.gov.au/industry/geological-survey/products-and-services/map-viewers-databases-and-services/data-services)

## 重播與工程

`geography review-native` 只讀兩份釘選清冊和 CAS。重用 `DocumentCache` 的 HTML 與 CSV 原生定位、PDF 原句錨點、Temporal Gate 和 `geography.infrastructure_site_compatibility`。新責任是識別 SARIG 頁面、MODAT 原生 ID 和 NSW 的區域性敘述，並保留各自的角色、原始投影及未填的準確度欄位。CLI 及角色表沿用既有位置。風險 `medium`，因本輪新增候選資料解析，正式投影、判準與選值路徑仍沿用基準。

一份正式候選的兩個既有本機 CAS 加上兩份獨立探查 CAS，使用 `PYTHONHASHSEED=17` 及 `271` 運行離線讀回。兩個收據與初次執行逐位元相同。兩次 wall time 0.587／0.547 秒，CPU 0.589／0.554 秒，峰值 RSS 36,356／36,336 KiB。實際網路探查 wall 5.445 秒，CPU 0.350 秒，RSS 30,944 KiB。檔案系統頁面快取未控制。新模組無額外第三方依賴，五個內部匯入有向邊無新增循環。兩個原有地理模組的函式 AST 分支上限維持 17／34，新模組上限 10。本輪合併原生點角色詞彙的一份共用映射。正式新增值成本分母為零，狀態 `UNMEASURED`。

```bash
work=/workspace/AU_REE_V3/work
probe="$work/census-fill/geography"
base="$work/census-stage6"
export PYTHONPATH=src

python -m au_ree_v3 geography review-native \
  --bindings config/research_profiles/native_geography_bindings_v1.json \
  --baseline-manifest "$base/additional-sources/combined/source_manifest.json" \
  --baseline-assets "$base/verified/cas" \
  --probe-manifest "$probe/acquisition/source_manifest.json" \
  --probe-assets "$probe/acquisition-cas" \
  --out "$probe/review-new.json"

AU_REE_GEO_BASE_MANIFEST="$base/additional-sources/combined/source_manifest.json" \
AU_REE_GEO_BASE_ASSETS="$base/verified/cas" \
AU_REE_GEO_PROBE_MANIFEST="$probe/acquisition/source_manifest.json" \
AU_REE_GEO_PROBE_ASSETS="$probe/acquisition-cas" \
AU_REE_FULL_SOURCE_MANIFEST="$work/research-yield/source-inputs/source_manifest.json" \
AU_REE_FRESH_ASSETS="$work/fresh/source-store" \
python -m unittest discover -s tests -p 'test*geography.py' -v
```

可用 `native_geography_source_seeds_v1.json` 作獨立來源設定的 `source_seed_catalog`，同步填寫 SHA 後，以 `sources acquire --per-project-limit 10 --workers 4 --timeout 24` 重新探查。服務重刊取得的新 SHA 和擷取時間需建立新清冊。重播此結果應使用探查 CAS 的準確位元與既有收據。

| 檔案 | 處置 |
| --- | --- |
| `native_geography.py`、`test_native_geography.py` | 新增官方入口讀回及正反例測試。 |
| `native_geography_bindings_v1.json`、`native_geography_source_seeds_v1.json` | 新增獨立探查與讀回版次設定。 |
| `cli.py`、`geography.py`、`official_geography.py` | `REFACTOR`，新增調度，合併原生角色映射，回歸原 WA／GA 功能。 |
| `native_government_location_review.md`、`native_government_location_verification.json` | 新增可重跑方法和實測指紋。 |

大型原件與候選 JSON 留在工作區 CAS。PR 與 CI 由整合流程驗收。本工作包未重跑正式網站切換與候選發行 Gate，Stage 6 的歷史 F0–F4 `PASS`、F5 `BLOCKED` 及 O2 `FAIL` 以原收據為準。
