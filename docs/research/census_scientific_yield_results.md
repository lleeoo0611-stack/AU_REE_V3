# Census 科學資料擴張驗收

研究截日為 2026-11-28，時區為 Asia/Taipei。固定原件清冊 SHA-256 為 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`，共 144 個原件 SHA、148 筆取得收據及 93 個 Project。開工主線為 `44bc122d1337434cb8f496d8d0d6d0dd2dc4d981`。Stage 4 封存候選為 `a1805f68b22d73ab070be50f6d2a110edfcb30230e21778b6b62513ccbcc96ee`，其研究運算提交為 `69a111dd56420d364c1ddf2547fe81888b4e51ed`。

## 可驗證的科學增益

| 指標 | Stage 4 | Stage 5 | 淨變化 |
|---|---:|---:|---:|
| 數值候選 | 42 | 53 | +11 |
| 驗證觀測 | 27 | 37 | +10 |
| 正式科學選值 | 21 | 31 | +10 |
| Census 直接科學格 | 16 | 16 | 0 |
| Census 核准衍生格 | 3 | 3 | 0 |
| Census 已驗證描述格 | 27 | 27 | 0 |
| 原 51 欄有值 | 1,138 / 4,743 | 1,138 / 4,743 | 0 |
| 擴充欄有值 | 39 / 837 | 40 / 837 | +1 |
| 有正式值或驗證描述的 Project | 12 | 12 | 0 |
| 可比較 Project×Criterion | 6 | 6 | 0 |
| 完整 R1+S1 Project | 2 | 2 | 0 |
| 完整六構面 Project | 0 | 0 | 0 |

Wolverine 新增的資料可以回答 2025-01-15 資源估算中 10 種全礦氧化物分別報告多少 ppm。十項為同一個 Project、同一份 JORC 資源聲明與同一個 0.15% TREO cutoff 的不同量測項，跨案樣本仍為零增量。原件 SHA 為 `e29ab1305c8edb5bd1074806d5cf31c323c82cfd68c5c5b2e9b545137f09ccb0`，定位為物理第 6 頁 Table 5 `Total`，分母為全礦。

| 氧化物 | 原報 ppm |
|---|---:|
| La₂O₃ | 170 |
| CeO₂ | 420 |
| Sm₂O₃ | 200 |
| Eu₂O₃ | 40 |
| Gd₂O₃ | 560 |
| Ho₂O₃ | 170 |
| Er₂O₃ | 490 |
| Tm₂O₃ | 70 |
| Yb₂O₃ | 380 |
| Lu₂O₃ | 50 |

Y₂O₃ 的 5,680 與 5,700 ppm 名義精度區間分離，兩處原報值一併保留待判。Dy 既有精度衝突繼續隔離。全組成合計只作表內檢查，未產生新衍生值。十項新選值留在候選封存的 SQLite 與觀測證據中。既有 51 欄投影及 MCDA 用途仍採原核定政策。Wolverine 逐項原件定位、Scope、Statement、Observation、時間證據與精度見 [原件審查](wolverine_oxide_constituents.md)。

4,743 格中 4,693 格完全一致，50 格的版本性證據收據有變動，值與狀態一致。擴充欄新增 Mt Weld 估算日 `2024-06-30`，以同版封面、腳註、正文及相同 cutoff 綁定；觸及一個既有 Project、一個原件與一個展示欄。其正式估算日已存在。六個正式比較鍵為 R1 四個及 S1 兩個。既有兩案面板的矩陣、分數、排名與敏感度延續封存結果。其餘四個面板維持 `INSUFFICIENT_COHORT`。

## 取得、解析與獨立評估

- 93×51 工作清冊整合 3,948 筆來源映射為 2,518 個去重鍵。最高優先 109 項，其中 9 項已有原件審查。原生未綁定位置與缺結構工作的基準分別為 142 與 15，兩者屬於不同排查單位。詳見 [優先序清冊與方法](census_scientific_priority.md)。
- 固定語料的獨立原件掃描涵蓋 141 份 PDF、7,051 頁。既有原生數值工作清冊全數檢查，未綁定位置 142→141、缺結構案 15→14，定位候選位置 16→19。通用辨識修補沒有產生額外正式選值。新增正式值由上述人工獨立定位確認，再由既有科學、時間及正式選值契約採納。
- 歷史家族端到端欄義 TP／FP／FN／TN 為 0／0／14／20 → 2／0／12／20，召回從 0% 升至 14.29%，改後精度 100%（只有 2 個正命中）。新家族同一指標維持 0／0／16／25，0% 召回；精度與兩組的正式採納泛化均為 `UNMEASURED`。詳見 [分層留出評估](native_resource_scoped_binding.md)。
- Splinter Rock 的相同 682 Mt／1,338 ppm 在不同段落標示不同 JORC 分級；Tower、Cummins Range 與 Aileron 保留相異 cutoff、分類或範圍。Yin 同報估算量矛盾、Gifford Creek 範圍無法確定、Wudinna 修飾分析物與 Goschen 物料基準皆須保留獨立用途。
- Barkly 與 Tower 新取得 4 筆公開擷取、3 個新 SHA，代表 2 份資源聲明。離線核驗 3 份 PDF 共 42 頁，正式選值增加 0。Wudinna、Yttria、Mt Stirling–Yttria、Splinter 四份指定歷史原始版次仍無法取得。逐格分類的 1,396 個固定歷史展示缺口中，1,121 格證據不足、275 格缺來源。詳見 [來源版次驗收](census_source_version_recovery.md)。
- 官方 GIS 複核 8 個 `SHADOW` 特徵及 3 個身份／角色反例。既有地理與新地形來源缺少足夠的 Project 鏈結、來源日期或區位精度，I1–I4 無新增。Jupiter 與 North Stanmore 共 3 個設計／預測情境保持各自材料與期間，無正式年產實測新增。詳見 [地理與年產研究用途](census_context_comparability.md)。
## 同範圍工程量測

每個組別交錯執行三次非剖析建置，另執行一次 cProfile 及一次資源文件冷暖快取配對。三組皆使用同一 144 原件清冊。Stage 4 與 Stage 5 同範圍組皆使用既有 v4 綁定及同一專案設定，科學內容的比對差異為 0。v5 組新增十筆正式選值並保留一筆衝突。運算程式樹與設定檔在工程量測提交 `dfcc53791c10bcbd75faf6337a5c7516bd8073f6` 到研究運算提交之間逐位元一致。作業系統的檔案快取未受控制。

| 指標 | Stage 4 v4 | Stage 5 v4 | Stage 5 v5 |
|---|---:|---:|---:|
| 中位 wall 秒 | 10.686 | 10.133 | 10.436 |
| 中位 CPU 秒 | 10.862 | 10.300 | 10.563 |
| 中位峰值 RSS KiB | 96,840 | 97,348 | 97,428 |
| SQLite 語句 | 5,145 | 5,145 | 7,305 |
| 冷／資源快取暖 PDF 解碼 | 22／14 | 22／14 | 22／14 |
| 冷／資源快取暖解碼頁 | 1,487／1,254 | 1,487／1,254 | 1,487／1,254 |
| 建置實際到達模組 | 36 | 36 | 36 |
| 可達模組靜態匯入邊 | 151 | 152 | 152 |
| 實際執行相依邊 | 115 | 115 | 116 |
| 靜態／執行相依循環 | 1／1 | 1／1 | 1／1 |
| AST 分支分數上限／P90 | 61／19 | 61／19 | 61／19 |
| 跨模組重複 AST 群組 | 1 | 1 | 1 |

v4 與 v5 在 Stage 5 程式上的中位差為 0.302 秒 wall 及 0.263 秒 CPU，攤至十筆各約 0.030／0.026 秒。這是整批新選值及一筆保留候選的差值，不含人工原件查讀。原生定位專項的同範圍中位 wall 由 5.861 增至 6.079 秒，AST 分支分數上限由 44 增至 48，無新增重複群組及循環。最終候選另驗證快取停用及 3 個有效 Python hash seed。暖快取可少解碼 8 份 PDF、233 頁，增加持留資料及峰值記憶體。工程量測為本機樣本，尚無長期人工維護成本。

## 研究封存、網站及 Gate

候選截日與原始清冊相同。保存兩份位元組獨立的本機 CAS 與 SQLite 檢查，兩次各驗證 314 個封存物件。每次重建均產生同 SHA 的 8 件科學產物及 4 件展示產物。獨立驗證另重讀 43 個原文證據與全部 9 欄矩陣，核對每筆新正式值的原件、頁碼、Scope、時間資格、SQLite 鍵及選值。

| 驗收項目 | 狀態 | 證據 |
|---|---|---|
| F0 原件、註冊與研究輸入 | PASS | 144 個 exact SHA、148 筆取得及 93 個固定 ID |
| F1 證據及時間管制 | PASS | 十筆新值逐筆核對同一原件、可採納範圍與截日負例 |
| F2 Census 科學投影 | PASS | 4,743 格覆核、SQLite 完整性及外鍵 |
| F3 MCDA | PASS | 相同公式與權重；6 個鍵、2 案面板及四個樣本不足面板 |
| F4 自身冷重播 | PASS | 兩次離線 CAS 與另一獨立 hash seed／快取實驗 |
| F5 正式發布 | BLOCKED | 兩份 CAS 位於同一故障域；正式研究與網站發行入口均拒絕 |
| 歷史 O2 | FAIL | 保留原消融結果 |
| 新家族辨識增益 | FAIL | 新家族端到端仍為 0/16 |
| 新家族精度、兩組正式採納精度 | UNMEASURED | 無自動正命中或無足夠獨立正式正例分母 |
| 正式 GIS 距離 | NOT_RUN | I1–I4 的 372 格皆缺合格代表點及雙端依據 |
| 候選預覽發布／回滾 | PASS | 錯 SHA 與遭竄改展示資產均拒絕，維持舊候選並可回滾；正式指標缺席 |

剩餘科學瓶頸為 Wolverine 的 Y/Dy 精度衝突、其他 Project 的資源估算及適用氧化物證據，與可配對的土礦範圍、明示有效日、同化學分母和材料分母。新來源優先補前述具原件線索的發行方同版聲明與官方礦址角色。新家族的原生欄義辨識仍需要逐表供應可靠定位，泛化改善尚未得到資料支持。

## 版本、成本及執行收據

- 研究程式提交 `33587f23eda8243ad7713f2a5524f93ac9b2ce65`。新研究候選 `bdb9e868e5cb2a514e608231db35cdb27c3e77b2fc9427f7dbb444409f511c78`。
- 完整真實材料測試 379 通過、零跳過。Stage 4 相同環境從起始主線測得 363 通過、零跳過。測試程式內計時 114.727→116.295 秒，Stage 5 外層 wall 117.136 秒、CPU 114.142 秒、峰值 RSS 188,096 KiB。
- 候選建置／封存 wall 11.005／2.827 秒。離線重播 wall 15.846／17.745 秒。Chromium 156 測得 Census 初次載入 417 ms，歷史版本載入 297 ms。實際覆蓋搜尋、排序、欄位、精確來源、地圖、資源組成、MCDA、語言、行動版，以及舊候選完整性。預覽 ZIP 重解後通過驗證，其 SHA-256 為 `08aa0be8b663f28179d5283c728b516872878e7da7deb8572fd71730f878add3`。
- 基準、原件選值、獨立尋回、科學用途四個遠端 PR 的成功工作合計 393 runner 秒，各為 104、103、105、81 秒。各有 Static and unit、Exact resource integration 成功及 Frozen reference integrity 條件跳過。相近的 Stage 4 主線 push 工作為 Migration 20 秒與 Web 56 秒，PR 的精確整合工作採不同觸發條件。實際計費 `UNMEASURED`。 [A #45](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/45) · [B #46](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/46) · [C #47](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/47) · [D #48](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/48) · [候選整合 CI](https://github.com/lleeoo0611-stack/AU_REE_V3/actions?query=branch%3Afeat%2Fverify-scientific-yield-candidate)
- 完整輸入指紋、封存物件 SHA、工具版本、提交、各 CI job 連結及實測收據見 [機器驗收](census_scientific_yield_verification.json)。檔案逐一列出新增、修改、保存及封存理由。 [檔案生命週期](census_scientific_file_lifecycle.csv)

完整逐格差異位於 `work/census-stage5/final/delta/census_delta.csv`。操作逐次輸出保留在 `work/census-stage5/final.log`、`work/census-stage5/final/`、`work/census-stage5/verification/`、`work/census-stage5/context/` 與 `work/census-stage5/sources/`，均相對於 `/workspace/AU_REE_V3`。低階實際呼叫與計時照錄於 `work/census-stage5/final/reproduction`。以下列出主要產製命令；A–D 分支的真實原件測試和命令存於前述專責報告。

```bash
# 研發工作樹先檢查為 33587f23eda8243ad7713f2a5524f93ac9b2ce65
core=/workspace/AU_REE_V3
out="$core/work/census-stage5/final"
prior="$core/work/census-stage4/final"
manifest="$core/work/research-yield/source-inputs/source_manifest.json"
profile=config/research_profiles/au_ree_census_display_v1.json
bindings=config/research_profiles/resource_bindings_v5.json
release=bdb9e868e5cb2a514e608231db35cdb27c3e77b2fc9427f7dbb444409f511c78
export PYTHONPATH=src

# PASS。呼叫 build、archive、兩次 replay --offline、三次 build-display、compare、build-site、verify-site。
bash /workspace/scratch/census-stage5/run_candidate.sh "$out" web

# PASS。原件 4 案是拒收檢查，不能產生合格正式結果。
python tools/audit_resource_statement_holds.py --profile "$profile" --manifest "$manifest" \
  --assets "$out/cas" --build "$out/build" --out "$out/resource-holds.json"
python tools/verify_scientific_yield.py --baseline "$prior/build" --candidate "$out/build" \
  --sources "$manifest" --cas "$out/cas" --archive-root "$out/archive" --release-id "$release" \
  --replicas "$out/cas" "$out/replica-cas" --replays "$out/cold-a" "$out/cold-b" \
  --display-views "$prior/display/census_display.json" "$out/display/census_display.json" \
  --out "$out/independent-scientific-yield.json"

# PASS，379 tests，0 skipped。
env AU_REE_STAGE5_WORKSPACE="$core" AU_REE_STAGE5_V2_ROOT=/workspace/AU_REE_V2_reference \
  AU_REE_STAGE5_BASELINE_BUILD="$prior/build" AU_REE_STAGE5_CANDIDATE_BUILD="$out/build" \
  bash "$core/work/census-stage5/verification/run_exact_suite.sh" "$PWD"

# PASS，三組各三次，依輸出辨別相同工作與擴增工作。
python -B tools/census_stage3_engineering.py \
  --base-ref 69a111dd56420d364c1ddf2547fe81888b4e51ed \
  --candidate-ref dfcc53791c10bcbd75faf6337a5c7516bd8073f6 \
  --base-profile "$profile" --bindings config/research_profiles/resource_bindings_v4.json \
  --candidate-profile "$profile" --candidate-bindings "$bindings" --manifest "$manifest" \
  --assets "$core/work/fresh/source-store" --runs 3 --out "$core/work/census-stage5/engineering"

# PASS。F5 的兩個正式入口均保持 BLOCKED。
env AU_REE_CHROMIUM=/usr/bin/chromium \
  AU_REE_PLAYWRIGHT=/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs \
  AU_REE_STAGE4_CAS="$out/cas" AU_REE_STAGE4_CAPTURES="$core/work/fresh/source-store/captures" \
  AU_REE_CANDIDATE_RELEASE_ID="$release" node web/tests/browser.mjs "$out/web-data" --stage5
python /workspace/scratch/census-stage5/verify_faults.py --revision "$out" --sources "$manifest" --out "$out/faults"
python /workspace/scratch/census-stage3/pack_preview.py "$out/preview" \
  "$out/census-stage5-candidate-preview.zip" "$out/restored-preview"
python -m au_ree_v3 research verify-site --mode candidate --site "$out/restored-preview" \
  --out "$out/restored-preview-verification"
```
