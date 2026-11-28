# Census 含 TREO 正式衍生值

已核准公式登錄限於 Census 用途。兩個公式變體以同一資源聲明的礦量 Mt 乘以 TREO 品位 ppm 或百分比，再分別乘以 0.001 或 10 得到 kt。計算保留 Decimal 點值與兩個原報告精度區間乘積的上下界。區間描述報告捨入所容許的範圍。

| Project | Census 衍生值 | 精度區間（kt） | 來源 |
|---|---:|---:|---|
| Jupiter | 2,890 kt | 2,722.5–3,062.5 | 2026-09 原表第 4 頁，1.7 Bt、1,700 ppm，第 19 頁明示兩位有效數字 |
| North Stanmore | 134.560032 kt | 134.556224825–134.563839225 | 2026-08 原表第 97 頁，278.80 Mt、482.64 ppm |

正式衍生只接受核准用途已接受的同一 Project、Deposit／Scope、來源 SHA、聲明、有效日與 cutoff 之乾基全礦石礦量及品位。15 種氧化物成員、來源欄格幾何及印刷精度須核對。已有同聲明直接報告含量時，保留直接報告的優先權。公式迭代、材料或範圍不明、原件衝突和資料時間錯置均有拒收紀錄。Wudinna 與 Wolverine 的五個組分計算仍是 Shadow；組分公式、同範圍輸入或相衝突的 Dy₂O₃ 來源各有獨立理由。

SQLite schema 維持 v3。衍生量用既有 DerivedValue 與輸入引用存放全套評估。啟用此用途的內部研究 Release ID 納入 Formula Registry SHA。收據保留核准使用、選值、驗證、原始觀測、解析及 Temporal Decision 引用。舊釘選版本受唯讀寫入測試保護。正式 Census 欄展示 `FORMAL_DERIVED`，MCDA 沿用原核准途徑。

固定 144 份原件的候選 `0410d7e7fe21a409645abe2c62cb3741abd1afa04d1e339f5f6028f888374de1` 使用提交 `00e33cb9720b46a97023bfae022f58f11b750dab`。逐格比對原有 16 格正式值、21 筆選值與 6 個比較鍵，維持科學身份及數值。結果為正式數值 18 格、有值 1,110 格、新增原始量測 0。兩次獨立 CAS 冷重播各核對八項科學產物。新舊候選 JSON、兩筆衍生證據視窗、歷史缺值與錯誤包隔離通過檢查。F5 維持 `BLOCKED`。[公式、重播與工程收據](census_treo_derivations_receipt.json)

獨立 Oracle 重新讀取原件，使用有理數重算輸入點及區間，並對照歷史 Shadow、候選 SQLite 推導紀錄與五個既有組分 Shadow。可用 `python tools/verify_census_derivations.py --baseline <封存 build> --candidate <新 build> --assets <CAS> --out <收據>` 核對。
