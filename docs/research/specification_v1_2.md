# Codex 開發規格：AU_REE_V3 獨立研究基準與 93 案重新建檔

**規格版本：** `fresh-research-baseline-v1`  
**目標儲存庫：** `lleeoo0611-stack/AU_REE_V3`  
**歷史唯讀參照：** `lleeoo0611-stack/AU_REE_V2`  
**新研究截日：** `2026-11-28`（以 `Asia/Taipei` 定義研究日期；所有事件時間戳保留原始時區與 UTC 正規化值）  
**治理基準：** AU_REE 開發治理規範 v3.0  
**開發原則：** 直接執行可獨立完成的工作，按風險分級驗證；不得只交付草案、空模組或未執行的測試。

## 0. 執行前狀態與基準

執行前重新查閱 GitHub 狀態，不要假設 PR 已合併。

- V3 `main` 最近核對的提交為 `d35a55916754fd1adb02d564d6fcb143a0b1b692`。
- **PR #5** `feat/temporal-evidence-governance`：`b1045fddc31342d8adbc1f44045e982b9f64dd08`。時間資格功能及 CI 已通過，但檢查時仍是 **OPEN、未合併**。先將其以受控方式整合至 `main`，核對 `contracts.temporal.evaluate_temporal`、相關 Store／ResearchInput 介面及測試。若執行時已合併，不要重複實作。若發現衝突，只處理相容性並重跑受影響測試。
- **PR #4** `feat/frozen-decision-replay`：`b5015579933f73c2990718b1b9b2f47d9a04f2af`。其 SAW、TOPSIS、PROMETHEE II 及 deterministic leave-out 算法已有固定矩陣驗證；可擷取/整合可重用計算核心與測試，但**不得讓 V2 舊矩陣的完整重播成為 V3 新研究版本之必要條件**。整合前注意與 PR #5 的 CLI、CI 衝突。
- V2 可唯讀取得穩定 Project ID、研究方法、已核准規則、歷史觀測與追溯清冊；工程參照 `2af6f611e51eb4ac9dda73419d1afeba70d1307e`；科學發布參照 `0dff6beb5aca52e565c37289e3ab295737e76942`。
- V2 2026-11-13 的 93 案、51 欄只是**舊研究母體／資料契約的起始參照**；新 V3 不必複製舊數值，不必恢復其 715 份歷史原件，也不必達成舊 16 面板的完全相同排名。

**變更治理：** 原遷移規格 v1.1 的 G0–G5、Golden expected 及 V2 歷史收據維持唯讀，不重寫既有驗收狀態。新建 `F0–F5` 閘門與版本化新研究規格。舊完整歷史重播明確列為 `LEGACY_DIAGNOSTIC_ONLY`，絕不作為 V3 新發布的阻礙。不得刪除舊 ID／來源 SHA 的歷史記錄，亦不得把新 SHA 假裝為舊版本。

## 1. 本輪主要可量測目標

**建成一條以 V3 新來源為基礎、能獨立重跑的 `93 Project Registry → Source Discovery/Acquisition → Immutable Source Edition → Temporal Gate → Generalized Document Processing → Candidate/Validated/Accepted Observation → V3 Census` 資料流程，並為後續 MCDA 與新研究發布提供版本化輸入。**

首輪至少應交付：

1. 保有 **93 個原 Project ID** 的 V3 專案清冊與身分對照；新增獨立新案可在候選擴充層，不得悄悄改變這 93 案研究母體。
2. **93 案逐案來源搜尋及取得收據**：每案清楚報告已找到、已下載驗證、未找到、HTTP/來源受限、時間證據不足等狀態，不能把未檢索稱為零來源。
3. 新來源的不可變位元組、SHA、來源版次、公開證據、取得紀錄、資料庫實體關係與備份驗證機制；相同 URL 不同 bytes 必須成為不同 source edition。
4. 真實來源的端到端觀測正例與拒收反例；至少驗證 **3 種相異的真實文件版面／資料陳述形式**（包含 Mt Weld 既有範例與至少兩種新格式）。無法取得時個別保留 `BLOCKED`，繼續其他工作。
5. 全 93 案、51 欄的 **V3 Census schema/status 投影**：完整輸出 4,743 格的狀態及來源鏈，未核驗者維持缺值、候選或 `NOT_APPLICABLE`；**不要求填滿 4,743 格**，也不把未取得資料視為實測零值。
6. 將新 Census 的正式、可比較資料接到獨立 MCDA 介面；正式面板只在既有資格規則有足夠有效樣本時運算，否則留下 `INSUFFICIENT_COHORT` 與理由。
7. 建立**屬於 V3 的新研究發布與離線重播**機制；發表前能驗證每個被正式使用的原始 bytes／證據／規則／程式／設定均已可保存。這是新版本的 G4 等價工作，不要求重播 V2。

若全部不能在同一 PR 內科學上完成，依第 9 節拆成短期可獨立整合工作；不要因某一來源缺失或需要額外資料就停止其他任務。

## 2. 科學資料與來源重新建檔規則（強制）

### 2.1 Project 與來源版次分離

- `Project ID` 不因重新下載、改網址、公司更名、PDF 版本或資料更新而重新編號。
- 一個 Project 可對應多個 Source，一份 Source 也可關聯多個 Project／Deposit／Resource Scope；保留關係證據，不因公司／名稱相同自動合併。
- `Source SHA-256 = SHA256(取得的**原始 bytes**)`；不得對解析後文字或重新輸出的 PDF 計算後當作原始 SHA。
- 新下載 bytes 與舊 SHA 相同：記錄 `EXACT_SHA_REUSED`；不同：新增 `SOURCE_EDITION`，記錄 `NEW_SHA`、URL 與可能的前後版次關係。**不同 SHA 不一定意味科學內容不同，但在取得同版證據前不得合併版次。**
- 歷史 V2 SHA 僅作 lineage／診斷；不得成為 V3 新來源驗證的硬性匹配目標。
- 禁止以 V2 舊 CSV 當成新的正式數值。V2 可用於候選定位與科學規則參照；所有 V3 新正式選值須有可核驗的 V3 source edition 與當期判定。

### 2.2 Source Acquisition 與合法取件

- 遵循官方／發行人公開管道：ASX 公告、公司年報及 JORC 技術文件、澳洲州政府與 Geoscience Australia 公開資料等。優先使用正式 API、公布檔案及授權公開下載。
- 可重用 V2 已驗證的來源 connector、政府原生 ID／GIS 介面與節流設定，但須以功能分層重構；不整包複製 V2 `acquisition.py` 或所有 Phase 工具。
- 記錄原始 URL、最終 URL、HTTP 狀態、擷取時間、回應 MIME／大小、SHA、出版者、文件版次及 Project 關聯；保留取得與錯誤收據。
- 403、404、逾時、政策禁止、HTML 代替 PDF 等須分類；允許公開官方替代來源，**不繞過登入、CAPTCHA、權限限制或網站限制**。
- `SOURCE_UNAVAILABLE` 僅影響該來源／觀測；不妨礙其他案建檔。
- 使用 content-addressed storage，SHA 重複內容只存一次，並保存每次取得事件；不要因換網址建立不必要的重複 bytes。

### 2.3 Temporal Evidence Gate

- **重用 PR #5 的 `temporal-evidence-v1`，不要再發明第二套截日判定。**
- 分開保存 `publication_date`、同一 exact edition 最早有證據的公開時間、`effective_date`、`acquired_at`、`reacquired_at`、`research_cutoff`，以及預測／工程計畫的未來情境日期。
- 截日前公開的同版文件可以在截日後重新下載；必須有符合核准政策的**同版公開證據**。單憑 PDF 印製日期、目前存在的網址或新下載 SHA 不等於歷史同版證明。
- 既有時間證據有已核准可自動核驗規則者，實作可重播的 proof validator，自動產生／核驗 TemporalEvidence，不設逐筆人工簽核；無法自動確認的保留 `CANDIDATE`，不得降低門檻。
- 截日後首次公開者不入本研究正式值；若確為新情報，可進入新研究截日的後續 release。
- 截日前發布的未來生產計畫可以保留 `FORECAST`／`ENGINEERING_PLAN` 資料角色，不得冒充已實現的事實或歷史觀測。
- 來源 SHA 驗證、時間資格、化學／材料／Scope 相容、正式選值與 MCDA 使用資格各自記錄獨立狀態；任何正式 consumer 都重新核對同一規則版本及被綁定的證據 ID。
- 前一輪的 93 筆 Golden 稽核、83 筆晚於舊截日的 registry 日期與其 66 筆缺歷史同版證據案件**仍保留歷史參照狀態**；不可將它們直接升格至 2026-11-13 歷史研究。新 V3 2026-11-28 則按新文件證據獨立評估。

### 2.4 不可變來源與防逸失

- 自新研究第一份 PDF／CSV／HTML／官方地理資料開始，保存原始 bytes、來源 SHA、清冊與可驗證備份，不依賴 Codex 暫存磁碟或 Git 單一工作樹。
- 建立可替換 storage adapter（本地 content store ＋可配置的獨立備份位置），至少支援兩個**實際可讀回並核驗 SHA**的獨立副本；若遠端儲存尚未設定，記錄 `REPLICA_UNAVAILABLE`，可繼續候選擷取與測試，但不得通過完整正式發布門檻。
- 每次新研究 release 綁定其全部正式採納來源的 manifest、證據索引、SHA、程式 commit、規則／Schema 版本、環境鎖檔、研究截日、MCDA 設定與輸出指紋。
- 重跑不得修改封存 release；新來源或新規則必須產生新的 Release ID。

## 3. 通用擷取、觀測與選值

### 3.1 文件解析架構

- 將目前僅針對 Mt Weld 英文句型與 Total 表格的 `observations.read_resource()` 重構為**共用文件層＋可替換的 parser／extraction strategy**；保留 Mt Weld 策略作回歸範例。
- 同一 PDF 的文字、OCR、區塊、實體頁、表格網格與原值 token 優先共用 document cache，避免擷取、驗證與 Census 各自重新打開同一份文件。
- 支援 PDF 原生文字、表格欄列、跨頁聲明／表題的來源錨定；OCR 作後備，記錄 OCR 引擎與模型版本／SHA。另支援 HTML／CSV 的統一證據定位介面。
- NLP／表格辨識可產生候選值，但只有來源、日期、Project/Deposit/Scope、單位、材料與化學基準符合現有採納規則才進正式資料層。
- 不應以每新增一家公司的特殊 PDF 就在單一巨大函式中追加 if/regex。策略以文件結構／陳述模式負責，記錄適用條件與測試樣本。

### 3.2 Observation 與資料採納

- 保留 Project ID、Observation ID 或版本化對應、原始 token、量測單位、原始精度、來源 URL/SHA、頁碼／表格列欄、估算日期、JORC 分類、cutoff、材料分母與化學基準。
- 將 `Exploration Target`、`Mineral Resource`、`Ore Reserve`、礦物組成、REE 氧化物、技術試驗及產量預測明確分開。
- 支援同一 Project 的不同 Deposit、估算版次、礦體範圍、資源分類、cutoff 及材料基準同時存在，不可簡單因同案有兩筆有效聲明便全部拒收；只有規則無法判定相容性或優先順序時標記衝突。
- 正規化保留換算公式、輸入 Observation IDs、單位及組成基準；不得以 HM 及 whole-ore、TREO 與 TREO+Sc₂O₃、元素與氧化物、精礦與原礦作直接等價換算。
- 在既有核准規則適用範圍內自動採納；證據不足或超出規則範圍保留 Shadow，不要求逐筆人工批准。
- 實驗與模型預測值不得寫入正式實測觀測欄位。正式 T1 揭露透明度也不能誤稱技術實際績效。

## 4. 全 93 案 Census 與 MCDA 接線

- 使用 V2 93 個固定 Project IDs 建立 `project_registry_v3`。每案保存資料搜尋、來源驗證、觀測、GIS、選值及正式可用狀態。
- 先建立相容的 51 欄 Census 輸出契約；每個 Project×Column 均有 `PRESENT / NOT_APPLICABLE / UNKNOWN`、原因、時間資格、正式選值 ID、來源 SHA／scope。缺值不得成為零；`NOT_APPLICABLE` 須有規則證據。
- 舊 V2 93×51 的 2,500 個有值不能直接算作新 V3 填值。**新 V3 科學增益以本次核驗的來源與觀測、正式可比較 project×criterion、可用 cohort 等實測。** 可以把 V2 同欄差異附為參考，不要求新結果等價。
- GIS 可獨立建置位置與基礎設施候選，保存座標角色、原生 ID、CRS、精度、來源與適用範圍；代表點與礦床邊界不得混用。當正式輸入不足，GIS 模組可先獨立通過其測試。
- 用已核驗的資料建立 MCDA criterion matrix，保存準則方向、效用轉換、缺值策略、選擇群組、權重及情境版本。驗收可引用 PR #4 的已驗證點分數計算核心，但必須從 V3 正式資料建立矩陣才能稱端到端研究結果。
- 對正式可用群組執行 SAW、TOPSIS、PROMETHEE II 及適用的敏感度；若樣本／資料不足，記錄 `INSUFFICIENT_COHORT`、輸入缺口及面板跳過原因；不虛構排名。
- 新方法的 NLP／Ontology／ML 改善先以固定樣本作平行實驗，量測準確率、召回、可比較性淨增益、可重播性；未達門檻不影響正式流程。

## 5. 新版本獨立驗收：F0–F5

**不可重定義或覆寫舊 G0–G5。** 建立 `fresh-research-baseline-v1` 的專屬驗收收據，按受影響範圍執行。

| Gate | 檢查內容 | PASS 判定 |
|---|---|---|
| **F0 · 研究母體** | 93 Project ID、來源與身分契約、2026-11-28 截日、版本化設定 | 93 ID 不變、無未經證明合併或重新編號；研究 profile 完整 |
| **F1 · 來源與時間** | 93 案搜尋收據、新文件 bytes/SHA/版次、Temporal Gate、來源資格與失敗隔離 | 93 案皆有實際搜尋狀態；已聲稱驗證的來源逐件 SHA 通過，時間資格由證據支撐；未找到不算已核驗 |
| **F2 · 通用觀測** | Mt Weld + 至少兩種不同真實文件模式、觀測/准入/拒收、Scope/化學基準 | 固定正反例與獨立核驗對照通過，僅已驗證值進正式研究；不足模式記 BLOCKED，不假成功 |
| **F3 · V3 Census** | 93×51 的逐格狀態、正式值來源與可比較性 | 4,743 格均有合法狀態／缺值原因，正式有值格可追溯 Source、Observation、Temporal Decision；無無證據值 |
| **F4 · V3 新研究重播與分析** | 由 V3 原始 bytes、規則及程式獨立重建新 Census／實際可用 MCDA 面板 | 乾淨離線重跑的科學結果等價；受影響的實際 MCDA 輸出相同；不足面板明列，不使用 V2 舊矩陣冒充 |
| **F5 · 新研究發布與回滾** | 不可變 release、輸入／規則／輸出 manifest、來源雙副本核對、指標及 rollback drill | 完成正式資料與適用面板的發布檢查、離線重播、備份讀回與 pointer-only 回滾；不得修改已封存成果 |

- Gate 狀態只允許 `PASS / FAIL / BLOCKED / NOT_RUN / UNMEASURED`，另在觀測層標示 `ELIGIBLE / INELIGIBLE / CANDIDATE / CONFLICT` 等更細狀態。
- **F1 不以成功下載文件總數作唯一 PASS 條件**；要求每個 Project 真正被搜尋並留下具體來源／阻礙證據，區分 `NO_SOURCE_FOUND`、`NOT_SEARCHED`、`HTTP_403`、`TIMEOUT`、`SOURCE_CHANGED`。來源缺失只影響其相依能力。
- **F3 PASS 並非宣稱 93 案每欄都有數字。** 研究母體、缺值狀態及證據鏈完整性是必要條件；真正可比較的資料覆蓋率另外報告。
- **F4/F5 只驗證 V3 自己正式採納的來源與實際發布面板。** 不再以 V2 19 份／其他舊 SHA 缺件作 `BLOCKED` 理由。相依的新 V3 正式來源若缺失，則該部分 F4/F5 仍不得 PASS。
- 任何無法取得的第三方服務、資產副本或獨立真值須照實報 `BLOCKED`／`UNMEASURED`；仍完成不相依的模組、測試與提交。

## 6. 風險分級及必測反例

**低風險**：命名、文件、輸出路徑與非科學設定 → 靜態、依賴、入口檢查。  
**中風險**：模組重構、來源 adapter、parser → 單元／相關整合與固定樣本。  
**高風險**：時間資格、核驗、正式選值、資料契約、矩陣 → 多案例端到端與受影響的 V3 重播。  
**最高風險**：研究母體、正式數值、評分規則、release → 全部相關 V3 正式成果重播及差異報告。

至少實作並執行以下反例：

- 同一 URL 取得不同 bytes → 新 source edition，不覆蓋舊 SHA。
- 不同 URL 取得完全相同 bytes → 同一 content object，保留多個取得事件。
- 截日後重新取得同版、截日前已有可靠同版公開證據 → 允許進一步驗證。
- 僅有舊出版日期但缺同版公開證據 → Candidate；截日後新發布 → Ineligible。
- 缺件、403、錯誤 MIME、超時與下載中斷 → 局部失敗，不撤銷已驗證的其他案。
- 競爭的不同 Resource Scope／MRE 版次、不同 cutoff、HM vs bulk ore、TREO vs TREO+Sc₂O₃ → 不混用，不盲目合併。
- NULL／空白／不適用／實測零的逐格狀態區分。
- 重複匯入相同版次及中斷交易 → 冪等、回滾、無無證據覆寫。
- 改研究截日／規則／來源版次 → 新 Release ID；舊成果不變。
- V3 離線備份取回且故意損壞一份原件 → SHA 檢查失敗，F5 不 PASS。
- MCDA 不足 cohort 或某准則缺值 → 不生成虛假排名。

## 7. 工程品質與檔案治理

- CLI 只做指令解析與工作調度，正式數據判斷需在共用服務與領域規則中；來源、解析、正規化、正式選值、Census、MCDA、發布互不直接呼叫對方的內部細節。
- **優先擴充現有 `contracts/`、`documents.py`、`observations.py`、`store.py`、`application.py` 等責任介面；如果有獨立長期責任，允許拆成 `sources/`、`documents/`、`observations/`、`decision/`、`publication/` 等功能模組。** 不因模組數增加而拒絕合理重構。
- 正式程式功能命名；避免永久 `phaseXX_*.py`、`fix_final.py`，歷史報告／版本化執行目錄允許日期與 Phase。
- 對 V2 的 29 項能力維持 `Capability Matrix`，逐項註明 `VERIFIED / PARTIAL / DEFERRED / BLOCKED`，V3 不需要保留 V2 全部舊函式或原檔名。
- 對每個新增、修改、重構、合併、封存、移除的檔案留下原因；不得只因檔名古怪就刪除仍有依賴的程式。
- 效能、複雜度與模組相依**僅比較已完成相同功能的範圍**。不以 V3 模組較少或缺少功能當作工程進步。
- 暫存下載、OCR、benchmark、CI logs 和研究中間產物輸出至 `work/` 或外部版本化產物庫，不要無限制放入正式程式目錄；正式 release 具不可變 ID 和完整清冊。
- 第三方依賴需鎖版本、重建環境及說明必要性；對 PDF/OCR、GIS 和統計函式庫的非科學執行差異須明確標註。

## 8. 建議 CLI／資料輸出（以既有介面為優先，不要為了檔名重複建立工具）

可依現有 CLI 擴充下列功能，實際介面應在 README 提供可執行命令：

```bash
au-ree-v3 registry import-projects --v2-root "$V2_ROOT" --profile config/research_profiles/au_ree_2026-11-28.json
au-ree-v3 sources discover --profile config/research_profiles/au_ree_2026-11-28.json --out work/fresh/discovery
au-ree-v3 sources acquire --profile config/research_profiles/au_ree_2026-11-28.json --out work/fresh/acquisition
au-ree-v3 research build --profile config/research_profiles/au_ree_2026-11-28.json --out work/fresh/build
au-ree-v3 research verify --profile config/research_profiles/au_ree_2026-11-28.json --out work/fresh/verification
au-ree-v3 research replay --release-id "$RELEASE_ID" --offline --out work/fresh/replay
```

以上是**目標介面示意，不代表目前已存在**；先檢查 CLI 是否已有等價命令再實作。正式 release manifest 至少包含 `cutoff, project_registry_hash, source_manifest_hash, evidence/temporal_policy_versions, parser_version, database_schema, selection_rules_hash, code_commit, environment_hash, census_hash, mcda_inputs_hash, output_hashes, replica_inventory`。

建議交付檔案：

- `config/research_profiles/au_ree_2026-11-28.json`：研究設定／截日／93 案範圍（日期在版本化研究設定中合法）。
- `docs/research/fresh_baseline.md`：基準說明與 F0–F5 收據索引。
- `docs/research/project_source_inventory.csv`：93 案逐案狀態（可以由程式生成，正式報告用清冊及 SHA 參照；原始大量資產不要直接 commit）。
- `docs/research/source_edition_crosswalk.csv`：新來源 SHA、舊來源參考關係（若已知）、版次差異及證據。
- `docs/research/capability_progress.csv`：各功能已驗證／未驗證範圍。
- `work/fresh/` 下的 Discovery、Acquisition、Validation、Census、MCDA、Replay 收據與測試差異。

以上名稱可按既有責任介面調整，但必須功能導向、文件索引明確且不重複產生同類正式產物。

## 9. 執行與 PR 整合順序

避免一次大 PR 及多層未合併 Draft PR。按可驗證增益分成短期工作，完成後直接整合回 V3 `main`：

**A. Source Registry & Acquisition**  
統一 PR #5 時間契約、建立 93 案研究設定、來源 connector、不可變來源倉、逐案搜尋收據；驗 F0、F1。建議分支 `feat/fresh-source-registry`。

**B. Generalized Extraction & Admission**  
建立共用解析視圖、不同文件策略、Scope-aware validation／selection、正式觀測與反例；驗 F2。建議分支 `feat/generalized-resource-extraction`。

**C. Fresh Census & Decision Inputs**  
建立 93×51 新 Census、正式 Project–criterion 矩陣、可用 cohort、正確缺值與 MCDA 介面；驗 F3，適用時運算 MCDA。建議分支 `feat/fresh-census-decision`。

**D. V3-native Replay & Publication**  
原件雙副本／封存、離線冷建、來源到 Census 和適用 MCDA 的重播、回滾演練；驗 F4、F5。建議分支 `feat/research-release-replay`。

每個 PR 都需清楚標記本輪真正通過的 Gate；單一 PR 不必等待所有 93 案都有資料，亦不可把尚未完成的 Gate 記為 PASS。CI 必須對受影響核心規則執行相關單元、真實固定來源整合、Temporal Gate 與逐格狀態驗證。只有發布 PR 才要求執行全部適用研究重播。不要對每個局部變更重跑整個 V2 歷史資料庫。

## 10. 每輪交付報告（必填）

1. **基準與版本**：V3 base/head commit、PR、研究截日、設定/規則/程式/資料契約 SHA。
2. **來源盤點**：93 案各自搜尋狀態、成功取得新來源數、exact-SHA 重用數、新 SHA 數、錯誤來源數、來源證據不足數、實際可讀回備份數。按 Project 與唯一 SHA 分開計數。
3. **科學增益**：新增候選、已驗證觀測、正式選值、可比較 Project×criterion、完成面板樣本及新研究解釋能力；與執行前 V3 相同口徑比較。無資料增益可如實回報零。
4. **截日／科學完整性**：ELIGIBLE／CANDIDATE／INELIGIBLE／CONFLICT、Scope、化學及材料基準拒收理由；是否出現無證據正式值（必須為 0）。
5. **工程增益**：相同已驗證功能的模組依賴、AST 複雜度、重複函式、執行時間、RSS、快取效益、CI 成本；未量測就寫 UNMEASURED。
6. **驗收結果**：F0–F5 的 PASS/FAIL/BLOCKED/NOT_RUN/UNMEASURED，附重現命令、實際執行環境、工作量和收據；不得冒稱成功。
7. **檔案生命週期**：新增、修改、合併、封存、移除的檔案與理由；不得在正式模組繼續累積日期／Phase 臨時名稱。
8. **剩餘阻礙與下一輪**：只列真實阻礙、可獨立進行的後續工作及需要研究方法決策的極少數問題。

## 11. 完成判準

**本輪的成功不以「重新下載到全部 715 份舊文件」、「重現 V2 2,500 個填值」或「重新得到 V2 16 組排名」定義。**

成功應表現為：93 案身分及來源狀態可核對；新來源內容不可變且能追溯；正式數值具備同版時間與科學基準證據；跨公司不同技術文件可逐步進入共用觀測流程；Census 對每一格正確表示有值、缺值或不適用；V3 新發布能在完整核驗的新原件與設定上獨立重播。缺件只影響其相依範圍。

**直接進行實作、執行測試、產出真實收據並提交短期可審核 PR。不要只撰寫開發計畫，也不要因來源缺失、非科學命名或無法人工判定的少數候選停下全部工程。**

---

# 簡短執行提示詞（可直接貼到 Codex）

請在 `lleeoo0611-stack/AU_REE_V3` 執行附件《AU_REE_V3_Fresh_Research_Baseline_Codex_Prompt.md》全部規格，以實際 GitHub `main` 為基準；先確認 PR #5 截日治理是否已合併，必要時整合並驗證。PR #4 僅選擇性移植已驗證的 MCDA 計算核心，不延續 V2 完整歷史重播作為 V3 新發布的前置條件。

本輪以 2026-11-28 為固定新研究截日，保留 93 個 Project ID，重新搜尋和取得來源，按原始 bytes 建立新 SHA 與不可變版次，經共用 Temporal Gate／來源／Scope／化學基準驗證後自動採納合法觀測，建置 93×51 V3 Census、可比較資料及適用 MCDA 面板，並建立 V3 新版本的來源保存、離線重播和發布契約。V2 保持唯讀，缺失的 V2 原件不可阻擋新 V3 建檔，亦不可假冒歷史版本。

按 A／B／C／D 四組短期 PR 漸進整合；每輪實際執行可用測試、產生 F0–F5 收據、科學與工程增益、能力清冊與完整檔案處置紀錄。尚未執行、來源不足或備份未配置者依實況記為 NOT_RUN／BLOCKED／UNMEASURED，不得宣稱正式全量研究已完成。請直接開發、測試、提交 PR，不要只提供規劃。
