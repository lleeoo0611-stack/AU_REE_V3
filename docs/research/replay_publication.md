# D · V3 原生離線重播和發布檢查

實際封存研究版本 ID 為 `d055f0726cff7bf783731bd3e698d3334a214fbcf593b8f934dfe089204a5a6b`，科學程式提交為 `17d48b5d4eb730444905ba15697df7d34ed46918`。清冊固定 93 案、來源和證據物件、截日、解析器、時間及選值規則、SQLite schema v2、程式和環境指紋、Census 及 MCDA 輸出。來源清冊 SHA-256 為 `aa3ffa0ff4cb5a10e2cf19778e088c3e5721dc2a3bace3b7832f1e86a9f1b915`。選值規則 SHA-256 為 `a6c61cf5330da398001c6adc9705cf438d61edebad5d42ce2c67f60f4be9c81f`。

**F4 PASS（已實際產生的研究範圍）。** 一次從主儲存、一次僅從本機第二副本冷建，均由 V3 程式、原件與收據重算。兩次各比較 8 件科學產物，差異均為 0。鎖定 6 格正式科學值與兩組 R1。五個決策面板均記錄為 `INSUFFICIENT_COHORT`，零排名。原件、程式、規則及全部實際產物的核對收據見 [離線驗證 JSON](native_replay_verification.json)。

**F5 BLOCKED。** 主儲存與本機第二副本各完整讀回 268 個物件，但同屬單一儲存故障域。另在獨立測試目錄破壞一份正式採納用 PDF，SHA 驗證及冷建依預期失敗，兩份正常副本仍通過。以兩個不同的不可變封存版本執行 pointer-only 回滾演練，結果通過，正式指標維持原狀。

兩次冷建分別耗時 4.014、3.578 秒，皆無網路擷取。含封存、兩次重播、故障注入及回滾的本機流程約 29.44 秒，父 Python 程序峰值 RSS 57,668 KiB。未控制 OS page cache。等功能的 V2 成本比較為 `UNMEASURED`。

D 新增 `publication.py`、發布／冷建／回滾測試和本組驗證收據。修改 CLI、CI、README 及報告索引。未合併、封存或移除既有程式檔。固定 V3 候選版與留存的科學產物可在 `work/fresh/native-release-verification/` 核查。V2 舊歷史回放維持診斷用途。

下一組獨立工作可在同一來源清冊上擴展技術與地理資料准入、可比較準則及 North Stanmore 等候選。正式發布須重新查核獨立備份故障域及 SHA 讀回。
