# R1 跨發行方原件與精度驗證

以 Stage 5 `c8bce8a893358a63bf2c587e44e0bc3f84523db7` 為起點，讀回固定 144 份原件與封存正式 MCDA 輸入。截日為 2026-11-28 Asia/Taipei。R1 沿用 `fresh-decision-policy-v1`，政策 SHA-256 為 `b8dd516b6f6a765f3a656630d91315061ff0a27bf2c57bef41c92a2dc80abf19`。查核按 Project、版次、物理頁、資源聲明、截止品位、量測欄與原印單位逐筆綁定。

| 候選 | 原件結論 | 正式用途障礙 |
| --- | --- | --- |
| Mangaroon–Yin `AU-8f3fe174a998d20ee4cf` | 2023-11-30 申報原件第 1、11 頁均列同一個 `0.20% TREO` scope 的 `29.98 Mt`、`1.04% TREO`，兩列直接含量分別是 `311,800 t` 與 `312,300 t`。第 50 頁證實乾基。2026-11-27 AGM 第 31 頁重刊 `312.3 kt` 並引用同一原申報。 | `SCIENTIFIC_CONFLICT`。依原表百噸顯示採 ±50 t 敏感度，兩直接含量區間互斥 `[311.750, 311.850]` 與 `[312.250, 312.350]` kt。兩者皆落在礦量與品位乘積的 `[310.241250, 313.343250]` kt 區間，乘積無法裁定版本。仍需原估算的有效日證據及可查核的正式釐清。 [原申報](https://dreadnoughtresources.com.au/announcements/5449464) · [AGM](https://dreadnoughtresources.com.au/announcements/3DxkA6-agm-presentation) |
| Yangibana `AU-908fd6f4461622e89548` | 2022 原件第 2 頁總列為 `29.93 Mt`、`0.93% TREO`、`276,955 t TREO`，註腳分列 `0.24% TREO` 與 `0.20% NdPr` 截品。2023 年申報第 3 頁確認估算基準為 2022-10-11。2026 修訂 DFS 第 32–33 頁分別列出六個主要礦床 `28.12 Mt` 和其他四個 `1.82 Mt`。 | `SCIENTIFIC_CONFLICT`。全案列涵蓋兩種 cutoff。2026 DFS 第 7 頁重刊同一總列，將表標示為 Ore Reserves，並把 Inferred 直接含量由 `57,298 t` 改列 `51,298 t`。該差異遠超兩原印整噸區間。核對特定單一 cutoff 新聲明須同時取得獨立同列含量及完整對應 scope。 [2022 原件](https://announcements.asx.com.au/asxpdf/20221011/pdf/45g43n16ywv3wf.pdf) · [2023 原件](https://announcements.asx.com.au/asxpdf/20230206/pdf/45l9yjck4pk40g.pdf) · [2026 DFS](https://announcements.asx.com.au/asxpdf/20260713/pdf/071n8v54p9pdql.pdf) |
| North Stanmore `AU-216d0983f6892f9b58f9` | 2026 年 8 月原件第 97 頁 Mineral Resource 為 `278.80 Mt`、`482.64 ppm TREO`。第 9 頁 `47.0 Mt`、`692 ppm`、`32.6 kt` 屬另外的 Probable Ore Reserve。兩段使用 `300 ppm TREO`。 | `EVIDENCE_INSUFFICIENT`。32.6 kt 在 Reserve 的原印精度內，R1 所需 Mineral Resource 直接含量仍缺。 [ASX 原件](https://announcements.asx.com.au/asxpdf/20260818/pdf/072w8qbl7kfy0n.pdf) |

兩個相互獨立的擴充清冊合計 5 次公開請求及 5 張合格收據。Yin 原申報的 bytes 已在固定清冊，重取沿用作交叉核對。其餘 4 個 SHA 是新的原件版次。三個 Project 的本輪正式 R1 增益為 0，完整 R1＋S1 個案增益為 0。此工作包的獨立 R1 增益假說結果為 `HYPOTHESIS_NOT_SUPPORTED`。三個案例均屬已知挑戰案例，正式誤收為 0，未見家族泛化精度與召回為 `UNMEASURED`。

離線重播校驗 6 份原件的 233 頁及實際 MCDA 輸入血緣。更換 `PYTHONHASHSEED` 後收據逐位元一致，SHA-256 為 `398df6e6f8524ca96198e080b643708e8906a66289341add98ec6712b44de0e7`。本次重播 wall 1.535 秒，CPU 1.593 秒，Python 峰值 RSS 55,024 KiB。兩項本地條件測試通過，包含錯原值、錯單位及篡改正式 R1 血緣。既有四案原件挑戰另行重播通過。[機器收據](resource_comparability_challenge_verification.json)

## 執行

```bash
PYTHONPATH=src python tools/audit_resource_statement_holds.py \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --manifest "$FIXED_SOURCE_MANIFEST" --assets "$FIXED_CAS" \
  --build "$FIXED_SCIENTIFIC_BUILD" \
  --challenge config/research_profiles/resource_comparability_challenge_v1.json \
  --annex yin "$YIN_EXPANSION_MANIFEST" "$YIN_EXPANSION_CAS" \
  --annex yangibana "$YANGIBANA_EXPANSION_MANIFEST" "$YANGIBANA_EXPANSION_CAS" \
  --out "$NEW_CHALLENGE_RECEIPT"
```

既有 `tools/recover_source_editions.py --verify` 可用本輪兩份 `*_resource_source_routes_v1.json` 查驗各擴充清冊。擷取原件與完整收據保存在 `work/census-stage6/r1/`，正式基準、歷史發布與選值規則維持固定指紋。
