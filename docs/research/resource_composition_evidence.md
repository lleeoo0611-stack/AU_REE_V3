# 同一資源聲明的磁性氧化物證據

三案獨立原件查驗重讀固定清冊中的五份 PDF，以及另行匿名擷取的四份 PDF。九份原件各以 SHA、公開擷取收據、Project ID 和頁內定位核對，共解碼 383 頁。查驗以 Stage 5 封存候選 `bdb9e868e5cb2a514e608231db35cdb27c3e77b2fc9427f7dbb444409f511c78`、固定 144-SHA 清冊及原有 S1 政策為基準。三案新增正式 S1 鍵與完整 R1＋S1 個案皆為 0，增益假說為 `HYPOTHESIS_NOT_SUPPORTED`。此結果適用於這三個已知案例，泛化精度與召回為 `UNMEASURED`。 [固定原件核對表](../../tests/fixtures/resource_composition_review_v1.json)

| 原件範圍 | 原始證據與限制 | 仍待補的證據 |
| --- | --- | --- |
| Mt Weld，2024-06-30 原位資源，2.5% TREO | 物理第 2 頁資源為 106.6 Mt／4.1%／4,389 kt TREO。第 10 頁只提供 Dy＋Tb 29,800 t；第 23 頁的四種單體為 32.0 Mt、2.8% TREO 截品的 Ore Reserve。第三方比較簡報的換算值僅作尋回線索。 | 同一原位資源估算的四種全礦氧化物或明示四種的總量、尺度與 TREO 分母。 |
| Goschen Area 1，2025-10-15，1% THM | 物理第 12 頁報 94.2 Mt／0.08%／74,000 t 的原位整體 TREO＋Y₂O₃。相鄰氧化物列按 THM 計量，Pr₆O₁₁／Nd₂O₃／Dy₂O₃／Tb₄O₇ 為 0.12／0.43／0.07／0.01%。同頁表列與第 9 頁分類用語仍有差異。後續固定原件第 23、24 頁維持 THM 角色，另外報導的年產值則屬濃縮產品預測。 | 發行方核對分類後的同估算全礦四種氧化物、完整 TREO 組成與單位。 |
| Wolverine，2025-01-15，0.15% TREO | Table 2、Table 5 及後續發行方重刊均限定同日期與 cutoff。兩種氧化物名義精度衝突重現。 | 說明資源表之間有效位數、精度與修訂關係的發行方正式勘誤，或可驗證的原始完整資源表。 |

[Mt Weld 2024 發行方原件](https://wcsecure.weblink.com.au/pdf/LYC/02835257.pdf) · [Goschen 2025 發行方原件](https://announcements.asx.com.au/asxpdf/20251015/pdf/06qkvvhp20rxx2.pdf) · [Goschen 2026 發行方簡報](https://announcements.asx.com.au/asxpdf/20261019/pdf/075h3jfgqhmlkb.pdf) · [Wolverine 2025 發行方原件](https://announcements.asx.com.au/asxpdf/20250116/pdf/06dl60mwyl4f8n.pdf)

## Wolverine 的版次與精度

| 來源及相同資源角色 | Dy₂O₃ 原報及名義 ppm 區間 | Y₂O₃ 原報及名義 ppm 區間 |
| --- | --- | --- |
| 2025 年 1 月 MRE 第 5 頁 Table 2 Wolverine Total | 0.85 kg/t，845–855 | 5.68 kg/t，5,675–5,685 |
| 同版第 6 頁 Table 5 Wolverine Total | 840 ppm，839.5–840.5 | 5,700 ppm，5,699.5–5,700.5 |
| 2025 年 9 月 DFS 第 13 頁 Table 5 Wolverine Subtotal | 0.84 kg/t，835–845 | 5.66 kg/t，5,655–5,665 |
| 2025 年 10 月簡報第 37 頁 Wolverine Subtotal | 0.84 kg/t，835–845 | 5.66 kg/t，5,655–5,665 |

兩份重刊的 Wolverine-only 列均有 7.3 Mt／0.96%／70,500 t，且引用 1 月原件。10 月簡報第 36 頁將 0.01 kg/t 四捨五入明確限定於 Ore Reserve／Production Target。第 37 頁的資源表只說四捨五入可能造成差異。重刊的 Dy 區間涵蓋 840 ppm，原表的衝突仍在。Y 的三個不同名義區間互斥，Dy 再利用與 Y 繼續隔離。名義區間採印刷末位，用於驗證刊印精度。獨立 Decimal 重新算得凍結 Wolverine S1 為 1,300／9,600 × 100＝13.541666…%，Wudinna 為 112／470 × 100＝23.829787…%。已逐一核對相關輸入、時間決策、SQLite 正式選值與 Wudinna 原 ppm 影像指紋。 [9 月 DFS](https://announcements.asx.com.au/asxpdf/20250915/pdf/06p6zt0hxtpg01.pdf) · [10 月發行方簡報](https://invest.northernminerals.com.au/announcements/3jY4R3-equity-raising-presentation-27-october-2025)

## 獨立擴充清冊

四份公開原件均在截日內匿名取得。擴充清冊 SHA 為 `c594129be54b565fdf2b6aec85b23ab72234f63ff0cfff2df96da1dffdaacfc8`，離線重新驗證通過。Mt Weld 年報物理第 120、136 頁各為 2025-06-30 和 2026-06-30 的原位估算，分別報 106.0 Mt／4.09%／4,337 kt 及 105.2 Mt／4.1%／4,287 kt。兩版另列含庫存及尾礦的範圍，仍缺 S1 所需的全礦分子。VHM 半年報第 12 頁只列全公司 THM 組成。Northern Minerals 10 月簡報保留為重刊佐證。四份版次均留在獨立擴充清冊，正式資料與研究母體沒有變動。 [Mt Weld 2025 年報](https://announcements.asx.com.au/asxpdf/20250828/pdf/06ngtkbmf2fb5g.pdf) · [Mt Weld 2026 年報](https://announcements.asx.com.au/asxpdf/20260826/pdf/0737nx07s3tnf7.pdf) · [VHM 半年報](https://announcements.asx.com.au/asxpdf/20260304/pdf/06x1tj0ylqrm7t.pdf)

## 離線重查

以下兩個命令在固定工作區執行通過。收據位於 `work/census-stage6/s1/verified/resource_composition_audit.17.json`。新工具重用既有 CAS、PDF 定位、時間決策及唯讀 SQLite 驗證。

```sh
export PYTHONPATH=src
root=/workspace/AU_REE_V3/work
review="$root/census-stage6/s1"
profile=config/research_profiles/au_ree_2026-11-28.json
manifest="$root/research-yield/source-inputs/source_manifest.json"

python tools/recover_source_editions.py --verify --profile-path "$profile" \
  --fixed-manifest "$manifest" --bindings config/research_profiles/composition_source_editions_v1.json \
  --store "$review/expanded-cas" --out "$review/expanded-receipts"

python tools/audit_resource_composition.py --profile "$profile" --fixed "$manifest" \
  --assets "$root/fresh/source-store" --expanded "$review/expanded-receipts/source_manifest.json" \
  --expanded-assets "$review/expanded-cas" --archive "$root/census-stage5/final" \
  --out "$review/resource_composition_audit.replayed.json"
```
