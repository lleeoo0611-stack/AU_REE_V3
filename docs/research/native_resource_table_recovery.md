# 原生資源表格尋回與欄義核對

此輪以 `2fc644dc3fadca64cf9cb75db921bd728ea4160a` 為基準，沿用固定 144-SHA 來源母體、2026-11-28 `Asia/Taipei` 截日與 Stage 2 來源清冊。定位與欄義候選由原始 PDF 產生。正式採納仍經原有研究流程，這次尋回新增 0 筆正式觀測。

| Stage 2 待查項目 | 本次結果 |
| --- | --- |
| 143 筆有原文位置、欄義未綁定的出現紀錄 | 1 筆比對為既有核閱觀測，142 筆保留證據不足 |
| 19 個缺表格結構的 Source×Project | 4 個找到 6 列候選，15 個仍未取得足夠結構證據 |
| 142 筆仍未綁定 | 59 筆表頭或單位未綁定、2 筆非唯一數字格、9 筆為修飾後的 TREO、11 筆為非全礦物基準、24 筆屬其他分析物或物理量、37 筆組合或富集值缺適用化學語境 |

找到的表格提供 16 個可辨識的物理位置，涵蓋已在後續研究版本核閱的例子。相同原件與數值位置依 SHA、實體頁、行、欄及原文 token 去重。逐項處置與完整原文定位分別保存在 `prior_binding_dispositions.jsonl`、`discovered_native_rows.jsonl` 和 `value_evidence_lineage.jsonl`。 [量測收據](native_resource_table_recovery_receipt.json)

真實原件 Jupiter 的同一列保留 2025 與 2026 的父欄、各自的 Bt 單位、重複顯示的 ppm 和原標註符號。Aileron 原件在同一編號標題前印出兩個不同 cutoff 區塊，每一列以自身表頭定位。North Stanmore 的無編號標題能保存對應的資源表格位置。跨頁單位註解須有對應符號與相鄰同編號附註；此路徑已以合成正反例測試。OCR 本輪未執行。

| 凍結留出評估 | TP | TN | FP | FN | 召回率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stage 2 第一組，提供定位 | 2 | 11 | 0 | 8 | 20% |
| Stage 2 第二組，提供定位 | 2 | 16 | 0 | 29 | 6.45% |
| Stage 3 新家族，自動定位 | 0 | 3 | 0 | 5 | 0% |
| Stage 3 新家族，提供定位後辨識欄義 | 8 | 20 | 1 | 6 | 57.14% |
| Stage 3 新家族，端到端欄義 | 0 | 20 | 0 | 14 | 0% |

兩個舊留出集部分家族重疊，結果獨立回報。新留出集的五個家族與開發案例分離。新留出集與基準各層結果一致，通用性召回增益為 `HYPOTHESIS_NOT_SUPPORTED`。提供定位時重現一筆既有候選欄義誤判，欄義 precision 為 88.89%。兩個自動定位層未產生正判，precision 為 `UNMEASURED`。正式採納的獨立精確率與召回率亦保留 `UNMEASURED`。 [逐家族匿名指標](native_resource_table_recovery_receipt.json)

同一批定位輸入採三次交錯新程序量測。基準與新版皆開啟 15 份 PDF 並解碼 1,292 頁，耗時中位數 5.516→5.467 秒、CPU 5.591→5.549 秒、最高 RSS 48,608→48,200 KiB。兩次完整尋回以 34 份 PDF、2,698 頁為工作量，其中 1,406 頁進行結構搜尋，耗時中位數 18.348 秒、最高 RSS 73,968 KiB，兩次五件產物 SHA-256 一致。透過 `DocumentCache` 每次執行每 SHA 只解碼一次。此路徑無 SQLite 查詢，兩組量測未清除作業系統檔案快取。 [逐次效能數據](native_resource_table_recovery_receipt.json)

`resource_table_binding.py` 保留數字與原生欄層定位。`native_table_discovery.py` 負責從原文找表，`native_table_work.py` 負責來源核驗、逐項處置及收據。原綁定模組 AST 最大分支分數由 87 降至 44。相同定位工作的到達函式 Max/P90 從 87/16 變為 44/17，動態相依邊 14→16，靜態相依邊 30→35。全套件靜態循環見證與完全重複函式群未變。 [工程度量與檔案清冊](native_resource_table_recovery_receipt.json)

完整研究重建核對 4,743 格、18 個正式科學格、42／27／21 筆候選／通過驗證／正式選值，以及六個可比較準則鍵、MCDA 矩陣、權重、排名與敏感度。13 組比較皆一致。本機測試執行 321 項，308 項通過，13 項因未附可選 fixture 跳過。正式發布與獨立故障域仍依整合驗收。 [可重播科學比較](native_resource_table_recovery_receipt.json)

使用固定來源清冊與先前的候選欄位收據重跑。可加 `--located-only` 重現同工作量比較。

```sh
python -m au_ree_v3 research bind-native-tables \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --inventory work/scientific-admission/inventory-verified-a/source_case_inventory.jsonl \
  --inventory-receipt work/scientific-admission/inventory-verified-a/source_case_inventory_receipt.json \
  --sources work/research-yield/source-inputs/source_manifest.json \
  --assets work/fresh/source-store \
  --prior-binding work/census-stage2/binding-benchmark/candidate-1 \
  --out work/census-stage3/binding/replay
```

## V2 歷史線索的原件欄位讀回

`native_table_work.replay_legacy_source_lanes` 從唯讀 V2 `2af6f611e51eb4ac9dda73419d1afeba70d1307e` 讀取固定觀測清冊。歷史值詞元只供同版、同 Project、實體頁、欄義與單位比對。回傳的原報詞元、位置、欄名、單位、列與表名錨點均從 V3 CAS 的 PDF 重新讀出，並附 V3 原件 SHA、網址、擷取資格與時間判定。辨識到位置仍需獨立查核專案範圍、分類、估算日、cutoff、材料與化學。輸出的 `scope_status` 是 `UNBOUND`，用途為候選核對。

固定 Stage 6 擴展清冊使用 SHA-256 `4ed939586a90708da79e95072eef363905e568e1463cb79a1c546ac379feff6a`，V2 清冊 SHA-256 為 `067e6bf1d8ba46426fb5f16ec7ab404606aa58c181b6ec57445ea6321e97aeb2`。519 筆線索中有 448 筆可聯結至這份清冊的 44 份 PDF，解碼 3,322 頁並分析 57 個指定頁。找到 55 個唯一原生位置，涵蓋 11 案，分別為礦量 6、TREO 品位 6、TREO 含量 2、個別氧化物 41。7 筆具有多個相符原生位置，繼續隔離。85 筆歷史衍生值走獨立公式審查，另有 71 筆缺少同版同案合資格 PDF。Goschen 第 12 頁讀出的 94,200,000 t 屬 M＋Ind＋Inf 列，與舊線索的 M＋Ind 分類不同。正式新增值為 0。

兩份本機 CAS 的候選收據一致，canonical JSON SHA-256 為 `b992aba604df6b543a6f439a957c7fb73486a82317b5a064f91e6e39e75a6f72`。原始／獨立本機 CAS 的實測 wall time 約 21.8／24.7 秒，Python 程序的峰值 RSS 為 71,304／71,108 KiB。兩次使用相同原件與規則，未控制作業系統頁面快取。此流程沿用既有來源驗證、表格讀取及原生欄位契約。32 項直接與相依測試均通過，包含原件回歸及不同單位、詞元和衍生值負例。

```bash
export AU_REE_LEGACY_V2_ROOT=/workspace/AU_REE_V2_reference
export AU_REE_LEGACY_LANE_MANIFEST=/workspace/AU_REE_V3/work/census-stage6/additional-sources/combined/source_manifest.json
export AU_REE_LEGACY_LANE_CAS=/workspace/AU_REE_V3/work/census-stage6/verified/cas
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -p test_native_table_discovery.py -v
```

## 固定 93 案的原件回讀

`research recover-historical-cohort` 從唯讀 V2 凍結版核對 Project ID、4,743 格血緣、來源清冊及原擷取設定的 SHA。它逐來源回讀既有 V3 CAS 或按 SHA 命名的歷史原件庫，重用原生表格解析及文字定位。能以舊線索定位一格時，同列其他已辨識角色的數值也由原件讀回。歷史擷取設定只提供位置、角色及判讀線索，候選數字來自當次讀回的原件錨點。

候選 SQLite 使用既有 Store 契約，保存 Project、Source、Scope、原件錨點和 Raw Observation。Scope 暫繫於該表列。文字候選保留原件可唯一定位的引文及原欄位。已註冊的 TREO 含量公式可用同一原始表列的量與品位重算，結果保持影子狀態。外部圖層可輸入先前政府擷取收據及 CAS，依原生 ID 重播。缺同版原件、欄義、時間或資源範圍證據的結果保留在回讀收據。

```sh
PYTHONPATH=src python -m au_ree_v3 research recover-historical-cohort \
  --v2-root "$V2_REFERENCE" --profile config/research_profiles/au_ree_census_evidence_fill_v1.json \
  --sources "$SOURCE_MANIFEST" --assets "$V3_SOURCE_CAS" --historical-assets "$EXACT_V2_ASSETS" \
  --geography "$GOVERNMENT_RECEIPT" "$GOVERNMENT_CAS" \
  --database "$OUTPUT/candidates.sqlite" --out "$OUTPUT/recovery.json"
```

輸出各自使用新路徑。移除 `--geography` 可單獨重播來源、文件與公式。候選 SQLite 可用來執行附來源分組的 NLP 評估。 [分組訓練與留出](oxide_table_retrieval.md)

## 逐格准入與原報展示

以 PR #66 合併提交 `fb3b0aa6300f119623d7e6f282073adc5cc23c0c` 為基準，逐格核對 93 案、4,743 格、85 案的 472 個歷史候選格。其中 363 格在基準候選網站仍空白。同一份 SQLite 也找出 3 個獨立的原生欄位候選格。逐格收據各自列出 Census 展示、SQLite 正式選值及 MCDA 資格，記錄來源 SHA、原件定位、適用規則及第一個未通過的關卡。可用此收據查閱一格的所有原件路徑。各用途的關卡次序固定，格級摘要採用走到最遠的證據路徑。

重用原有 Temporal Gate 及已驗證的同 SHA、同 Project 發行人擷取收據，將 212 個文字格中的 186 格核對為截日前可用。其餘 26 格仍缺合格公開證明。公開時間已確認的文字仍須核對欄義、主張角色及適用範圍。Charley Creek 的發行公告可定位到 Scoping Study 完成聲明，現行里程碑驗證器仍回傳 `NO_COMPLETION_OR_COMMENCEMENT_CLAIM`，因研究事件日期及受支援的完成句式未建立。

逐份回讀 11 份同版原件的 12 個聲明表列及 70 個原始儲存位置。12 列均能從原件連到有名稱的資源表；其中 8 列的原報 cutoff、7 列的分類未達新增正式資源准入，兩組原因可能重疊。共有 15 個位置已可對到基準中的同一筆驗證觀測。Wolverine 的一個 (Y_2O_3) 位置仍有已登錄的精度衝突。欄位辨識已能區分同表、同為 ppm 的 TREO 與 TREO−CeO₂ 欄，也可讀取帶全名括註的 TREO 成員定義。Circle Valley 重綁出 890 ppm，Inferred-only 仍由現行驗證器拒絕正式採納。98 Mt 的偏移欄在嚴格路徑仍未綁定，430 ppm TREO−CeO₂ 的原報 cutoff 可查核，化學式下標仍會影響正式定位器。

以原報表名、數字、單位、同版擷取及時間證據填入 3 格候選 Census 展示。Barkly 為 40 Mt，Brockman 為 41.6 Mt 與 0.20%。三格保留原報 Inferred 類別及候選狀態。正式科學採納新增 0，Census 正式科學格維持 22，MCDA 比較鍵維持 6，完整 R1＋S1 案維持 2，具完整六構面的案例仍為 0。全部 691 筆 V2 觀測線索的回讀及分組訓練見 [NLP 報告](oxide_table_retrieval.md)。

| 472 個歷史候選格的首個正式選值關卡 | 格數 |
| --- | ---: |
| 欄義或用途相容性 | 338 |
| 截日資格 | 78 |
| JORC 類別 | 53 |
| 估算有效日 | 2 |
| 已有同一原件的選值，Goschen 礦量 | 1 |

3 個新增展示格中，Barkly 屬原有 472 格，Brockman 兩格由原件表列讀回的相鄰數值獨立定位。正式 4,743 格比較含 4,677 格完全一致、66 格僅收據血緣改變。兩次候選 SQLite 回讀及兩次冷重播皆核對 SHA，F0–F4 通過，F5 因缺乏獨立故障域維持 `BLOCKED`。基準 `fb3b0aa` 補跑 Exact resource 及 Frozen reference integrity，後者核對封存參照與 Golden 時間稽核，V2 完整原件重播仍受阻。 [基準補跑](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46180027612)

相同候選母體的回讀兩次為 67.67／67.91 秒，峰值 136,112 KiB。基準回讀約 58 秒，舊新版分開量測。兩次研究冷重播為 21.44／19.56 秒。正式程式模組 72→73，靜態內部相依 352→362，AST Max/P90 維持 82／20，循環見證維持 1、跨模組完全重複函式群維持 3。候選版本 `1dafd509e5311250b168fafafa21ba382c1798973ec5f0e74005012f235db701` 固定程式提交 `407de5c171751c29734c9cb5b922465758a6d11f`。

以固定 CAS、同版候選 SQLite、基準及新研究目錄重現。

```sh
PYTHONPATH=src python tools/audit_resource_statement_holds.py \
  --profile "$PROFILE" --manifest "$SOURCES" --assets "$CAS" --build "$BUILD" \
  --recovered-statements "$RECOVERY" "$CANDIDATE_SQLITE" tests/fixtures/historical_resource_statement_review_v1.json \
  --out "$STATEMENT_REVIEW"

PYTHONPATH=src python -m au_ree_v3 research audit-cohort-admission \
  --recovery "$RECOVERY" --database "$CANDIDATE_SQLITE" \
  --baseline-recovery "$BASELINE_RECOVERY" --baseline-build "$BASELINE_BUILD" \
  --current-build "$BUILD" --baseline-display "$BASELINE_DISPLAY" --current-display "$DISPLAY" \
  --statement-review "$STATEMENT_REVIEW" --out "$ADMISSION_RECEIPT"
```

候選網站由 `research build-display --historical-cohort "$RECOVERY" "$CANDIDATE_SQLITE"` 接入同版原件回讀。 [逐格機器收據](historical_cohort_admission_receipt_v1.json)
