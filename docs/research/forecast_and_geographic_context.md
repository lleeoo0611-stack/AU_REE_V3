# 政府地理圖徵與研究產出預測

本輪使用 144 個唯一來源 SHA 的固定清冊，研究截日為 2026-11-28，時區為 Asia/Taipei。獨立重播以主線 `074e00daf69360cd71cab2554f651b4b6e76a668` 為基準。驗證收據位於 `forecast_and_geographic_context_receipt.json`。

## 政府圖徵

以既有 GIS 程式和 GDAL 讀取西澳兩份政府礦權快照。七個圖徵重現法定邊界，五個礦權關聯仍是候選。每列保存來源 URL 與 SHA、Project 關聯、CRS、原生 `TENID` 定位與已知精度。定位精度未公布時保留空值。

以 EPSG:4283 與 EPSG:7844 各一個實際礦權多邊形建立質心反例。兩個原件衍生點都因圖徵用途被拒於礦址點以外。兩份政府圖層的已驗證代表性礦址或礦床點為 0。93 案的 372 個 I1–I4 用途判斷維持證據不足；礦權的有效性不提供道路、鐵路、電網或港口距離。

## 產出情境

| Project／試驗 | 原文頁 | 角色 | 原文量別 |
|---|---:|---|---|
| Jupiter Scoping Study 基礎情境 | 8 | `FORECAST` | 約 8,300 tpa，為 MREC 內含 TREO 的礦山生命週期年平均 |
| Jupiter Scoping Study 名目處理能力 | 8 | `ENGINEERING_PLAN` | 約 9,300 tpa，為 MREC 內含 TREO 的設計年產 |
| North Stanmore PFS 基礎情境 | 62 | `FORECAST` | 約 29,000 濕噸／年重稀土富集浮選精礦 |

三筆影子觀測分別保存產品、原文 token／單位、研究情境、發布日期、原件 SHA、精確原文錨點及時間證據。原文「approximately」隨觀測保存。正式 Census、選值及 MCDA 增量皆為 0。

Jupiter 原件第 21 頁的 201,400 t 是全礦期 MREC 產出量，年產 TREO 驗證拒收該列。試驗也用原件清冊同時關聯的 Greater Brothers Project ID 重新提交 Jupiter 數值，原件命名範圍檢查拒收。實測欄位的既有輸入檢查拒絕 `FORECAST` 與 `ENGINEERING_PLAN`。

## 重跑

兩個獨立 Python 程序產出的五個機器可讀檔逐位元組相符。兩次耗時 2.551 s 與 2.478 s，Python 峰值 RSS 分別為 46,304 KiB 與 46,288 KiB，解析子程序峰值均為 63,488 KiB。每次解碼 6 份 PDF、174 頁，開啟 2 層政府圖徵並執行 2 次質心反例。作業系統頁面快取未清除。影子試驗使用獨立檔案，無 SQLite 查詢或寫入。受影響測試執行 32 項，全部通過。

```bash
PYTHONPATH=src python tools/review_context_shadows.py \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --sources work/research-yield/source-inputs/source_manifest.json \
  --assets work/fresh/source-store \
  --geography-bindings config/research_profiles/wa_tenement_bindings_v1.json \
  --forecast-bindings config/research_profiles/production_forecast_bindings_v1.json \
  --out work/context-shadows
```
