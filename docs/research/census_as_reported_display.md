# Census 原報值展示與正式採納

以 main 313f0e7 和候選 3d85497a 為基準。研究截日為 2026-11-28 Asia/Taipei，固定原件為 147 個 SHA、152 筆擷取，核心欄位仍為 93×51。Census 網站可展示帶來源的原報值，SQLite 的研究採納、衍生值及 MCDA 仍使用既有驗證與用途規則。

## 展示結果

| 展示用途 | 實際增加 | 證據範圍 |
|---|---:|---|
| 原報資源數值 | 6 格 | Yangibana 與 Yin 各 3 格，保留原數、原單位、Scope 和 cutoff。 |
| 年產預測與產品 | 7 格 | 3 個年產預測格與 4 個產品格。Dubbo 的兩個平均期間並列。 |
| 研究正式值與可比較鍵 | 0 | 18 個直接值、4 個衍生值、4 個 R1 與 2 個 S1 沿用相同科學輸入。 |

候選網站可展示格從 1,151 增至 1,164。21 筆資源證據有逐筆原件定位及採納理由，其中 6 筆 Wolverine 子項保留於來源審查區，既有研究格值優先顯示。Yangibana p14 的 29.93 Mt、0.93% 及 276,955 t 可以直接閱讀，發布日欄保持未知，另顯示可證明的公開日期。Yin 的 311,800 t 和 312,300 t 可依原件頁面並列，對應同 0.20% TREO cutoff 的衝突。各值維持原件單位。Census 網站標示「原報值 · 未納入研究」，點擊可查原件 SHA、來源、頁碼、逐字內容與封存格的判定原因。

候選核心表格使用現有展示契約及刊載資料。年度預測欄依無衝突的產品與原報年率投影，濕精礦的產品原報仍可展示。網站上的正式 CSV、資源比較、地圖和 MCDA 繼續讀取封存資料。

## 版本、重播與工程

候選 Release ID 為 5e75f29274a7f8c307e3286652d6d200ad186f4e0b604e17f55a4900bba82ea7，運算提交 0448f05c14b61ef0aa0055db93f4c739c8f2a85a。

419 項本機測試通過。SQLite 完整性、外鍵、33 個採納鍵、4 個衍生記錄及 6 個 MCDA 比較鍵通過獨立比較，4,743 個封存格的值相符，其中 63 格更新版本血緣。兩套本機 CAS 的 8 個科學產物與 4 個展示產物均通過離線重播。兩份重播收據 SHA 分別為 65e6f9fb883ae3078e4d4b5ff40e25d5aaccce0b39650cda6c46f2cb254314d9 及 b8be6ac99dca974330d6e2649e808e7caff10f9dda2844c92cbe8305109c227f。

F0–F4 PASS，F5 因兩份本機副本共享故障域維持 BLOCKED。瀏覽器查核採兩個封存候選，驗證原報格、證據頁、版本切換及 CSV。候選 ZIP 可解壓再驗證，SHA-256 為 e7a4728eba8caf75569afd8f1895d08530b00ac810e29ce09f416ef329e62300。

展示打包單次觀察為 8.30 → 8.30 秒，峰值 RSS 107,428 → 112,148 KiB，工作量增加原報資源回讀及呈現。測量期間使用同一工作環境，OS 檔案快取未清除。

對已封存版本啟用原報資源證據時，在現有 research build-display 命令加入 --reported-resources。固定原始研究輸入，依候選資料的 Release ID 重跑。既有預測與官方地理可沿用同一資料版本的 --forecast、--geography 及 --geography-base，歷史展示重跑沿用既有參數。瀏覽器驗證可使用 node web/tests/browser.mjs "$WEB_DATA" --reported-census --reported-resources，WEB_DATA 指向含兩個候選的 releases 目錄。

本輪重用 DocumentCache、原件 CAS、來源時間判定、研究觀測及既有證據面板，Python 生產模組、內部靜態依賴、AST 最大及 P90 分支分數、循環及重複函式群均相同。無新增套件。

## 檔案生命週期

| 處置 | 檔案 | 理由 |
|---|---|---|
| 修改 | census_display.py、census_resource_display.py、display_package.py、cli.py | 使用既有證據契約重讀未選值的資源原件，加入可重播的展示選項。 |
| 修改 | web/assets/app.js、display.mjs、labels.mjs、release.mjs、styles.css | 將來源原報格投影到 Census 預覽並保留角色與歷史版本。 |
| 修改 | test_census_resource_display.py、web/tests/browser.mjs | 真實原件、衝突、合格來源與 SQLite 研究輸出邊界的測試。 |
| 新增 | 本報告 | 保存展示增益、研究差異及重跑方式。 |
| 封存 | work/census-as-reported/verified | 以新 Release ID 封存候選、冷重播、完整比較及網站預覽。 |

Git 合併或移除檔案數均為零。
