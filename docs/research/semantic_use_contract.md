# 語義用途契約與來源鏈

`ontology-use-contract-v1` 把量測的用途判定存為 `semantic-assessment-v1`。評估身分綁定詞彙 SHA、政策、用途、Source SHA、Observation IDs、原始 EvidenceAnchor、時間判定與正規化語境。SDK 入口為 `SemanticContext`、`ProcessContext`、`assess_semantic_use` 及 `compare_semantic_use`。現行正式 R1/S1 路徑沿用 `criterion-comparability-v1`；語義影子記錄供消融和候選驗證。

用途契約涵蓋三組判定。

- R1/S1 記錄資源分類、JORC 類別、估算有效日和 statement、截止品位與分析物、材料分母和明示的 REO 集合。跨專案用途比較另要求已知的庫存情境。礦物資源、儲量及勘探目標各有分類。
- T1a–T1d 分開量測種類、原料與產品、流程邊界、尺度、測試日期、操作程序、條件與代表性。T1d 的證據適用性是描述用途。用途群保留未知值並逐項列出已知的端點不相容。
- D1 要求已完成的歷史事件與日期。地理距離要求已核對的礦場／礦床點、原生 ID 與 CRS。法律礦權範圍可有自己的身分和證據關聯。

SKOS 映射須以明確上下文及原件證據限定 `exactMatch`。QUDT 維度、JORC／CRIRSCO、EarthResourceML、SOSA／SSN、PROV-O 與 SHACL 的對照網址隨詞彙版本一起保存。目前執行器使用 Python 的顯式判定。

SQLite v3 僅新增語義判定與來源錨定關聯，語義的 Project／Scope／時間要求會回到既有正式資料鏈查核。正式採納透過既有 Selection 與發布規則執行。新表接受同筆判定的冪等重播，保留舊原始值、正式選值和封存產物。

```bash
python -m unittest discover -s tests -p 'test_semantics.py' -v
python -m unittest discover -s tests -p 'test_store.py' -v
```
