# Codex 執行提示詞：重建 AU_REE_V3（Migration Baseline & Acceptance v1.1）

**請直接開始執行，不要只提出建議、目錄草案或未驗證的開發計畫。**

## 工作目標與授權

- **目標 Repository**：`lleeoo0611-stack/AU_REE_V3`，以執行當下的 `main` 為開發主線。
- **唯讀參照 Repository**：`lleeoo0611-stack/AU_REE_V2`。絕不修改、提交、推送或清理 V2 原始儲存庫。
- **權威執行規格**：本文件下方的《AU_REE_V3 Codex 遷移基準與驗收契約 v1.1》。先完整讀取，再按照其中第 0–11 節與 G0–G5 驗收實作。建議將其版本化保存於 V3 `docs/migration/migration_specification_v1_1.md`，保留內容 SHA。
- **治理規範**：遵守 AU_REE 開發治理規範 v3.0，科學正確性、證據可追溯性、風險分級、資料自動採納、漸進驗證與功能導向命名均為必要條件。
- **當輪主要可量測目標**：建立可重播的 V2→V3 遷移契約與最小真實資料流程，完成獨立的科學等價性驗證；以同功能範圍量測工程改善。不要宣稱第一輪已完成全量 V3。

## 固定 V2 參照

請依照規格實際核對下列完整 SHA，**不要讀 V2 `main` 或最新分支替代**：

| 用途 | 完整 Commit SHA |
|---|---|
| 工程遷移 | `2af6f611e51eb4ac9dda73419d1afeba70d1307e` |
| 2026-11-13 Census 科學發布 | `0dff6beb5aca52e565c37289e3ab295737e76942` |
| 2026-11-05 Phase 22 子集重播 | `dcda87e81e1250d443f73c1984faaa7beaa19930` |

既有證據：2026-11-05 的子集已有 538/538 份來源核驗與 16 組 MCDA 面板重播收據；2026-11-13 封存 Census 是 93 案 × 51 欄、2,500 格有值、1,715 格不適用、528 格適用但缺值，715 份鎖定原件中曾核驗 696 份，尚缺 19 份，且 `summary_rules.json` 封存指紋存在差異。**這是 V2 既有收據的狀態，不代表你目前執行環境已具備原件，必須重新核對。**

## 實際執行順序

### A. Migration Baseline：先確立獨立對照基準

1. 從 V3 `main` 建立短期功能分支 `feat/migration-baseline`，固定 V2 參照、研究截日與資料指紋；依規格 G0 產生機器可讀的來源可得性報告。
2. 逐檔盤點 V2 的 `src/`、`tools/`、`config/`、`data/`、`R/`、`tests/`、`docs/`、`.github/workflows/` 和 `results/`，產生 `file_disposition.csv`。依實際引用與重播用途分類 `KEEP / REFACTOR / MERGE / ARCHIVE / REMOVE`，不可僅依 Phase/日期檔名判斷可刪除。
3. 以研究**能力**而非檔案為單位建立 `capability_matrix.csv`，對每一項來源、PDF/NLP、本體論、觀測、正式選值、Census、GIS、MCDA、敏感度、實驗與重播功能列出 V2 入口、測試、V3 去向及驗證狀態；尚未移植者不得隱藏。
4. 建立逐欄 `schema_crosswalk.csv` 與版本化 JSON，保留 Project/Observation IDs、來源 SHA、原始精度、材料分母、化學基準、statement/scope、不同日期及各類空值語義。
5. 建立 `tests/golden/`：固定真實正例、真實拒收/阻礙案例及獨立標記的合成反例。依規格優先核對 Wolverine、Mt Weld、Mangaroon–Yin、North Stanmore 及至少一個不同材料/氧化物基準案例；實際 `case_id`、SHA、預期值、截日必須由鎖定原件及 V2 收據確認，不得硬編造。
6. 實作獨立 `Reference Oracle`，只從已凍結 V2 原始成果與清冊取得 expected，不能以 V3 科學演算法生成預期值。加入故障注入測試，確認 SHA 錯誤、Scope 錯誤、NULL→0、錯誤截止日及排名改變會導致 FAIL。
7. G0 通過後建立一個可審核的 PR。若後續 B/C 需要其程式，必須先按 repo 政策整合，避免形成 V2 式多層未合併 PR。

### B. V3 Core Foundation：建立真正可執行的科學契約

1. 建立可安裝的 `au_ree_v3` Python 專案與薄 CLI；正式程式按責任命名，不要移入整套 V2 原始檔案或建立 `phaseXX_*.py` 等永久功能模組。
2. 建立 Source、Entity、Project/Deposit Scope、RawObservation、EvidenceAnchor、NormalizedObservation、ValidatedObservation、AcceptedResearchValue、DerivedValue、ResearchRelease 等**版本化資料契約**。不可將實測、候選、推估與正式採納混為一類。
3. 實作一個受控的正式 SQLite Store、Schema Migration 與唯讀 V2 Import Adapter。確保 ID 穩定、同版重播冪等、失敗回滾、外鍵完整、來源不能被不同 SHA 默默覆蓋。實作 G1 正例與反例的單元測試。
4. 確保來源不足、科學衝突與程式錯誤採不同狀態，不因個別缺件阻止其他獨立項目。通過 G1 後交付短期 PR，整合到 `main`。

### C. Verified Vertical Slice：證明 V3 不是空殼

1. 在固定同版且**實際可用**的來源樣本上，完成 `Source → Document → Candidate → Normalization → Evidence Validation → Accepted Value → SQLite → Census` 的最小端到端執行。
2. 新版應共用文件解析與觀測契約，避免 V2 的重複 PDF 解析、跨模組內部呼叫與龐大函式。保留經核驗的科學規則，必要時重構其實作。
3. 將 V3 真正執行的結果交由**獨立 Oracle**與 Golden expected 逐筆比對：ID、值、單位、材料/化學基準、聲明、scope、來源定位、日期、正式選值、拒收理由及逐格狀態。不得把複製 V2 CSV 說成 V3 重算。
4. 執行兩次同輸入的清潔重跑、交易回滾、錯誤隔離、Schema Crosswalk 和故障注入測試；符合 G2 才宣稱 `VERTICAL_SLICE_VERIFIED`。
5. 僅在**兩版已通過相同 fixture**的能力範圍，比較模組相依、AST 複雜度、重複函式、耗時、記憶體與 CI 成本。未遷移的功能要列為能力缺口，不得宣稱因程式少而維護改善。

### D. 後續全量遷移與發布

A/B/C 完成或可獨立並行時，繼續依規格推進 Phase 22 的 G3、2026-11-13 全量封存的 G4，以及未遷移的來源、GIS、MCDA、技術、NLP、本體論和 ML 能力。對每一個研究能力都用固定資料、既有規則與獨立 Oracle 驗證，之後才將其列為正式可用。

完整原件或歷史設定缺失時，G4 保持 `BLOCKED`；可以發布**已驗證的局部研究版本**，但不可稱為 93 案完整歷史重播。只有符合 G5 的能力覆蓋、科學驗收、不可變發布與可演練回滾條件，才可宣稱 `FULL_V3_CUTOVER_APPROVED`。

## 強制執行規範

- 不得修改 V2、覆寫任何凍結成果、虛構原件或觀測、使用不同 SHA 的文件代替歷史原件。
- 已核准規則可自動採納正式資料，不需要逐筆人工簽核；證據不足的資料保留 Candidate／Shadow。只在真正無法由既有規則決定研究方法時才提出研究者決策需求。
- 如缺少網路、原件、執行環境或 PR 權限，將相依項標 `BLOCKED`／`NOT_RUN` 並繼續其他獨立工作；不得把未執行的驗證寫成 `PASS`。
- 允許合理新增模組、依賴或替代演算法，必須記錄必要性、可重建性及影響；不以零新增檔案或過度精簡作為成功標準。
- 如需拆成多輪，保持每輪有一個可量測目標、固定基準、可審核 PR 和真正執行的測試收據；不可只交付設計文件就稱重建完成。

## 交付與驗收回報

在 V3 建立 `docs/migration/implementation_report.md`，逐項報告：

1. V2/V3 完整 commit、來源／規則／Schema 指紋、研究截日與真正取得的原件數量。
2. G0–G5 狀態：`PASS / FAIL / BLOCKED / NOT_RUN / UNMEASURED`，附命令、輸出及原因；G5 預設未達條件不得宣稱切換完成。
3. Golden 案例逐筆驗證、獨立 Oracle 差異、Schema Crosswalk 覆蓋與 Capability Matrix 的 `VERIFIED/PARTIAL/DEFERRED/BLOCKED/UNMAPPED`。
4. 科學增益：新增可驗證觀測、可比較 Project–criterion、有效面板樣本與排名差異；未量測時明確記錄。
5. 工程增益：相同功能範圍的複雜度、相依、重複邏輯、執行時間、記憶體、CI 成本；不可與工作量不相同的 V2 全域指標直接相減。
6. 每項新增、修改、合併、封存、移除的檔案與理由；剩餘阻礙、下一輪優先事項、PR 連結與真實 CI 狀態。

**請先閱讀本文件後半的完整 v1.1 規格，再開始真正建立檔案、執行測試、產出收據、提交可審核的程式變更。不要向我詢問你能依現有證據與核准規則自行決定的非科學性事項。**


---

# 完整規格（同檔，可直接交給 Codex）

# Codex 執行指令：AU_REE_V2 → AU_REE_V3 遷移基準與驗收契約（v1.1，補強版）

> 執行位置：`lleeoo0611-stack/AU_REE_V3`  
> V2 參照倉庫：`lleeoo0611-stack/AU_REE_V2`  
> 總目標：逐步重建 V3 正式研究能力，避免 V2 的歷史技術負擔。第一輪先交付**可執行的遷移基準、Golden regression fixtures、獨立參照驗證、Schema Crosswalk、功能能力清冊、科學驗收工具及最小垂直流程**；不宣稱首輪已完成全系統遷移。  
> 執行授權：可在 V3 開功能分支、建立/修改檔案、執行測試與提交 PR；**不得修改 V2 的遠端分支、提交或封存成果**。禁止覆寫 V3 `main`、force push、未驗證便合併。  
> 按「AU_REE 開發治理規範 v3.0」執行；本提示詞是具體遷移契約，不取代科學治理。

## v1.1 補強摘要與執行策略

此版本延續 v1.0 的 G0–G4，**新增 Golden Dataset／獨立 Reference Oracle／逐欄 Schema Crosswalk／Capability Matrix／等功能工程基準／G5 切換條件**，避免只靠程式數量下降或新程式自行驗證就宣稱遷移成功。

- **Golden Dataset 是固定回歸資料集，不等於獨立科學真值。** 已凍結 V2 的行為是遷移等價性基準；獨立標註的 accuracy/recall 是另一項研究驗證，不可混淆。
- **Reference Oracle 必須獨立於 V3 的新實作**：預期值來自不可變 V2 原始發布、固定收據及可查核 V2 測試，而不是由 V3 的演算法推導；比較器不得 import V3 的科學判斷函式。
- **同功能範圍比較**：只有通過同一案例的功能，才可比較重構後的複雜度、相依與效能；未遷移功能必須在 Capability Matrix 保留為缺口。
- **三個短期工作單元**：A 基準／清冊，B 科學契約／SQLite，C 垂直流程／獨立驗證。每個工作單元可獨立產出可驗證成果；相依 PR 不得無限堆疊。
- **G5 為正式切換條件，不是首輪阻擋條件**。可先發布已驗證的局部 V3 研究版本，不得聲稱等同 V2 全量歷史成果。

## 0. 執行原則與不可變條件

1. 科學正確性優先。完整保留來源 URL、來源 SHA-256、Project ID、Observation ID、原始值、單位、化學及材料基準、scope、statement、各類日期、來源定位、規則與程式版本。禁止虛構資料、替換不同 SHA 原件、混淆資料層、默默更改歷史發布。
2. 正式資料依既有核准規則可自動驗證與採納，不設逐筆人工簽核；不確定資料歸入 Candidate／Shadow，保留衝突原因。涉及新的研究方法選擇時先產生獨立實驗版本，不自動改正式結果。
3. **證據缺件只阻擋受影響的驗證。** 保留 `PASS / FAIL / BLOCKED / NOT_RUN / UNMEASURED`，不能將 `BLOCKED`、未執行或有來源缺件的發布視為 `PASS`；其他可獨立執行的工作繼續。
4. 正式模組用功能命名，禁止以 Phase、日期、`final`、`fix2` 作永久模組名；先找現有責任與介面，再決定是否新增。允許必要新依賴、模組、演算法與重構，不以檔案數或程式行數作唯一驗收。
5. V2 只用作**固定唯讀參照**。不要從 V2 最新 `main` 取科學基準（該分支不是實際研究開發主線）；不整包複製 V2 的 `src/`、`tools/`、`data/`、`results/`、歷史分支與 CI。原始大型資產可留在經 SHA 核驗的外部資產庫，V3 用 manifest 引用。
6. 在每一批重構前後固定輸入與規則，量測輸出等價性、模組耦合、複雜度、重複邏輯與時間成本。未執行的測試不得寫成通過。

## 1. 必須鎖定的 V2 基準（使用完整 SHA）

| 角色 | 固定提交／資料 | 用途 |
|---|---|---|
| 工程功能參照 `V2_ENGINEERING_REF` | `2af6f611e51eb4ac9dda73419d1afeba70d1307e` | 現行可選擇移植之 Python、R、管線與審計紀錄 |
| 2026-11-13 科學發布程式 `V2_SCIENCE_REF` | `0dff6beb5aca52e565c37289e3ab295737e76942` | 與 93×51 封存 Census 的科學結果對照 |
| 已核驗子集重播程式 `V2_SUBSET_REF` | `dcda87e81e1250d443f73c1984faaa7beaa19930` | 2026-11-05 的可執行子集對照；以既有收據為準 |
| 2026-11-13 發布資料 | `results/ree-project-census/coverage-locked-20261125/`（於固定 V2 參照） | Census、逐格狀態、追溯與發布指紋 |
| 2026-11-05 Phase 22 | `results/phase22-2026-11-06/`（於固定 V2 參照） | 原件鎖、R 面板、Census 與研究分析 |
| Phase 10 稽核收據 | `results/phase10-2026-11-27/`（於 `V2_ENGINEERING_REF`） | 基準、來源恢復、品質、模組圖與子集重播 |

**必讀的機器可讀原件（依固定 ref 讀取，驗 SHA，不能從「最新版」代替）：**

- `results/phase10-2026-11-27/phase10_baseline.json`
- `results/phase10-2026-11-27/verified-subset.json`
- `results/phase10-2026-11-27/source_recovery_manifest.json`
- `results/phase10-2026-11-27/phase10_module_audit.json`
- `results/phase10-2026-11-27/repository_integration.json`
- `results/phase22-2026-11-06/snapshot_lock.json`
- `results/ree-project-census/coverage-locked-20261125/verification.json`
- `results/ree-project-census/coverage-locked-20261125/REE project census.csv`
- `results/ree-project-census/coverage-locked-20261125/field_status.csv`
- `results/ree-project-census/coverage-locked-20261125/field_provenance.csv`
- `config/research.json`, `config/summary_rules.json`, `config/sources.json`, `config/evidence_policy.json`
- `src/au_ree_v2/{store,evidence_model,observation_schema,normalization,selection_rules}.py`, `R/{methods,run_mcda}.R`。

**V2 已確認的參考事實，不得混用不同截日與分母：**

- **2026-11-13 的 93 案 Census**：93×51＝4,743 格；`PRESENT=2,500`、`NOT_APPLICABLE=1,715`、適用但缺值 `528`。當期適用分母 3,028，覆蓋率 82.56%；歷史固定分母 3,034，覆蓋率 82.40%。兩分母的差異來自 6 格改判不適用，需同時報告。
- 此次封存來源鎖為 **715 個唯一 SHA／805 筆來源參照**；先前核驗 **696 份原件**、缺 **19 份**（PDF 13、政府 WFS 4、政府 CSV 2）。缺件直接關聯 21 個 Project、120 個 Census 欄位格、21 筆資源觀測、3 筆影子候選；0 條直接 MCDA 準則來源鏈，間接選值影響仍應分開核對。
- `phase10_baseline.json` 表示 **2026-11-13 的 `summary_rules.json` 封存指紋與收據不符**；`research.json` 亦有版本差異。此問題不得由猜測設定、取最新設定或回填輸出掩蓋。設定不相容時記錄 `REPLAY_ENVIRONMENT_MISMATCH`／`BLOCKED`，並繼續其他驗證。
- **2026-11-05 的 Phase 22** 另有 538／538 必需原件核驗及 16 組面板重播收據（`verified-subset.json`）；它是第一個適合遷移的完整重播子集，不能將其等同 2026-11-13 的全量重播。
- 2026-11-13 封存的 **16 組面板排名**已核對，其他矩陣／準則／權重／敏感度產物有指紋收據；應逐項標示「實際重算」、「原檔重新雜湊」、「僅收據指紋」三種證據程度。
- V2 Phase 10 模組審計：工作樹 78 模組、343 條內部相依、0 循環；`resource_source._table_rows`、`cli._execute`、`spatial.summary_locations` 的自訂 AST 複雜度分別 314、233、202。這些是工程比較的**基準值**，不是 V3 必須沿用的模組結構。

## 2. 工作範圍與首輪交付物

### 2.1 建立 V3 工作分支

- 使用 V3 `main` 建立短期功能分支，首輪以 `feat/migration-baseline` 開始；後續 B/C 按第 10 節獨立整合，勿建立層層堆疊 PR。初次取得固定 V2 參照可採下列命令（在具 Git 存取權的環境執行；對 V2 不做推送）：

  ```bash
  git clone --filter=blob:none https://github.com/lleeoo0611-stack/AU_REE_V2.git ../AU_REE_V2_reference
  git -C ../AU_REE_V2_reference checkout --detach 2af6f611e51eb4ac9dda73419d1afeba70d1307e
  # 在 AU_REE_V3 工作樹：
  git switch -c feat/migration-baseline
  ```
- V3 工作單元 A 建立可執行 migration 工具與基準測試；工作單元 B 增加最小可安裝 Python 專案、薄 CLI、資料契約與 SQLite；C 完成垂直流程。若有獨立並行能力可以提前建立，不必等待無關原件。保留單一正式整合主線。
- 取得 V2 固定提交供**唯讀**比對，必要時使用相鄰目錄的 detached checkout、Git partial clone／sparse checkout 或 GitHub API；不要求先複製所有歷史資產到 V3 Git。
- 記錄實際 checkout 的完整 commit SHA、Git blob／資料檔 SHA、缺少哪些原件。未取得 V2 checkout 時，其他不依賴它的模組骨架與測試可繼續執行；對應項目標記 `BLOCKED`。

### 2.2 建立下列持久交付物（檔名依功能；不存在才建立）

| 類型 | 建議路徑 | 內容 |
|---|---|---|
| 遷移基準 | `config/migration/baselines_v1.json` | 固定 refs、研究截日、來源路徑及 SHA、預期科學計數、收據證據程度 |
| 科學契約 | `docs/migration/scientific_contract.md` | 不變量、資料映射、歷史結果及資料狀態定義 |
| 欄位對應 | `docs/migration/schema_crosswalk.csv`、`config/migration/schema_crosswalk_v1.json` | V2→V3 欄位、型別、NULL、精度、規則與驗證 |
| Golden fixtures | `tests/golden/` | 真實及合成案例、固定 expected、來源及證據等級 manifest |
| 功能清冊 | `docs/migration/capability_matrix.csv` | 所有 V2 正式研究能力的對應與驗證狀態 |
| 獨立 Oracle | `src/au_ree_v3/migration/reference_oracle.py` | 不使用 V3 科學演算法的基準比較器及故障注入測試 |
| 檔案清冊 | `docs/migration/file_disposition.csv` | V2 路徑、Git SHA、功能、相依、生命週期處置與理由 |
| 風險與驗收 | `docs/migration/acceptance.md` | 可執行驗收表、比較規則、已知阻礙與放行條件 |
| 架構邊界 | `docs/architecture.md` | V3 模組責任、輸入輸出、相依方向與重用策略 |
| 基礎資料型別 | `src/au_ree_v3/contracts/` | Source、Entity、Observation、EvidenceAnchor、Assessment、ResearchRelease 的版本化型別及序列化 |
| 遷移作業 | `src/au_ree_v3/migration/` | 唯讀清冊、基準核驗、契約映射、差異比較、有限樣本匯入 |
| 自動化驗證 | `tests/`、`.github/workflows/` | 無原件的契約測試、可用原件的整合測試與分級 CI |

不要因為上表而機械性建立空殼檔案。可合併同一責任的檔案，但需保留上述可核對的交付內容與路徑清單。執行期報表進入 `work/`（Git ignored）；正式封存以版本化 manifest 指向不可變結果／資產庫，不在 `data/` 根目錄堆積 Phase 收據。

### 2.3 V2 逐檔處置審計

- 掃描 V2 `src/`、`tools/`、`config/`、`data/`、`R/`、`tests/`、`docs/`、`.github/workflows/`、`results/`。
- 對每個檔案標示：`KEEP / REFACTOR / MERGE / ARCHIVE / REMOVE`；列出 `source_path`, `source_commit`, `source_blob_sha`, `role`, `used_by`, `dynamic_references`, `target_path`, `disposition`, `reason`, `verification`。
- 特別檢查直接／函式內 import、CLI 子指令、GitHub Actions、測試、`importlib`／動態路徑、研究凍結收據。**不能僅憑 `phaseXX` 或 `latest` 名稱判定刪除。**
- 重要的歷史原件、發行版輸出與重播必要資料至少保留固定 Git ref、外部不可變資產及 SHA 清冊；V3 不必保留 V2 歷史檔案的執行相容性。
- 針對高複雜度函式提出可測試的責任拆分；不得只以重新命名或移動檔案宣稱可維護性改善。

### 2.4 功能能力矩陣：避免「清掉檔案也清掉功能」

在 `docs/migration/capability_matrix.csv` 建立以**可執行研究能力**為單位的完整盤點，不能只靠檔案清冊。逐項掃描固定 V2 `src/`、`tools/`、`R/`、CLI 子指令、測試、CI、文件與研究發布契約，至少涵蓋：來源發現／取得／恢復、PDF/OCR/表格、NLP/本體論、Observation、證據採納、資源與技術數據、Census、GIS、MCDA、敏感度、研究實驗、發布及歷史重播。

每一列須包含：

`capability_id, domain, scientific_purpose, v2_entrypoints, v2_tests, v2_contract, v2_baseline_ref, v3_interface, v3_implementation, migration_decision, parity_fixture_ids, verification_command, parity_status, missing_reason, owner_module, notes`

- `migration_decision` 使用 `KEEP / REFACTOR / REPLACE / MERGE / ARCHIVE / DEFER`；`parity_status` 使用 `VERIFIED / PARTIAL / DEFERRED / BLOCKED / NOT_RUN / FAIL`。**決策與驗證狀態分開**。
- 對 V2 已知的正式資料流，初次建表不得遺漏任何可由 CLI／CI／測試追蹤到的研究能力。未辨識的能力須列 `UNMAPPED` 並提供掃描結果，不能因沒移植就從分母消失。
- V2 檔案可 `ARCHIVE`，前提是其關鍵功能已由 V3 通過等價驗證，或者確定只支援歷史唯讀用途。證據與封存研究成果遵循不可變保存規則。
- 第一輪只須完成完整盤點與可用能力的對應方案；G5 全量切換前，所有正式必要能力須 `VERIFIED` 或有明確、已核准的範圍排除，不能保留未說明的 `UNMAPPED`。

## 3. V2 → V3 資料契約（實作與驗收）

1. **原始來源**：`source_key`, `url`, `sha256`, `kind`, `publication_date`, `available_date`（如有）, `acquired_at`, `source_version`, `original_path`, `source_locator`。檔案 bytes 必須與 SHA 一致；不同 SHA 為不同版次，不能替換歷史來源。
2. **身分**：保持既有 Project ID、native namespace／ID、Company／Project／Deposit／Resource Scope 的分別，母子、別名與同名異地不得自動合併；若新 Schema 增加內部 ID，必須保留一對一或有證據的對應表。
3. **原始觀測**：完整保留既有 Observation ID、`variable`, `raw value`, `raw unit`, `basis`, `scope`, `statement_id`, `category`, `effective_date`, `source_sha`, `locator`, `status`, `transform_version` 與原始 JSON；以 Decimal 或無損字串保留精度。
4. **驗證後及正式值**：Raw → Candidate → Validated → Accepted 的狀態與用途可比性分開；來源／語義／正式准入的決策需有規則版本、依據與原因；衍生量保留公式、輸入 Observation ID、轉換因子、分母與適用範圍。
5. **時間語義**：區分估算有效日、公開日、可得日、取得日、研究截日及事件發生日。不可將下載日等同歷史公開日，不得以截日後資料回填舊發布。
6. **研究發布**：每次發布固定 `input_sha`, `code_commit`, `schema_version`, `rules_sha`, `cutoff`, `cohort`, `MCDA method`, `weights`, `seed`, `result fingerprints` 與 `validation_status`。
7. **拒收與衝突**：任何缺件、材料／化學基準不同、JORC 資源分類不明、不同截品、來源與數值證詞不一致時，只隔離相關候選，保留既有合法觀測及其他專案的執行。

遷移工具應使用 schema-versioned importer 與明確欄位映射；同一 V2 輸入重複匯入須具**冪等性**；交易失敗不得產生部分正式更新。新 V3 schema 不要求沿用 V2 資料表配置。

### 3.1 明確的 Schema Crosswalk（不能只寫概念）

建立 `docs/migration/schema_crosswalk.csv`（供人審閱）及 `config/migration/schema_crosswalk_v1.json`（供程式執行），逐欄記錄：

`entity_kind, v2_table_or_type, v2_field, v2_schema_version, v2_type, v2_null_semantics, v3_table_or_type, v3_field, v3_type, mapping_rule, transform_version, lossless, comparison_rule, reversibility, evidence_required, status, reason`

最低覆蓋：來源、原生 Entity、Project/Deposit/Scope 身分、關係、原始 Observation、原始值與單位、basis/material、statement/category/cutoff、SourceAnchor、有效／公開／可得／取得／研究截日、Candidate／Validated／Accepted／Derived／Shadow、版本化選值、Census 欄位及 MCDA 產物。明確拆分：

- V2 `observation-v3` 的讀取契約與 `observation-v1` 原始 ID 生成語義；V3 不得重新雜湊後默默替換已發行 Observation ID。
- `NULL`、未揭露、無資料、無效、不適用、衝突、來源缺件；不可全部合併成空字串或零。
- source SHA 與來源 key、Project ID 與 native Entity ID、材料分母與氧化物基準、Resource statement 與 scope、估算月與發布日。
- `float` 若原始輸入已有精度不足，保留原始字串、精度與不確定性，不得假裝反推精確小數；可無損映射的 Decimal 使用 Decimal/字串。

Crosswalk 必須由測試驅動，驗證必填欄位不遺失、不產生未記錄的一對多／多對一合併、既有 stable IDs 等價。禁止為了讓 V3 測試通過而重新詮釋 V2 科學規則。某欄位無法無損對應時，保存原始 payload、影響範圍及 `LOSSY_OR_UNRESOLVED`，禁止正式遷移該值；不妨礙其餘已核驗欄位。

### 3.2 Golden Dataset（真實固定樣本＋獨立合成反例）

建立 `tests/golden/`，並由獨立唯讀萃取工具依**固定 V2 commit 與原件**產生：

- `golden_manifest.json`：每筆 `case_id`、版本、截日、Project/Scope/Statement ID、來源 URL／SHA、原件可得狀態、必要頁／表／欄位置、原始預期輸出與比對方式、輸入內容雜湊。
- `golden_cases.jsonl`：固定的真實資料案例；必要時僅保存來源 hash/locator，不在 Git 重複提交大型受版權保護 PDF。
- `golden_expected.jsonl`：只從固定 V2 的既有正式結果、經確認的原件／測試及封存收據建立，保存 `expected_source`、`evidence_tier`、`reference_sha256`。**不可用 V3 生成 expected**。
- `synthetic_rejections.jsonl`：明確標記 `synthetic=true` 的反例；不得混算真實觀測新增量或抽取準確率。
- `oracle_manifest.json`：每個 expected 的來源檔路徑、Git ref、原始雜湊、計數及生成器版本。Golden 更新須增版並留下逐案例差異，不得覆寫舊版。

**必查案例類型**（若固定鎖中不存在可用原件，記錄該案例 `BLOCKED` 並以相同科學風險的已核驗案例替代；替代不得混研究截日）：

1. **Wolverine**：同估算的礦石量、品位與已採納 TREO 值（具體採納期、SHA、數值以所選 V2 版本/收據核定，不能硬塞到不相同的 Phase 22 profile）。
2. **Mt Weld**：資源表、精度、TREO 定義、截日與證據不完整時的正確候選／拒收。
3. **Mangaroon–Yin**：Yin 與 Gifford Creek、相鄰表格及總量／分項 scope 隔離。
4. **North Stanmore**：TREO、TREO+Sc₂O₃、複合截品及估算有效月差異。
5. **另至少一組**：材料分母或氧化物轉換（如 Wimmera、Splinter、Burracoppin），以固定證據與可用性為準。
6. **真實負例**：來源鎖中相同 key 但不同 SHA、未取得原件／衝突聲明、缺定位的候選；不能把壞資料偷偷改成成功值。
7. **合成反例集合**：同名異地、不同 Project/Deposit scope、Exploration Target 冒充 MRE、整礦／重礦物混分母、TREO／氧化物混用、跨頁否定、截日後資訊、來源 SHA 不符、重複匯入、交易中斷。

每個實際案例均須附帶 **PASS 所需的完整原件或可獨立核對的 V2 原始收據**；僅有案例名稱或 README 敘述不能算通過。Golden regression 衡量的是 V2/V3 行為等價，不是新的外部科學 gold truth。獨立模型 accuracy、recall 與 annotation holdout 無真值時維持 `UNMEASURED`。

### 3.3 獨立 Reference Oracle 與差異判讀

實作 `src/au_ree_v3/migration/reference_oracle.py` 或等價獨立 verifier；它只能讀入固定 V2 `golden_expected.jsonl`、封存結果與不可變 manifest，**不得呼叫 V3 解析器、Ontology、Validation、正式選值或 MCDA 核心函式**來產生預期值。可使用 Python 標準庫與獨立比對工具。

比對契約必須至少包含：

- 身分與語義：Project／Entity／Observation ID、statement、scope、category、物種、材料與化學基準、研究截日、證據有效性、來源 SHA 及 locator **完全一致**。
- 狀態：`PRESENT/UNKNOWN/NOT_APPLICABLE` 等逐格分類，以及 Candidate/Validated/Accepted/Derived/Shadow 與拒收原因；不得將缺值當 0、不得把不同拒收理由視為成功等價。
- 原始測量 Decimal／原始字串**精確相同**；經版本化公式的衍生 Decimal 依宣告精度比對；唯有已宣告浮點方法的計算量可依 `abs_tol=1e-8`, `rel_tol=1e-8` 初始容差比對，並將容差記入 contract。排名順序、並列集合、面板母體、方法、種子、權重輸入與已採納值需獨立比對。
- 對 CSV/JSON 先以明確主鍵排序並做型別穩定化，排除 run-id、耗時、暫存路徑等非科學性 metadata；**不能忽略會影響科學結果的欄位**。
- 輸出 `oracle_diff.json`／`oracle_diff.csv`，逐筆列出 `case_id`, `field`, `expected`, `actual`, `comparison_rule`, `severity`, `source_reference`, `v3_reference`, `reason`，以及正式准入和研究結果差異。

將 `compare-oracle` 的程式與 fixtures 自身加入測試：用**刻意插入的錯誤**（錯誤 SHA、錯 scope、NULL→0、錯 cutoff、微小科學差異、不同排名）證明它確實會 FAIL。Oracle 的結果可以證明**遷移等價性**，不能單獨證明 V2 原結果的外部正確性。

## 4. 分階段驗收 Gates（可獨立回報）

### G0 — 參照與資料可用性（首輪必做）

- 所有 Git refs 用完整 SHA 鎖定、可讀時核對預期提交；讀取並雜湊可用的基準檔與收據。
- 驗證 V2 的 93×51 Census、2,500／1,715／528 格及雙覆蓋率口徑；解析 `phase10_baseline.json` 時以機器資料為權威，不將 README 描述代替原始比較。
- 輸出每個資產 `EXACT_SHA / MISSING / WRONG_SHA / NOT_REQUIRED_FOR_SCOPE`；19 份缺件與設定不符仍保留為明確阻礙。
- 產出 `golden_manifest.json` 的來源清單與 Oracle 參照指紋；有 `MISSING/WRONG_SHA` 的案例不得直接納入「原件已核驗」的分母。
- **PASS 門檻**：可取得的 manifest 與封存 CSV 指紋正確、計數一致、資料截日不混用，Golden／Oracle 的原始輸入可追溯。原件不足的「完整 2026-11-13 原始來源重播」仍為 `BLOCKED`，不阻止 G1、G2。

### G1 — V3 科學資料契約與 SQLite（首輪必做）

- 以固定的小型**真實且已核驗樣本**，及另外標記的合成反例檢驗：ID 穩定、來源證據可追溯、精度不損失、時間不向前洩漏、狀態分離、版本化選值與失敗交易回滾。
- 最小反例覆蓋不同 Project／Deposit、同名不同 scope、JORC Mineral Resource vs Exploration Target、整礦 vs 重礦物／細粒分母、TREO vs 不同氧化物基準、不同 cutoff、跨頁證詞、來源 SHA 改變、遺失來源、同一觀測重複匯入。
- SQLite `PRAGMA integrity_check` 結果 `ok`、`foreign_key_check` 0 筆；不得改變原始觀測及來源 SHA。
- 強制 `schema_crosswalk.csv/json` 的實際欄位驗證、原始 ID 版本語義、NULL 與精度回歸，Capability Matrix 至少對正式執行入口完整建表；不得要求所有能力已經移植。
- **PASS 門檻**：所有契約與反例測試通過，對已測欄位無未說明的資料遺失、無證據正式採納或合法資料撤銷；已知未遷移能力列入差距。

### G2 — 最小完整資料流（首輪盡量完成，不等待全量原件）

- 選取已核驗的同版 PDF 與相對應 Project／Resource Scope（候選不得虛構）；完成 **Source → Document → Candidate → Normalization → Evidence Validation → Accepted Value → SQLite → Census projection** 的最小端到端流程。
- 由同一來源與同一規則的 V2 結果建立對照，逐筆比較 ID、原值、單位、statement、scope、日期、來源頁／表／列、准入結果及拒收理由；新模型可以改良解析，但變更需隔離為實驗對照。
- 使用獨立 Reference Oracle 對正例、拒收反例及可比較性規則逐筆比對；比較器須能在故意注入錯誤的單元測試中 FAIL。若僅使用合成資料或回讀 V2 CSV 而未經 V3 執行資料流，不能標 `G2 PASS`。
- **PASS 門檻**：固定真實樣本的科學輸出等價、重播可重複、來源錯誤僅影響相關資料、Oracle 故障注入測試有效。缺原件而只能完成合成或 fixture 契約測試時，G2 標 `BLOCKED/PARTIAL`，不得宣稱完成遷移。

### G3 — 2026-11-05 Phase 22 完整可用子集（後續必達）

- 以固定 snapshot lock、相容來源資產及研究設定重播；資產齊全時核對 538／538 必需 SHA。
- 核對同一子集之 Census 相關值與狀態、被選 Project、16 個面板及 MCDA 計算。對矩陣、criterion、weights、seed、draws、排名、同分及比較母體建立穩定排序下的語義對照。
- R／Python 數值比較使用同一方法和精度；最初可採 `abs_tol=1e-8`、`rel_tol=1e-8` 作為雙精度暫定門檻，並核對排名及決策狀態完全一致。若更動容差須記錄原因、原精度與差異，禁止藉放寬容差掩蓋科學差異。
- 由獨立 Oracle 核驗 V3 實際重新計算的矩陣與排名；封存檔案「重新雜湊」和「重新計算」分開報告。
- **PASS 門檻**：已明確界定的 Phase 22 子集科學輸出等價、來源指紋正確、關聯資料完整；有資產卻重播失敗是 `FAIL`，無資產則 `BLOCKED`，不能視為成功。

### G4 — 2026-11-13 完整 93 案封存（最高風險，後續完成）

- 固定 93 Project ID × 51 欄及所有原始 cell-state；逐格比較數值、缺值、不適用、來源鏈及精度。要同時輸出 82.40%（歷史分母）與 82.56%（重新分類分母），不得當成同一覆蓋率。
- 完整原件 SHA 和歷史 `research.json`／`summary_rules.json` 配置都能核對後，才進行完整源頭重播；現階段 19 份原件與設定不符要列出阻礙及受影響集合。
- 已保存的歷史結果可單獨做**輸出完整性核對**，但不得取代「從原始來源重播」的驗證。16 面板要清楚標示是重新運算、現存檔重新雜湊或僅有收據指紋。
- 若先完成 2026-11-13 的「封存輸出檔比對」可單獨標 `ARCHIVED_OUTPUT_VERIFIED`，但 G4 必須等原件與配置可追溯且完整源頭重播真的完成才能 `PASS`。
- **PASS 門檻**：相同科學輸入／規則的 93×51、證據鏈及所有受影響的 MCDA 結果等價。其餘已封存未變成果仍保持不變。

### G5 — 正式切換、回滾與版本發布（後續；首輪不阻擋）

- V3 可以發布 `PHASE22_SCOPE_VERIFIED` 等**明確限定範圍**的研究版本，但必須標示可用年份／母體／來源與不支援的功能；不允許用局部驗收取代 V2 全量正式基準。
- 宣稱 `FULL_V3_CUTOVER_APPROVED` 前，需 G3 與 G4 符合其宣告的完整範圍、功能矩陣必要能力逐項 `VERIFIED`（或有正式範圍排除及科學理由）、資料映射無未處置的損失、重播與發布契約完整、CI 實際通過、回滾流程經演練。
- 發布前建立不可變 release manifest：V3 commit、完整配置／資料／來源／Schema SHA、研究截日、面板母體、輸入與輸出指紋、驗證紀錄與環境版本。舊版 V2 及 V3 發布不應被覆寫。
- 回滾不做資料刪改；只把 `current` 指向先前已核驗的不可變發布，並保留失敗版本及錯誤報告。V2 為唯讀對照，V3 不要求長期維持 V2 全部 CLI 執行相容性。
- 有不可取得資產時，G4 保持 `BLOCKED`，仍允許不依賴 G4 的 V3 小範圍驗證與開發；不得宣稱 `FULL_V3_CUTOVER_APPROVED`。

## 5. 實作可執行的 CLI 與測試（以下是要新增的指令，不是 V3 已有功能）

請為 V3 新增一致的 migration 子指令，或提供等價可執行介面：

```bash
# 以下在 V3 repository 根目錄執行，V2_ROOT 為唯讀固定 checkout。
python -m au_ree_v3 migration audit \
  --v2-root "$V2_ROOT" --out work/migration/audit
python -m au_ree_v3 migration build-golden \
  --v2-root "$V2_ROOT" --out work/migration/golden-review
python -m au_ree_v3 migration verify-crosswalk \
  --v2-root "$V2_ROOT" --out work/migration/schema-validation
python -m au_ree_v3 migration verify-baseline \
  --v2-root "$V2_ROOT" --profile archived-census-2026-11-13 \
  --out work/migration/archived-census
python -m au_ree_v3 migration verify-baseline \
  --v2-root "$V2_ROOT" --profile phase22-2026-11-05 \
  --assets "$ASSET_LIBRARY" --out work/migration/phase22
python -m au_ree_v3 migration compare-oracle \
  --expected tests/golden/golden_expected.jsonl \
  --actual work/migration/v3-observations.jsonl \
  --out work/migration/oracle
python -m unittest discover -s tests -v
```

- `audit`：產生 module DAG、循環、函式複雜度（固定同一計量法）、重複函式候選、file disposition、capability_matrix，以及尚未確定是否有使用者的檔案。輸出完整 CSV/JSON，不只摘要。
- `build-golden`：從固定 V2 證據與發布產物建立 **候選 fixture／預期值清冊**，核對來源 SHA 與截日；不得使用 V3 科學演算法產生 expected；無原件案例標 `BLOCKED`。正式 fixtures 需審核來源與驗證狀態後版本化固定。
- `verify-crosswalk`：逐欄執行型別、NULL、ID、日期、無損精度及來源定位驗證，輸出 `schema_diff.json`；不應靠硬編碼放行。
- `compare-oracle`：執行與 V3 科學方法獨立的 Golden 科學語義比對，輸出差異與 FAIL/BLOCKED/NOT_RUN；當 `--actual` 未存在時報 `NOT_RUN`，不得造資料湊 PASS。
- `verify-baseline`：對比固定 ref 的 manifests、觀測、Census 與可用面板；每個子項列出 `PASS/FAIL/BLOCKED/NOT_RUN/UNMEASURED`、預期、實際、差異、證據路徑、執行時間與環境。
- `--assets` 應為**選用**參數；若未供應或缺件，仍可通過獨立的 manifest／CSV 核驗，並把需要原始 bytes 的項目標記 `BLOCKED`。不得默默改用網路當前版本或合成資料。
- CI 分 `static/unit`、`integration`、`frozen replay`、`publication` 四級；變更只跑受影響的必要級別。需交付 CI workflow 與本地重播方式。大型資產不存在時離線測試回報受阻，不可報綠燈代表完整重播。
- 若增加第三方套件，加入版本鎖、安裝指令及可重建測試；記錄 Python、R、Tesseract 與 OCR 語言資料版本／雜湊。不要假設網路與外部來源永遠可用。

## 6. V3 模組邊界（維持小而完整，禁止建立無作用空殼）

- `contracts`：跨模組型別與 schema；不依賴解析、SQLite 或 MCDA 實作。
- `sources`／`documents`：來源、文件解析與候選輸出；不得直接決定正式 MCDA 分數。
- `observations`／`ontology`：資料正規化、語義相容性、證據驗證及正式採納規則，保持可單獨測試。
- `census`／`spatial`：資料投影、空間量測與來源品質；不跨層呼叫 PDF 解析內部函式。
- `decision`：固定指標與評分、權重、R/Python 計算、敏感度；只讀已驗證且合適研究用途的輸入。
- `publication`／`migration`：發布契約、差異、版本管理與歷史重播；不覆寫 frozen results。
- `application`／`cli`：組裝與調度工作、錯誤分類；不得新增複雜的科學判斷分支。

可以在後續輪次新增或整合模組；不得為了達到目錄數量或程式行數而拆出重複／無責任的檔案。

## 7. 失敗分類、獨立工作與放行規則

| 事件 | 狀態 | 應做事項 |
|---|---|---|
| 原件無法取得 | `SOURCE_UNAVAILABLE` / `BLOCKED` | 記錄缺少 SHA、來源與影響對象；繼續其他來源、G1、局部 G2 |
| 來源存在但不同 SHA | `SCIENTIFIC_CONFLICT` 或來源不符 `FAIL` | 保存新版本紀錄，禁止替換歷史原件；舊版重播維持受阻 |
| 證據缺條件／範圍 | `EVIDENCE_INSUFFICIENT` | 留在 Candidate／Shadow，其他有效觀測繼續 |
| 研究設定／執行環境指紋不符 | `REPLAY_ENVIRONMENT_MISMATCH` | 分離設定、環境與來源因素，禁止宣稱精確歷史重播 |
| 程式或 Schema 錯誤 | `CODE_ERROR` / `FAIL` | 修復受影響模組並跑對應測試 |
| 新方法無實測增益 | `HYPOTHESIS_NOT_SUPPORTED` | 保存負結果、實驗版本與比較依據 |
| 未執行／缺獨立標註 | `NOT_RUN` / `UNMEASURED` | 清楚留下未量測的分母，禁止偽裝通過 |

**PR 放行政策：** 首輪 G0／G1 通過且 Golden/Oracle 來源契約及 Capability Matrix 已建立即可標示 `MIGRATION_FOUNDATION_READY`；G2 通過才可標 `VERTICAL_SLICE_VERIFIED`；G3 通過可標 `PHASE22_SCOPE_VERIFIED`；G4 通過可標 `FULL_ARCHIVE_REPLAY_VERIFIED`。完整功能、發布與回滾 G5 才可標 `FULL_V3_CUTOVER_APPROVED`。G3、G4 未達標應明確保持 `BLOCKED`／`NOT_RUN`，不得把首輪基礎建置標示為 `FULL_V3_PARITY`。只有受影響科學驗收全部通過並完成版本差異審查，才可發布相應範圍的正式成果。不要要求 G3/G4 就緒才能合併純工程基礎改動。

## 8. 工程效益與科學增益的量測方式

- **工程基準**：使用 `phase10_module_audit.json` 同一 AST 定義，對比相依邊數、循環、函式分支複雜度、跨模組重複群、核心函式的單一責任、工具入口數；同時計算每個修改的受影響測試數及 CI 實際耗時。避免將「減少模組數」當成唯一成果。
- **科學基準**：固定相同 source SHA、cutoff、schema 映射、規則與研究母體；統計正式可選觀測、可比較 Project–criterion、完整面板樣本、逐格輸出及排名差異。工程重構的目標可以是**科學等價、工程改善**；若本輪只建基礎架構，科學新增量可為 0，請如實報告。
- **性能基準**：記錄硬體、OS、Python／R／OCR、cache cold/warm、原件數量、資料量、峰值記憶體及執行時間；不把網路等待與純科學重播耗時混為一談。
- **品質基準**：區分合成案例、同代理自查、silver cross-source 參照與獨立 holdout。缺乏獨立真值時，accuracy／recall 必須記 `UNMEASURED`。

### 8.1 **等功能範圍**的工程比較（強制）

不得把 V3 功能尚未搬完造成的模組／依賴下降，當作架構改善成果。建立 `work/migration/comparable_engineering_metrics.json`，**先列測試功能覆蓋範圍，再列工程差異**：

- 每組比較宣告 `capability_id`、相同真實／合成 fixture ID、輸入來源 SHA、版本化規則、成功測試、V2/V3 實際呼叫入口、環境、快取狀態。
- 僅對兩版都能成功執行的相同能力，量測可到達的模組數／依賴邊、AST 複雜度分布、重複函式、核心入口數、峰值記憶體、純計算耗時及 CI 實測成本。
- 整個 V2 的 78 模組／343 邊僅作**背景全域基準**，不能與只實作少數功能的 V3 全域數字直接相減並稱為降幅。
- V3 尚未移植的功能以 `NOT_MIGRATED` 顯示能力缺口；同樣功能比較尚無法執行時標 `UNMEASURED`，不能宣稱效能改善。
- 效能報告分別列出冷啟動、暖快取、是否包含來源網路等待、硬體/OS/Python/R/OCR 版本。禁止用不同工作量或快取狀態製造加速數字。

## 9. 本輪完成條件與回報格式

Codex 應自動執行所有可獨立完成的部分，**不要為非科學性的檔名、模組布局、工具選型逐項詢問**；遇阻礙照規範記錄並繼續。除非存在無法依既有規則處理的真正研究方法選擇，否則以保守預設完成工作。

**本次 Codex 任務的最少交付（可分成 A/B/C 整合工作單元；無法完成的須分類回報）：**

1. V3 基礎工具可安裝、可執行單元測試、`main`／開發分支治理清楚。若只完成 A 應標示 B/C 尚未實作。
2. 固定的三個 V2 程式提交及雙截日研究參照可驗、科學基準與資料來源 manifest 可讀。
3. V2 的逐檔生命週期清冊及**Capability Matrix**（包含 V2 工具、設定與歷史產物；先分類，不任意刪除）。
4. V3 Source、Entity、Observation、EvidenceAnchor、狀態與研究發布的版本化最小契約；Schema Crosswalk CSV/JSON、SQLite 受控寫入與回滾測試。
5. `migration audit`、`build-golden`、`verify-crosswalk`、`verify-baseline` 與 `compare-oracle` 有明確、可執行或清楚 `NOT_RUN/BLOCKED` 的實作；Golden fixtures 與獨立 Oracle 有來源證明與故障注入測試。
6. G0 與 G1 的驗收結果；G2 應盡可能完成最小端到端案例；G3/G4 明確紀錄已完成、受阻或未執行，不得假報。
7. 更新 `README.md` 只呈現 V3 現行可執行入口與研究狀態；歷史說明放 `docs/migration/`，不得複製 V2 大量 Phase README。

**必交報告 `docs/migration/implementation_report.md`：**

- 完整 V2/V3 commit、輸入與設定 SHA、實際可用原件／缺件／受影響對象。
- G0–G4 各項 `PASS/FAIL/BLOCKED/NOT_RUN/UNMEASURED` 與執行命令、輸出收據。
- 與 V2 的科學等價性比較：Golden 案例個別結果、獨立 Oracle 差異、實際新增觀測／可比較欄位／樣本／排名差異（無量測即寫無量測）。
- Capability Matrix 的完整覆蓋率、`UNMAPPED/DEFERRED/BLOCKED` 能力清單及 Schema Crosswalk 的逐欄遷移狀態。
- 同功能範圍的模組 DAG、AST 複雜度、重複實作及 CI 耗時前後差異，並列功能覆蓋範圍；尚未比較的部分寫 `UNMEASURED`。
- 每一個新增、修改、合併、封存、移除檔案與理由。
- 已知阻礙、風險、下一輪最值得遷移的獨立工作單元。
- PR 連結、CI 真正執行狀態、可重播的本地命令與工作樹是否乾淨。

**禁止的驗收捷徑：** 由 V3 程式產生自己的 Oracle expected；用沒有參照的合成 fixture 宣稱真實資料等價；把未移植功能造成的模組數下降當成維護改善；複製舊 CSV 當成 V3 計算成功；以 2026-11-05 已驗證來源冒充 2026-11-13 的全量來源；使用當前下載內容替換歷史 SHA；為求零差異而改寫凍結收據；把候選值填入正式 Census；把跳過或未執行測試標為 PASS；為了變簡潔而刪除仍有重播價值的原始證據。

## 10. 開發順序與整合策略（避免重複 V2 的堆疊 PR）

依序交付三個**可獨立驗證、各自以 V3 `main` 為整合目標**的工作單元，能自動執行者應持續推進，不能因無關缺件停工：

| 工作單元 | 建議功能分支 | 主要交付 | 完成條件 |
|---|---|---|---|
| A. Migration Baseline | `feat/migration-baseline` | V2 refs／SHA、Golden 真實與合成案例、獨立 Oracle、Schema Crosswalk 草案、檔案與功能清冊、G0 | G0 PASS、Oracle 來源可信、未取得原件隔離 |
| B. V3 Core Foundation | `feat/core-evidence-contracts` | 契約型別、資料儲存、無損映射、交易與冪等、G1 與最小 CI | G1 PASS、無不明 ID／欄位變更、已知能力缺口可追蹤 |
| C. Verified Vertical Slice | `feat/verified-observation-pipeline` | 共用文件解析→候選→證據核驗→正式採納→SQLite→Census，與獨立 Oracle 比對 | 固定真實樣本 G2 PASS 才宣告 VERTICAL_SLICE_VERIFIED |

**不得從尚未整合的功能 PR 繼續長鏈堆疊下一個 PR。** 已通過相應測試且符合 repo 權限／分支政策者，應逐一整合到 `main`；無權限或需要外部合併時留下明確 PR、驗證證據與整合指引，其他獨立工作可以從 `main` 開分支繼續。不可 force push，不能為了追求表面完成而繞過保護機制。以上分支名稱是功能導向而非 Phase 導向；不需要空殼模組。

C 工作完成後，可逐步進行 G3 Phase 22 全面子集驗證、G4 93 案歷史發布重播，以及剩餘 GIS、MCDA、技術透明度與 ML/本體論功能遷移。功能完成後更新 Capability Matrix、以相同固定資料及獨立 Oracle 驗證，再納入正式 V3。G5 是未來的全量切換關口。

## 11. 開始執行

在 V3 `main` 基礎上立即建立 `feat/migration-baseline`。先固定三個 V2 SHA，執行 G0、Golden／Oracle 原件盤點、Schema Crosswalk 與 Capability Matrix；接著推進可獨立實作的核心契約和垂直流程。按風險分級驗證並產生可機讀收據，不因 19 份缺件中止其他工作。若執行環境無法建立多個整合 PR，至少交付一個可審核、功能明確且測試通過的 PR，明確列出後續 B/C 的入口及受阻原因。不得在未完成 G2/G3/G4 之前宣稱整體 V3 重建完成。
