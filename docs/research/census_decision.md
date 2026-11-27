# C · 新 Census 與決策輸入

F3 的正式組裝使用新研究 Profile 和合併來源清冊。固定次序輸出 93×51 共 4,743 格。Census 逐格收據將固定登錄身分與新科學值分開，科學填值須有已核驗的 Source SHA、Observation ID、頁列證據錨、Temporal Decision 與正式選值 ID。空值與零分別測試；只有具已核准理由的格可標為不適用。

| 狀態 | 格數 |
|---|---:|
| 研究登錄身分 | 93 |
| 新正式科學值 | 6 |
| 未知或缺少當期核准值 | 4,644 |
| 不適用 | 0 |
| 合計 | 4,743 |

科學填值來自 Mt Weld 與 Browns Range／Wolverine。已從兩個完整且具同源、同 Scope 和相容化學基準的資源組合建立 **2 個 R1 Project×criterion**。五個釘選決策面板的額外準則及相容母體不足，皆標為 `INSUFFICIENT_COHORT`，實際排名為 0。Wolverine 的氧化物組成可供後續判斷；Mt Weld 的鄰近組成表是 Ore Reserves，維持與 Mineral Resource 分離。

自 PR #4 選擇性移植驗證過的 SAW、TOPSIS、PROMETHEE II、逐項移除演算法。輸出只從新 Store 正式選值組裝，政策封存來源為已核准的固定 V2 方法設定。通過合成正式觀測上的正向運算和缺證據、錯 Scope 反例。此輪實際 cohort 不足，決策排名的生產運算成本維持 `UNMEASURED`。

本機同輸入 Census 組裝與重新核對約 0.21 秒、Python 峰值 RSS 27,488 KiB；固定決策輸入及不足狀態的兩次重算約 0.047 秒、峰值 RSS 16,608 KiB。這些是已完成範圍的新流程量測，跨版本相同功能的複雜度和成本差異為 `UNMEASURED`。

C 新增 `fresh_census.py`、`fresh_decision.py`、`decision.py`、鎖定政策、研究組裝及環境指紋模組、相依測試與本報告，修改 CLI、打包設定、README 及能力矩陣。重跑命令見 [README](../../README.md)。
