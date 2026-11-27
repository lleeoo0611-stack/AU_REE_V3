# AU_REE_V3 獨立研究基準

研究截日為 **2026-11-28（Asia/Taipei）**。執行規格是 [v1.2](specification_v1_2.md)，SHA-256 為 `8e604f097b7018eef9239367e9733c9740f4839e37ab042e40d297581ccd3e85`。G0–G5 與 Golden 保留歷史參照用途。新版採用 F0–F5；完整 V2 歷史重播是 `LEGACY_DIAGNOSTIC_ONLY`。

## A · 研究母體與來源取得

基準為合併 PR #5 的 V3 `dbf297e55da13b93655950783b2dff85baeb4d3c`。隔離檢出該提交後，以 Mt Weld 原件執行原有 91 項測試，全部通過。V2 工程 `2af6f611e51eb4ac9dda73419d1afeba70d1307e` 及科學 `0dff6beb5aca52e565c37289e3ab295737e76942` 均為唯讀。

| Gate | 狀態 | 證據 |
|---|---|---|
| F0 | PASS | 分別核對封存選擇清冊、Census 身分欄及發布成員，保留 93 個 ID 和 51 個依序欄名。 |
| F1 | PASS | 93 案皆有實際公開端點檢索，所有標記成功的來源均通過原始 bytes 回讀及共享時間門檻。 |
| F2 | PASS | 三種真實資源 PDF 版面通過正反例與獨立核驗，6 筆選值。 [B 准入報告](resource_admission.md) |
| F3 | NOT_RUN | 新 Census 屬 C。 |
| F4 | NOT_RUN | 正式來源至結果的離線重建屬 D。 |
| F5 | BLOCKED | 未配置獨立備份端，尚無正式發布。 |

固定清冊有 180 個可供檢索的歷史公開來源指標和 226 筆專案關聯。A 由每案優先的最多兩個指標發出實際連線，合併重複網址後，執行 144 次 HEAD 與 144 次 GET。HEAD 有 139 次可達、3 次 404、2 次 500。GET 有 111 次通過原件檢查、1 次 404、32 次 MIME 不合。第一輪有 85 案取得時間合格原件。8 個缺口隨後以可核對的 ASX 官方直接 PDF 連結取得 10 份文件。另保存 6 筆解析目標來源的實際取得。四份原始批次收據合併後，93 案全數各有至少一份時間資格通過的來源；所有失敗連線保留。

合併清冊有 127 筆成功取得，其中 124 筆相同 SHA、3 筆新 SHA，合計 123 個唯一原件 SHA。獨立回讀核驗原件和時間證據及其配對，耗時 0.70 秒、Python 峰值 RSS 49,076 KiB。三個新 SHA 分別為 Wudinna HTML、Strandline Coburn PDF 及 ASRA Yttria PDF。第一輪取得含網路等待為 35.64 秒；為 8 個缺口補查的官方連結請求完成時間跨度為 8.21 秒。主端可讀，獨立副本讀回為 0。舊 SHA 僅作 lineage 分類。時間合格僅表示當期同版公開證據已核對，科學准入屬後續驗證。

A 工作單元新增正式觀測、可比較 Project×criterion、面板及排名均為 0。相同功能範圍的 V2 工程對照、快取收益與 CI 成本差異為 `UNMEASURED`。

- [F0 驗證收據](registry_verification.json)
- [93 案來源逐案清冊](project_source_inventory.csv)
- [來源 SHA 版次對照](source_edition_crosswalk.csv)
- [F1 獨立回讀收據](source_readback_verification.json)
- [補查來源與定位依據](supplemental_source_locators.csv)
- [能力進度](capability_progress.csv)

A 經 [PR #6](https://github.com/lleeoo0611-stack/AU_REE_V3/pull/6) 整合為 `b15404189149113b4a6feb87699227fb9f1d8d74`，測試 head `a996963f49348e609c9e5d154a29b03e4fde3218` 的 [CI](https://github.com/lleeoo0611-stack/AU_REE_V3/actions/runs/46071353342) 通過。A 新增四個版本化研究設定、`registry.py`、`migration/project_registry.py`、`sources.py`、來源及清冊測試，以及本目錄中的規格、報告和實測清冊。修改 CLI 與 README 提供可執行入口。本組未合併、封存或移除程式／資料檔。暫存原件與完整事件收據保留於 `work/fresh/`。重現命令見 [README](../../README.md)。
