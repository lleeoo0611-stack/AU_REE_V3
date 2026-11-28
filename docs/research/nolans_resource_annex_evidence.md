# Nolans Bore 原報資源與 JORC 附錄證據

2017-06-07 Arafura 發行方原件 `5f72a08840640987e8d619c6d55ca047a2fe26c0194ffd1a46521f76eda7b06f` 的物理第 2 頁 Figure 1 明列同日、1% TREO cutoff 的 Nolans Bore Measured＋Indicated＋Inferred 資源總列 56 Mt、2.6% TREO。該頁引用附錄 JORC Table 1。附錄第 4 頁標題具同礦床及同估算日，第 14 頁定義含 Y₂O₃ 的 15 種氧化物，第 27 頁明示乾噸。第 31 頁再次明示礦床。本次共用規則要求引用、原表和附錄標題確切相符，拒絕日期不符、引用錯置及中途更換附錄角色。 [ASX 原件](https://announcements.asx.com.au/asxpdf/20170607/pdf/43jsp1xldvw6rk.pdf)

同一案例新增兩個正式直接 Census 選值及依核准公式計算的 **1,456 kt** 衍生格。原報精度支持的區間為 **1,415.25–1,497.25 kt**。2017 原表無原報含量，故正式 R1 卡在 `INCOMPLETE_SELECTED_RESOURCE_BUNDLE`。同表 26.4% 是 Nd₂O₃＋Pr₆O₁₁ 的 TREO 占比，維持原用途。S1 仍要求合格 R1 與四種磁性氧化物的同聲明證據。

三個拒收對照均保留原件。2015 原件 `4cfc91f4…` 的同一總列明列 1,462,000 t，但其第 1 頁與第 7 頁附錄標題分別標示 2015-10-30 與 2015-10-29，原報資訊以獨立版次保留待核。固定 2019 DFS `63bc0386…` 不借至 2017 原表補列。Dubbo 2018 `2ffb042e…` 及 2025 `734d9ea3…` 列示 75.18 Mt，TREO 原始分母排除 Y₂O₃。獨立量測欄及工程研究不跨用途合併。 [2015 Nolans](https://announcements.asx.com.au/asxpdf/20151030/pdf/432kn4l4vvx4n8.pdf) · [2018 Dubbo](https://announcements.asx.com.au/asxpdf/20181025/pdf/43zmg28pb600f1.pdf) · [2025 Dubbo](https://announcements.asx.com.au/asxpdf/20250711/pdf/06lq7vsvvk6yms.pdf)

四個公開 HTTPS 路徑均取得原件，新增三個唯一 SHA，另有一份為固定清冊既有 SHA。擴充清冊保留原 148 筆 exact capture record，另包含四次新擷取。`prepare_expanded_source_profile.py` 在固定研究身分、母體與全部原 capture record 一致時，才重釘描述性規則的清冊指紋。既有政策及規則內容使用原值。研究本身繼續使用既有跨層驗證與 CAS 讀回。

```bash
PYTHONPATH=src python tools/recover_source_editions.py --verify \
  --profile-path config/research_profiles/au_ree_census_display_v1.json \
  --fixed-manifest "$FIXED_MANIFEST" \
  --bindings config/research_profiles/resource_original_edition_candidates_v1.json \
  --store "$EXPANSION_CAS" --out "$ACQUISITION"
PYTHONPATH=src python tools/prepare_expanded_source_profile.py \
  --profile config/research_profiles/au_ree_census_display_v1.json \
  --fixed "$FIXED_MANIFEST" --expanded "$COMBINED_MANIFEST" --out "$EXPANDED_PROFILE_DIRECTORY"
PYTHONPATH=src AU_REE_SCOPED_RESOURCE_ASSETS="$EXPANSION_CAS" \
  python -m unittest discover -s tests -p test_scoped_resource_evidence.py -v
```
