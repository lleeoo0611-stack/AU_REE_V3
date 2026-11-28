# Census 補值與證據擴張驗證

研究截日 **2026-11-28，Asia/Taipei**。本輪從 GitHub `main` `aa92a35c3d5b3593c25b246edcd28ea36c4b0d9a` 開始，逐格對照 Stage 6 候選 `8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3`。固定 93 個 Project ID、51 欄、147 份來源 SHA 和 152 筆取得事件。另有 144 份固定母清冊。V2 的運算版 `0dff6beb5aca52e565c37289e3ab295737e76942` 和封存輸出版 `2af6f611e51eb4ac9dda73419d1afeba70d1307e` 均以唯讀方式使用。

## 實測科學與展示增益

| 格或研究樣本 | Stage 6 | 本輪 |
|---|---:|---:|
| 核心有值格 | 1,141／4,743（24.06%） | 1,151／4,743（24.27%） |
| 核心未知／不適用 | 3,602／0 | 3,592／0 |
| 正式直接科學／正式衍生 | 18／4 | 18／4 |
| 已驗證描述 | 27 | 36 |
| 證據參照 | 4 | 5 |
| 正式與已驗證描述的原件 SHA／Project | 13／13 | 15／13 |
| 擴充展示有值格 | 45／837（5.38%） | 52／837（6.21%） |
| 數值候選／已核驗／正式選值 | 55／39／33 | 55／39／33 |
| R1／S1 可比較鍵 | 4／2 | 4／2 |
| 完整 R1＋S1／六構面 | 2／0 | 2／0 |

新增 10 格核心展示涉及 Nolans、North Stanmore、Cummins Range 與 Wolverine。53 格僅更新血緣、4,680 格逐位元不變。每案的里程碑三格共用其原件、Scope、事件日期、頁碼與 Temporal Decision。擴充的 7 格是 6 個新增年產情境欄格和 1 個 Cummins Range 研究完成日期格。擴充原件證據 48 → 58 筆。

獨立 Decimal Oracle 比對 Wudinna 與 Wolverine 的 SAW、TOPSIS、PROMETHEE II，共 18 個分數與排名點、24 個敏感度點。比較鍵、完整 cohort、分數和排名延續原結果。其餘面板仍未達樣本門檻。擴大 R1／S1 比較鍵與完整三案的假說為 FAIL／HYPOTHESIS_NOT_SUPPORTED。

## 來源、拒收與方法

| 工作 | 實際結果 |
|---|---|
| T0/T1 清冊 | 重算 4,743 格與 V2 歷史 2,500 個有值格。57 個 SHA、134 個 Project×SHA 缺少現行合格連結，涉及 280 個現行未知格。93 案各有合格來源。Stage 6 未知格中，98 個歷史原件連結未取得、14 個驗證失敗、7 個衝突、4 個原表未擷取、2 個在已查範圍內未找到揭露、1 個部分組成。其餘 3,476 個無可驗證查核紀錄。 |
| T2 適用性 | V2 1,650 個「無 MRE」與另 65 個其他類型不適用格在 Stage 6 均為未知。以 8 份真實原件審查 7 案、32 個資源格。舊日期的肯定敘述欠截日有效延續，另外有來源 Scope 衝突，新增不適用 0。 |
| T3 衍生與揭露 | 獨立 Oracle 重現 3 個 TREO、1 個 Wudinna MREO 正式衍生及 14 個 Wolverine 影子結果。Nolans 衍生 TREO 1,456 kt 的舍入區間為 1,415.25–1,497.25 kt，R1 所需的原報含量仍缺。Wolverine p6 的四種磁性氧化物為逐項揭露證據，Dy 原報精度衝突持續保留。 |
| T4 回復 | 5 筆實際請求取得 3 個 SHA。Yangibana 找回指定版次並取得時間資格，混合 cutoff 與資源／儲量欄的證據衝突仍在。Lyons 12&13 精確 SHA 重取缺截日前同版證據，且原表僅 Inferred。Yttria 的合格替代版含 TREO 減 Ce 的 cutoff，原指定版仍缺。另有獨立來源清冊 149 SHA／155 筆合格事件可讀回。 |
| T5 表格原件搜尋 | 預先封存 8 個報告家族、127 頁。BM25 的 TP／FP／FN／TN 為 1／7／2／117，原生 S1 欄義定位為 3／0／0／124。12 個單項欄位定位全數回讀一致，另有 1 個合計欄的重複公式詞元。小樣本及標註由同一作者完成，泛化與正式採納精確率／召回率為 UNMEASURED。 |
| T6 官方地理 | 8 個來源入口取得 6 份原件，2 個 HTTP 404／403。四案核對點位角色、日期證據、CRS 與精度。Koppamurra、Franklyn 的 GDA2020／MGA54 數值轉換與 GDAL 交叉相符。官方點位的時間或代表性證據仍不足，正式座標和 I1–I4 增量均為 0。 |
| T7 研究里程碑 | North Stanmore 與 Nolans 分別有 PFS、DFS 結果公布證據。Cummins Range 證實 2023 年 8 月 Scoping Study 完成，2022 年 9 月前版證據保留。結果公布日與完成日各有用途。早期工程、規劃與時證不足事件保留候選。 |
| T8 預測 | Nolans DFS 4,357 t/年為三年爬坡後平均並排除末個不完整年度。Dubbo Heap Leach Scoping 的 1,157 t/年為第 3–15 年平均，1,037 t/年為全礦期平均。三筆 NdPr 氧化物年產預測均可回查原件，擴充展示增加 6 個 Project×欄。 |

逐案定位、適用規則及重跑方式見 [缺值清冊](census_disclosure_inventory.md)、[適用性與衍生審查](census_fill_science_review.md)、[來源回復](census_targeted_source_recovery.md)、[原生表格搜尋](oxide_table_retrieval.md)、[官方地理查核](native_government_location_review.md)、[研究里程碑](census_development_milestone_evidence.md)及[年產預測收據](issuer_forecast_coverage_receipt.json)。

## 驗收、發布與重跑

候選 Release ID 為 **3d85497ac2fdaf19683bdc7dc22af4f8b4fda237c61643653f95721d774cc50e**，運算 commit 為 869f8970089da455e9bfe8858de5035d1352a904。原件清冊 SHA 為 4ed939586a90708da79e95072eef363905e568e1463cb79a1c546ac379feff6a。

F0–F4 PASS。兩套實體分離的本機 CAS 各核對 8 個科學產物，4 個展示產物與 live 逐位元一致。SQLite 完整性和外鍵通過。F5 BLOCKED，兩份本機副本仍共享故障域。歷史 O2 維持 FAIL。正式發布與正式網站指標未更新。

Chromium 實際驗證雙候選版本切換、搜尋、排序、篩選、雙語、MCDA、Wolverine 原件定位和三案里程碑的適用角色。損壞原件 SHA、篡改顯示 JSON 會遭拒收，回滾到 Stage 6 成功。候選 ZIP 解壓再驗證成功。首輪整合的網站驗證攔下 STUDY_RESULTS_RELEASED，保留試跑 60c02b6… 為 FAIL。修復後用新的候選 ID 重跑，新增不接受偽裝完成或錯誤事件日期的回歸。

精確本機套件 418／418 通過，耗時 146.93 秒、峰值 RSS 179,440 KiB。工作區 work/census-fill/verified/ 保存候選、兩份 CAS、網站、全量逐格差異與影子查核。archive/releases/ 保存完整封存清冊，該候選的 CAS 物件數為 320。

重播核心、逐格比較和候選網站時，RUN 指向已核驗的候選產物，STAGE6 指向歷史基準產物，REPLAY 指向空白目錄。從運算提交的工作樹執行。

~~~bash
export PYTHONPATH=src
release=3d85497ac2fdaf19683bdc7dc22af4f8b4fda237c61643653f95721d774cc50e
python -m au_ree_v3 research replay --offline --release-id "$release" \
  --release-root "$REPLAY/index-a" --store "$RUN/cas" --out "$REPLAY/a"
python -m au_ree_v3 research replay --offline --release-id "$release" \
  --release-root "$REPLAY/index-b" --store "$RUN/replica-cas" --out "$REPLAY/b"
python -m au_ree_v3 research compare --baseline "$STAGE6/build" --current "$RUN/build" \
  --release-root "$RUN/archive" --store "$RUN/cas" --current-release-id "$release" \
  --replay-run "$REPLAY/a" --replay-run "$REPLAY/b" --out "$REPLAY/delta"
python -m au_ree_v3 research verify-site --mode candidate --site "$RUN/preview" --out "$REPLAY/verify-site"
~~~

重建使用 au_ree_census_evidence_fill_v1.json 和 resource_bindings_v6.json。展示採用 census_display_columns_v2.json、production_forecast_context_oracle_v2.json 與既有官方地理輸入。年產綁定先用 sources.require_source_expansion 驗證 144 清冊至 147 清冊父子關係，再以 147 清冊 SHA 重綁 source_manifest_sha256。保留所有既有案例、原件 SHA、原件收據及 Oracle。各原件查核子流程的重跑方式見來源與研究報告。

## 工程與 CI 實測

固定相同的 147 份來源、v6 綁定、Stage 6 舊 profile 與 93×51，兩版各三次新程序。科學差異欄為空。

| 指標 | Stage 6／起始版本 | 本輪 |
|---|---:|---:|
| 固定範圍 wall 中位秒 | 11.05 | 10.65 |
| 固定範圍 CPU 中位秒 | 11.13 | 10.81 |
| 固定範圍峰值 RSS 中位 KiB | 97,608 | 98,148 |
| 生產模組／內部靜態依賴邊 | 70／328 | 72／337 |
| AST 分支分數最大／P90 | 84／20 | 82／20 |
| 靜態循環群／跨模組同體重複群 | 1／3 | 1／3 |
| 各自候選核心 build 秒 | 18.74 | 12.37 |
| 各自候選展示 build 秒 | 8.23 | 8.30 |

固定範圍皆載入 36 個模組、116 條動態依賴，OS 檔案快取未清除。兩個新增模組各處理原生地理與表格定位，無新第三方依賴。兩版重用快取皆把 PDF 解碼由 23 份／1,518 頁降至 14 份／1,254 頁，SQLite 操作皆為 8,317 次。各自候選使用不同的綁定，計時分開記在機器收據。T5 八案查詢 wall 中位 0.516 → 0.755 秒，峰值 RSS 21,804 → 22,380 KiB。

七個功能 PR 的 GitHub Actions 執行合計 757 runner 秒，取各執行工作的開始至結束。歷史及本輪帳單金額為 UNMEASURED。

## 整合及剩餘瓶頸

| PR | 驗證提交 | 工作 | 實際 CI |
|---|---|---|---|
| [#56](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/56) | 4fa73f0 | 年產情境 | [Development governance](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165226169), [Migration verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165226172) |
| [#57](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/57) | c550554 | 原生表格定位 | [Migration verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165060505), [Development governance](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165060537) |
| [#58](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/58) | cd351f1 | 基準與缺值清冊 | [Development governance](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165141948), [Migration verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165141957) |
| [#59](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/59) | c389d56 | 同版原件回復 | [Migration verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165231654), [Development governance](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165231692) |
| [#60](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/60) | 4bc9d42 | 官方位置查核 | [Development governance](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165302225), [Migration verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165302268) |
| [#61](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/61) | b5cbf3c | 有日期的研究里程碑 | [Migration verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165683869), [Development governance](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165683878) |
| [#62](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/62) | bcbfa12 | 適用性與磁性氧化物揭露 | [Research website](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165576006), [Static research web verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165576008), [Development governance](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165576016), [Migration verification](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46165576028) |

運算提交 869f8970089da455e9bfe8858de5035d1352a904 位於本候選整合 PR。檔案生命週期列 38 個新增、24 個修改，另有經 SHA 釘選的候選封存與全量逐格差異。Git 移除或合併檔案為 0。

來源清冊列有 blocked_sources.csv 和尚無擷取紀錄的歷史缺件。待解事項包括 Lyons 同版截日前公開證據、Yttria 指定版與估算有效日、Yangibana 的資源分類與 cutoff，以及有完整同版組成與原報含量的報告。官方礦址的時間、角色與位置證據是 I1–I4 的瓶頸。F5 仍需獨立故障域與發布授權。

[逐欄比較](census_fill_round_coverage.csv)和[機器收據](census_fill_round_receipt.json)提供各次工序與指紋。[檔案生命週期清冊](census_fill_round_lifecycle.csv)列出所有本輪變更及理由。
