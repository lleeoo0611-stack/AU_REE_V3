# Census 已發生開發里程碑驗證

T7 以 Stage 6 候選 `8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3` 為基準，使用相同 93 案、2026-11-28 Asia/Taipei 截日及 147 個來源 SHA。以原生文字篩讀其中 144 份 PDF，共得 255 條待查候選，耗時 13.90 秒。逐查下列原件與正反例，確認三案可增加已驗證的 Census 開發描述。

| 專案與原件 | 可驗證事件與日期 | 核准用途 |
|---|---|---|
| North Stanmore，Victory 季報實體第 8 頁 | 2026 年 8 月公布 PFS 結果。該季報發行日為 2026 年 10 月 30 日。 | Census 歷史結果披露 |
| Nolans，Arafura ASX 公告實體第 1 頁 | 2019 年 2 月 7 日由該公告公布 DFS 結果。 | Census 歷史結果披露 |
| Cummins Range，RareX 2024 年 ASX 公告實體第 12 頁 | 2023 年 8 月完成加強版 Scoping Study。原文另列 8 月 22 日公告日。 | Census 歷史完成描述及完成月份 |
| Cummins Range，RareX 2023 年 ASX 公告實體第 13 頁 | 2022 年 9 月完成較早的 Scoping Study。 | 保留已驗證歷史斷言及較晚研究連結 |

[Victory 季報原件](https://hermes-cdn.investorhub.com/announcement-pdfs/asx/7814024.pdf?v=63960532622) · [Arafura 公告原件](https://announcements.asx.com.au/asxpdf/20190207/pdf/442fgrbxqwrxdd.pdf) · [RareX 2024 公告原件](https://announcements.asx.com.au/asxpdf/20240125/pdf/05zrcjjvw7nkyl.pdf) · [RareX 2023 公告原件](https://announcements.asx.com.au/asxpdf/20230501/pdf/05p7grkh7l8j7v.pdf)

North Stanmore 2025 年公告的 PFS 預計公布保留為 `PLANNED`。Nolans 的早期現場整備保留為 `EARLY_WORKS`。Donald 的 BFS 涵蓋 Phase 1，原有分期拒收仍成立。Coburn 固定年度報告實體第 8 頁有 2021 年 5 月動工及 2022 年 11 月 18 日達成商業重礦物精礦生產，已找到另行公告索引。Nolans FID 亦有 2026 年 5 月 21 日直接公告索引。兩份直接公告需要精確版本及截日前公開證明。停產、維護、退出及出售本次未取得合格的專案事件證據。 [Coburn 年報原件](https://announcements.asx.com.au/asxpdf/20230906/pdf/05tkl2byt4wfmn.pdf) · [Coburn 商業生產公告索引](https://announcements.asx.com.au/asxpdf/20221118/pdf/45hr0vz5ndff9t.pdf) · [Nolans FID 公告索引](https://announcements.asx.com.au/asxpdf/20260521/pdf/06zt53blvvwbfy.pdf)

固定 V2 `2af6f611e51eb4ac9dda73419d1afeba70d1307e` 顯示 North Stanmore 的 2026-10-30、Nolans 的 2025-06-30 及 Cummins Range 的 2023-08-22。V3 使用各項事件原件可驗證的日期角色與粒度。North Stanmore 和 Nolans 的結果公布日期留在核心欄位的描述及證據。展示延伸的研究完成日期欄只增加 Cummins Range，整體呈現 45→46／837。較早的 Cummins 報告日期留在完整型別斷言，沒有據此推定前案結果已撤回。 [V2 歷史診斷](census_v2_diagnostic.md)

## 差異與成本

| 項目 | Stage 6 → T7 候選 |
|---|---:|
| 核心 PRESENT | 1,141→1,150／4,743 |
| 已驗證核心描述格 | 27→36 |
| 逐格差異 | 9 格新增已驗證屬性，53 格版本及血緣收據變更，4,681 格無差異 |
| 直接及衍生科學格 | 18＋4→18＋4 |
| R1／S1 可比較鍵 | 4／2→4／2 |
| R1＋S1 完整案數 | 2→2 |
| 六構面完整案數及正式 D1 可比件數 | 0→0 |
| MCDA 觀察值、矩陣、排名與敏感度 | 0 差異 |

兩個既有模組都重用來源 CAS、原生 PDF、時間資格、Typed Attribute 與 Census 投影。直接 import 邊各維持 13、12，無新增套件或抽取流程。以 AST 每個 `if`、條件運算、迴圈及例外分支計數再加 1，`read_milestone_evidence` 為 10→9，`_classification` 為 9→12，`review_attributes` 為 19→20。新增事件與日期綁定及歷史保留各自獨立。原有政策與設定保留供冷重播。釘選設定新增 `census_attribute_bindings_v3.json` 和 `development_milestone_bindings_v3.json`，並更新兩個既有測試檔。沒有合併、封存或移除既有檔案。

同一工作區 Python 3.12.14、Poppler 25.03.0、SQLite 3.53.1。每次為新的子程序，Linux 頁面快取未控制。Stage 6 實測建置為 18.74 秒／93,512 KiB，T7 建置為 16.84 秒／93,652 KiB。各自為單次樣本。T7 封存 3.78 秒／60,408 KiB，兩次離線冷重播 20.75 秒／93,156 KiB 與 17.30 秒／93,164 KiB，逐格比較 1.16 秒／97,172 KiB，展示包 8.25 秒／98,740 KiB。

## 驗收與重跑

T7 的 18 項適用測試皆 PASS，涵蓋真實正例、前置工程、先前預計公布、引文範圍錯置、事件與出版日期、明示不確定措辭與凍結屬性。F0–F4 PASS。候選展示包 PASS。兩個獨立路徑的本機 CAS 均完成冷讀，仍共用同一故障域，F5 BLOCKED。這組來源是定向驗證，未量測跨發行人泛化。網站端到端及 GitHub CI 由整合流程驗證。

已測程式提交 `900cbd545cf425ffbbfe3bf3b4d4700e78daef02`，候選封存 `e88107a04bef75a2d773a13eff8d1933b1ab819f28cd6c2b6ca94a91a63c0505`。本地 `work/census-fill/t7-milestones-900cbd5` 保存完整產物、收據及耗時。逐格對照收據 SHA-256 `564ab58915625f4728b70ae48c6cf648a5b6b644c5535005738a9b2090322be2`，候選展示包收據 `f7513183a4e317592395a5b1f2ecb73601163755b7a6e3aea6725759e9c22300`。

    export PYTHONPATH=src
    export AU_REE_FILL_T7_CAS=/workspace/AU_REE_V3/work/census-stage6/verified/cas
    export AU_REE_MILESTONE_MANIFEST=/workspace/AU_REE_V3/work/research-yield/source-inputs/source_manifest.json
    export AU_REE_MILESTONE_ASSETS=/workspace/AU_REE_V3/work/fresh/source-store
    for tests in test_development.py test_project_attributes.py test_attribute_batches.py; do
      python -m unittest discover -s tests -p "$tests" -v || exit $?
    done

以基準 CAS 內 `dc50a0fe07adc957b05c8990a77b3b4c77d1a479bb6319d6a74296f5d5482dbd` profile 為輸入，替換 `attribute_bindings` 與 `attribute_milestones` 的路徑和實際 SHA。測試重建此組合並分別核對兩個 profile。CLI 建置沿用資源規則 `8194d0bf…` 和來源清冊 `4ed93958…`。用凍結版本依既有 `research build`、`archive`、兩次 `replay --offline`、`compare` 和 `build-display` 完成重跑。 [封存與候選展示完整命令](census_comparability_results.md)
