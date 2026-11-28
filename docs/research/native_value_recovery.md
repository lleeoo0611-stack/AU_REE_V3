# 固定原件的 V2 證據引導尋回

以 PR #24 合併提交 a2e77572fe67d8421c217deeb2e32cb16b9a85d2 為基準，維持 V3 研究截日 2026-11-28（Asia/Taipei）。原型的固定 SHA 少了一個字元。修正後以真正的 V2 Git blob 核對 93×51 格與摘要。輸出的線索只含固定版本、Project、欄位角色及頁面資訊。V2 檔案與既有 V3 封存維持唯讀。

| 實測範圍 | 結果 |
| --- | ---: |
| 固定 V3 原件 SHA 讀回 | 144／144 |
| 歷史資源及氧化物定位提示 | 424 |
| 找到同 SHA 與 Project 的索引頁 | 389 |
| 缺相同 SHA 的提示／來源版本 | 35／4 |
| 實際重讀 PDF／專案／來源×專案 | 36／35／37 |
| 指定實體頁讀回 | 1,002／1,002 |
| 整份原件解碼實體頁 | 2,850 |
| 原表總計列／原生數字位置 | 47／298 |
| 既有 reviewed binding／重新讀出的量測原字串 | 6／33 |
| 33 筆既有科學結果 | 18 已驗證、9 證據不足、6 科學衝突 |
| 18 筆已驗證值的正式選值 | 12 已選、6 未選 |

47 筆工作項目列定位共涵蓋 38 個實體原件列。298 個原生數字位置保留來源 SHA、實體頁、行、欄及表頭。欄義、資源範圍、有效日、cutoff、材料和化學基準待跨欄綁定時，仍維持候選。19 個工作項目讀完指定頁，未找到符合本輪結構條件的總計列。36 份排定原件均可解析。召回率為 UNMEASURED。另以明確的欄位與計量單位人工審核 9 份 exact edition、7 案、38 個原文定位及 83 個位置級量測 token，與上表有交集。

回讀流程驗證佇列、清冊與固定 V2 來源摘要。索引頁只提供搜尋位置。實際讀取重新核對來源與 Project 關係、捕獲證據和內容 SHA，使用共用 DocumentCache 和 Resource Extraction。正式 Store、Temporal Gate、Census 和 MCDA 仍由既有研究建置治理。新尋回的資料維持 Candidate 或 Shadow。

## 原件證據與未採納原因

- Redlings 重現 11 Mt、1,130 ppm 與 12,430 t。原件日期可用於公告，估算有效日尚待同一估算證據。Inferred-only 未符合 R1。Mount Ridley 重現 122,546,251 t、889 ppm 及 108,954 t，日期角色和原表所列 Ce₂O₃ 基準需隔離。
- North Stanmore 的 MRE 資源量和品位可追溯，較新的直接含量欄出現在 Probable Ore Reserve。另一版的直接含量以 TREO＋Sc₂O₃ 報告。兩種範圍或化學基準均保留原文。
- Cummins 524 Mt、0.31% TREO 與 1.6 Mt 共有 P₂O₅ cutoff。Deep Leads 用 US$30/t 作主要 cutoff，約 350 ppm TREO-CeO₂ 為原件註記的近似值。
- Cowalinya 重現 158.9 Mt、870.3 ppm、138,290 t；採減 Ce cutoff、Inferred-only，而且發行人的 TREO 成員式重列 Er₂O₃。保留原件衝突。
- Nolans 的含量摘要和估算日角色還缺同版證據。Koppamurra、Jupiter 和 Salazar 的已核對資源行缺同範圍直接含量。Jupiter 的 Scoping Study cutoff 和主要資源 cutoff 已分開定位。
- 35 個缺件提示涵蓋 Wudinna、Splinter、Mt Stirling–Yttria 以及 Yttria 的歷史 exact edition。當前 Wudinna 的既有獨立 V3 證據與正式值照舊。

## 正式結果及重播

正式科學格 12→12，可比較 Project × Criterion 6→6（R1＝4、S1＝2），候選／驗證／選值 38／23／17 保持等價。凍結 R1＋S1 面板兩案的輸入、名次和敏感度一致，其他四個面板樣本不足。固定佇列、候選原文及路由收據重建時 SHA 一致。兩次 CAS 冷重播各匹配八個原件科學產物指紋。兩個研究候選也通過網頁匯出、驗證及 Chromium 版本切換。新研究候選 ID 為 dc5ba57911e6975a0dc60a35258fdcd3485db3fcd3dc2799915d54b7fcc9d19c。F5 維持 BLOCKED。完整固定原件測試執行 268 項，無跳過，全部通過。

## 重跑

先按 [PR #24 的發掘方法](legacy_value_recovery.md) 生成佇列。回讀使用同一份來源清冊、安全的 CAS 及新的輸出目錄。

```sh
QUEUE=work/census-completion/discovery
au-ree-v3 research route-source-work \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --queue "$QUEUE/source_project_work_queue.json" \
  --recovery-receipt "$QUEUE/legacy_value_recovery_receipt.json" \
  --sources work/research-yield/source-inputs/source_manifest.json \
  --assets work/fresh/source-store \
  --bindings config/research_profiles/resource_bindings_v3.json \
  --out work/census-completion/reread-new
```

[執行收據索引](native_value_recovery_verification.json)保留原件、路由、獨立複核、重播與網站的指紋。
