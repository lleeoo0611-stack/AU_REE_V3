# Census 跨專案可比較性與證據缺口驗收

Stage 6 延續 93 案及 2026-11-28 Asia/Taipei 截日。Git 起點 `c8bce8a893358a63bf2c587e44e0bc3f84523db7`。Stage 5 封存候選 `bdb9e868e5cb2a514e608231db35cdb27c3e77b2fc9427f7dbb444409f511c78`，其程式提交 `33587f23eda8243ad7713f2a5524f93ac9b2ce65`。兩次基準冷重播與 186 個 R1／S1 鍵核對可追溯。原 CLI 在初始化唯讀檔案系統時退出，實際開發由目前工作區直接執行。 [基準工作清冊](census_criterion_evidence.md)

## 科學成果

| 指標 | Stage 5 | Stage 6 | 淨變化 |
|---|---:|---:|---:|
| 數值候選／已驗證觀測／正式選值 | 53／37／31 | 55／39／33 | +2／+2／+2 |
| 93×51 Census 直接科學格 | 16 | 18 | +2 |
| 核准衍生科學格 | 3 | 4 | +1 |
| 既有已驗證描述格 | 27 | 27 | 0 |
| R1／S1 可比較鍵 | 4／2 | 4／2 | 0／0 |
| 完整 R1＋S1 案數 | 2 | 2 | 0 |
| 六構面完整案數 | 0 | 0 | 0 |

Nolans Bore 2017-06-07 版同一聲明的 1% TREO cutoff、JORC 合併分類及乾全礦證據齊全。新增 **56 Mt** 與 **2.6% TREO**，依已核准的 `contained-treo-from-mt-pct-v1` 推得 **1,456 kt**，原報精度區間 **1,415.25–1,497.25 kt**。2017 原表缺直接報告的資源含量，因此該案 R1 仍為 `INCOMPLETE_SELECTED_RESOURCE_BUNDLE`。同一原件的 NdPr 類別不能支持 S1。 [原件與公式](nolans_resource_annex_evidence.md) · [ASX 2017 發行方原件](https://announcements.asx.com.au/asxpdf/20170607/pdf/43jsp1xldvw6rk.pdf)

Goschen Area 1 的氧化物以原位 THM 為分母。Mt Weld 的四氧化物屬 Ore Reserve，後續兩個資源估算版次仍缺合格 S1 分子。Wolverine 後續重刊支持 Dy 840 ppm 的量級，原表 Dy 衝突與三個 Y₂O₃ 區間的矛盾仍在。Yin 同 cutoff 兩個原報含量互斥。Yangibana 資源總量跨 cutoff，修訂 DFS 還出現分類及含量衝突。North Stanmore 的 32.6 kt 屬 Ore Reserve。Dubbo 的原報 TREO 基準排除 Y₂O₃。 [S1 證據](resource_composition_evidence.md) · [R1 對照及時間資格](resource_comparability_challenge.md)

Census 原有 4,743 個格鍵皆核對。正式科學變更為上述三格，另有 Nolans 與 Dubbo 六格來源描述更新，逐格收據另記版本血緣。獨立 Decimal Oracle 驗證 R1／S1 的所有 186 鍵，以及全部正式準則共 837 個位置。兩案固定與完整母體的 SAW、TOPSIS、PROMETHEE II、三種權重情境及敏感度結果一致。四個其他面板仍為 `INSUFFICIENT_COHORT`。新增正式比較鍵與第三個完整案例兩項假說均為 `FAIL / HYPOTHESIS_NOT_SUPPORTED`。 [母體驗證方法](census_comparable_cohorts.md)

## 來源版本與盲評

固定來源清冊原有 144 個 SHA、148 筆取得證據。實際研究擴充版驗證 147 個 SHA、152 筆擷取，保有原 148 筆紀錄。其他 S1 與 R1 取得保存在各自的獨立佐證清冊。原生針對性解析新定位 Yin／Gifford 八列，把 Goschen 的 14 個 assay token 綁定正確的 THM 分母。固定工作清冊的 141 個待綁定位置及 14 組缺結構項維持原量。

| 獨立留出 | 基準端到端 TP／FP／FN／TN | 最終端到端 TP／FP／FN／TN |
|---|---|---|
| Stage 3 歷史家族 | 2／0／12／20 | 2／0／12／20 |
| Stage 5 盲留出 | 0／0／16／25 | 0／0／16／25 |
| Stage 6 新盲留出 | 0／0／6／54 | 0／0／6／54 |

Stage 6 先封存 8 家族、3 筆列層正例與 11 筆列層反例。給定定位仍有一筆礦砂礦量誤映射，未流入端到端或正式採納。S6-2 評估完整性通過，跨家族泛化假說未獲支持。正式採納 TP、FP、FN、TN 及準確率因缺獨立正式分母維持 `UNMEASURED`。 [分層與原件定位](native_resource_context_binding.md)

三組查找合計取得 13 筆公開收據與 11 個相對於固定清冊的新 SHA。研究候選新增的擴充展示由 40 增至 45／837 格，新增 Nolans 原報級別、cutoff、範圍、估算日及化學基準。9 筆已隔離的背景條目仍可在候選查核。既有地理影子資料執行精確父清冊比對，正式位置與距離未獲新證據。

## 重播與量測

候選 `8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3` 的研究運算提交為 `a7efe276563ff18b8b15c7a8d0d660ec8f0d8445`。兩份本機 CAS 均讀回 321 個封存物件，兩次離線重播比較八項科學結果，四項展示輸出一致。四次不同 Python hash seed 與快取情境皆重現科學指紋。獨立 SQLite 完整性及外鍵檢查通過。真實 Chromium 測試通過兩個候選切換、Nolans 新值的原件連結、錯 SHA 或資產回滾及行動版。候選 ZIP 重解與驗證通過，SHA-256 `00c7a9559b2fa3d7e3a99b37e84b3d721782fe4d7f6f6c4ec246ee420462416a`。

| 同範圍，固定 Stage 5 清冊及 v5 綁定，三次交錯中位 | Stage 5 | Stage 6 |
|---|---:|---:|
| wall 秒 | 11.459 | 11.120 |
| CPU 秒 | 11.348 | 11.329 |
| 峰值 RSS KiB | 97,532 | 97,668 |
| 冷／暖 PDF 解碼 | 22／14 | 22／14 |
| 冷／暖實體頁 | 1,487／1,254 | 1,487／1,254 |
| SQLite 語句 | 7,305 | 7,305 |
| 可達靜態／執行相依邊 | 152／116 | 152／116 |
| AST 分支上限／P90 | 61／19 | 61／20 |
| 靜態循環／執行循環／重複 AST 群組 | 1／1／1 | 1／1／1 |

同範圍科學比較差異為零。工作負載的作業系統檔案快取未控制。擴張組的完整建置／封存各為 18.743／4.043 秒，兩次冷重播為 22.055／20.255 秒，首次展示建置為 8.229 秒。另用三種有效 hash seed 比較擴張組資源冷暖快取，解碼 23→14 份 PDF、1,518→1,254 頁，SQLite 各 8,317 語句。新正式觀測增量為兩筆，擴張組單次全體建置攤提為每筆 9.372 秒。資料涵蓋全母體及原資料驗證，無法從這個攤提推定新增流程的純邊際成本。

完整原件材料及適用測試 **397 項通過，零跳過**，用時 135.168 秒。整合過程修正過來源清冊 helper 名稱衝突，7 項取得／組合測試重跑通過。擴充網站另保留固定地理證據輸入，修正擴充來源後的顯示綁定，並讓新增血緣 UI 驗證在獨立瀏覽器情境中執行。所有最後的本機資料、CI `SUCCESS`、條件跳過及可查驗的實際檢查各自保存。計費金額為 `UNMEASURED`。 [機器驗收收據](census_comparability_verification.json)

## Gate 新研究候選狀態

| 項目 | 狀態 |
|---|---|
| S6-0 基準及 186 鍵清冊 | PASS |
| S6-1 原件、科學判定及採納 | PASS |
| S6-2 獨立評估完整性 | PASS |
| S6-3 全量投影及決策對照 | PASS |
| S6-4 重播與候選網站切換 | PASS |
| S6-5 同範圍工程、收據及整合 | PASS |
| F0–F4 | PASS |
| F5／歷史 O2 | BLOCKED／FAIL |
| 新增正式比較鍵／完整面板擴充／未見家族泛化 | FAIL，原因 `HYPOTHESIS_NOT_SUPPORTED` |
| 全面正式採納泛化 | UNMEASURED |
| GIS 正式距離計算 | NOT_RUN |

兩份 CAS 同屬本機故障域，正式發布與網站指標受 F5 阻擋。後續優先補 Goschen 的同聲明全礦組成、Mt Weld 的資源而非儲量組成、Yin 的含量勘誤以及分開披露的 Nolans 2017 同版直接含量。上述來源待核狀態依逐案證據維護。

[基準清冊 PR #50](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/50) · [原件採納 PR #51](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/51) · [原生與跨發行方證據 PR #52](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/52) · [母體與預覽 PR #53](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/53) · [檔案處置清冊](census_comparability_file_lifecycle.csv)

程式驗收的實際工作路徑為 `/workspace/AU_REE_V3/work/census-stage6/verified`。隨附 `reproduction/` 保留建置、封存及回滾命令，`verified.log` 與 `verified-tests.log` 保存操作結果，均位於 `work/census-stage6/`。任一重跑請用新輸出路徑。主要命令如下。

```bash
root=/workspace/AU_REE_V3
out="$root/work/census-stage6/verified"
extra="$root/work/census-stage6/additional-sources"
release=8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3
export PYTHONPATH=src

# 固定 a7efe27，build、archive、雙 CAS、雙冷重播、三次展示驗證、compare、候選網站。
bash /workspace/scratch/census-stage6/run_candidate.sh "$out" web

python tools/verify_panel_cohorts.py --baseline "$root/work/census-stage5/final/build" \
  --candidate "$out/build" --delta "$out/delta" --policy src/au_ree_v3/fresh_decision_policy_v1.json \
  --sources "$extra/combined/source_manifest.json" --cas "$out/cas" --out "$out/independent-cohort-comparison.json"

# 同一固定輸入，兩版各三次，另有剖析與快取配對。
python -B tools/census_stage3_engineering.py --base-ref c8bce8a893358a63bf2c587e44e0bc3f84523db7 \
  --candidate-ref a7efe276563ff18b8b15c7a8d0d660ec8f0d8445 \
  --base-profile config/research_profiles/au_ree_census_display_v1.json \
  --bindings config/research_profiles/resource_bindings_v5.json \
  --manifest "$root/work/research-yield/source-inputs/source_manifest.json" \
  --assets "$root/work/fresh/source-store" --runs 3 --out "$root/work/census-stage6/engineering-a7efe27"

# 候選預覽與正式拒收入口皆實際檢查。
python /workspace/scratch/census-stage6/verify_faults.py --revision "$out" \
  --sources "$extra/combined/source_manifest.json" --out "$out/faults"
python /workspace/scratch/census-stage3/pack_preview.py "$out/preview" \
  "$out/census-stage6-preview.zip" "$out/restored-preview"
env AU_REE_CHROMIUM=/usr/bin/chromium \
  AU_REE_PLAYWRIGHT=/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs \
  AU_REE_STAGE4_CAS="$out/cas" AU_REE_STAGE4_CAPTURES="$root/work/fresh/source-store/captures" \
  AU_REE_CANDIDATE_RELEASE_ID="$release" node web/tests/browser.mjs "$out/web-data" --stage6
```
