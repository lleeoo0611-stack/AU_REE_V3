# 官方點圖層擴張與產出情境覆核

研究截日為 2026-11-28，時區為 Asia/Taipei。固定來源清冊 SHA-256 為 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`。兩項作業獨立輸出影子證據。

## 固定 144 份原件

以兩份固定發行人 PDF 的原文、表格和註解交叉重讀三筆既有影子情境。獨立 Oracle 核對數字、角色、產品、材料、期間、發行日期和原件 SHA。

| 原件 | 角色 | 值及基準 | 原文期間證據 |
|---|---|---|---|
| Jupiter，2026-10-20 | `FORECAST` | MREC 內含 TREO 8,300 tpa | PDF p8 與 p21，包含爬坡及排程的全礦期年平均 |
| Jupiter，2026-10-20 | `ENGINEERING_PLAN` | MREC 內含 TREO 9,300 tpa | PDF p8 與 p21，8.0 Mtpa 名目給礦能力下的設計情境 |
| North Stanmore，2026-08-18 | `FORECAST` | 29,000 濕噸／年浮選精礦 | PDF p62 與 p66，PFS 基礎情境的年率，原件未獨立明示此數值的平均期間 |

Jupiter p21 的 201,400 t 是全礦期 MREC 產品總量。North Stanmore p143 明示該專案尚無產量。測試把 Jupiter 的 9,300 誤配為基礎情境時，Oracle 以原件欄位與期間證據拒收。三筆皆保留影子角色，本輪新增 Observation、正式 Census、選值及 MCDA 格數皆為 0。

## 另冊公開來源擴張

10 次匿名 HTTPS 請求取得 7 份通過原件 SHA 與截日前公開時間驗證的回應，其中 4 份含點圖徵，3 份是圖層描述。3 次 `f=pjson` 回應的 MIME 為純文字，保留拒收紀錄，改以官方 `f=json` 結果完成取得。擴張清冊 SHA-256 為 `226c08be42beea1f085760f3fc7a0c0647b77eeb1bd361cdc7414fd582efee23`。 [DataWA MINEDEX](https://catalogue.data.wa.gov.au/en/dataset/minedex-dmirs-001) · [Geoscience Australia 礦床資料](https://ecat.ga.gov.au/geonetwork/srv/api/records/807e40bb-406a-4b3e-92e0-f4524598c683) · [Geoscience Australia 關鍵礦床圖層](https://services.ga.gov.au/gis/rest/services/AustralianCriticalMineralsOperatingMinesAndDeposits/MapServer/7)

- 4 份回應解讀 91 列；其中 11 列接受影子覆核。WA CLD `S0001709` 屬官方 Mine Point，有 `J00772`、GDA2020（EPSG:7844）、Lynas 原件的 Mt Weld 礦坑及 CLD 文字錨點。西澳欄位 `point_conf=Public` 只記錄公開可見性。圖層抽取日與圖徵定位日期是不同證據。
- GA `ENO 763335` 報告精度 5 m、GDA94（EPSG:4283）與 `mineral deposit`。同一原件的 `OTHER_ID=S0237005` 在 WA 版次中的類型為 Mine。兩個政府角色各自保留，該交叉 ID 留有角色差異收據。
- 原件反例包括 Mt Weld 同名金礦 `S0012302`、同專案的道路 `S0021959` 及 Jupiter 的 Prospect `S0244130`。這三筆因身分、商品或用途不符而拒收礦址提案。其餘點仍欠測定日期、數值精度或足以登入固定 Project 的獨立連結證據。沒有核准代表點或正式 I1–I4 值。

## 重播及量測

合併作業以兩個獨立 Python 程序重播，三份影子收據逐位元組相同。兩次執行共用相同固定與擴張來源清冊。作業系統頁面快取未清除，各作業執行 0 條 SQLite 敘述。

| 工作量 | 兩次耗時（秒） | 峰值 Python RSS（KiB） | 每次 PDF 開啟／頁數 |
|---|---:|---:|---:|
| 固定原件 Forecast 及獨立 Oracle | 1.643／1.767 | 46,552／46,556 | 4／247 |
| 擴張清冊的 GIS 與固定原件 Project 連結 | 0.071／0.078 | 27,948／28,084 | 1／3 |
| 合併離線重播 | 1.645／1.596 | 46,984／47,004 | 5／250 |

37 項受影響測試全數通過。測試含更改政府回應原件、錯配產出量、不同 CRS、錯誤圖徵型態及停在分頁上限的負例。以上是本輪影子工作量的成本，歷史同功能比較維持 `UNMEASURED`。

```bash
python -m zipfile -e tests/fixtures/government_point_expansion_v1.zip work/official-point-inputs
PYTHONPATH=src python tools/verify_context_expansion.py \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --fixed-sources work/research-yield/source-inputs/source_manifest.json \
  --fixed-assets work/fresh/source-store \
  --expanded-sources work/official-point-inputs/source_manifest.json \
  --expanded-assets work/official-point-inputs \
  --point-bindings config/research_profiles/official_geography_bindings_v1.json \
  --forecast-bindings config/research_profiles/production_forecast_bindings_v1.json \
  --forecast-oracle tests/fixtures/production_forecast_context_oracle_v1.json \
  --out work/official-point-review
```

[機器驗證收據](government_point_expansion_and_forecast_context_receipt.json)
