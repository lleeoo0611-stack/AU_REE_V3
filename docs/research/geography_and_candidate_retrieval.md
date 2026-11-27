# 地理相容性與候選檢索

研究截日為 2026-11-28，時區為 Asia/Taipei。各項試驗使用基準候選發布的 144 個唯一來源 SHA。固定來源清冊 SHA-256 為 `2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd`。各項作業各自輸出候選收據，正式觀測、Census、決策矩陣及發布指標的新增量均為 0。

## D1 開發里程碑

三份原件的五筆聲明中，三筆具有完成語義與可驗證日期。Donald 第一階段 DFS 和第二階段 PFS 各有獨立 Scope；Balranald 保留 2022 年完成的 DFS。Koppamurra 的完成命題欠可核對日期，另一筆未來工程計畫維持預期角色。三筆時間合格的量測只有影子序位，正式 D1 新增 0。序位沿用既有 D1 的順序，維持研究假設。

兩次原件重跑耗時 1.27／1.33 秒，Python 峰值 RSS 37,536 KiB，結果 SHA-256 `89437b6b` 開頭。逐筆證據位於 `work/ontology-v1/development-a/development_milestones.json`。

## GIS 證據角色

以 GDAL 重跑西澳兩個政府礦權快照。7 個法定邊界通過圖徵、原生 ID、CRS、形狀及時序核對，5 個礦權連結仍待唯一圖徵。93 案共產出 372 筆 I1–I4 用途判定。沒有核驗到礦址或礦床代表點，四項設施圖層也無適用證據，故各值維持 `INSUFFICIENT_CONTEXT`。相容性測試分別拒收礦權多邊形、一個標記為礦權中心的點與錯誤型態的礦床來源。

法定邊界收據及用途判定兩次重跑逐位元組一致，耗時 1.83 與 1.74 秒。用途收據 SHA-256 為 `b9da3e493e727defe8d77ff6fea2dbd4c345e13fc43a7255c341945510a44cbd`，檔案位於 `work/ontology-v1/geography/a/infrastructure_compatibility.json`。現階段沒有產生距離、網路可達性、接入能力、運輸成本或正式 I1–I4 分數。

## SQLite FTS5 候選索引

以既有共用文件視圖重新驗 SHA 並索引 141 份 PDF 和 1 份 HTML，保留 57,521 個可定位文字區塊。2 份礦權 ZIP 完成原件 SHA 核對，並列為 `UNINDEXED_CONTAINER`。每筆命中包含原件 SHA、Project 關聯、原始解析文字、定位及候選狀態。PDF 命中另有實體頁及起迄行。查詢字典和索引具有獨立版本及 SHA。

SQLite 3.53.1 的兩次冷建耗時 47.68 與 45.99 秒，Python 峰值 RSS 分別為 70,948 與 71,300 KiB。兩份約 59 MiB 的索引逐位元組一致，SHA-256 為 `649a47316cfc1c89d6952a7f30f6612517d74ecae23ed446c0b415b6ff88d1ed`。八類固定用途查詢各取前 20 筆，單次 0.06–0.11 秒（含索引雜湊），全部查詢收據亦逐位元組一致。與前一版關鍵字候選清冊的工作量不同，尚無等功能效能比較。

固定 R1 前 20 筆命中找回 Koppamurra 的兩頁獨立標註定位。Nolans 的兩頁及 Luni 的鈮資源頁未命中，總計找回 5 個已標註頁的 2 個。使用人工指定詞組可另找回 Nolans 的兩頁及 Luni 的鈮資源頁，固定字典未因這些結果調整。這項測試只核對已知頁面，用於研究定位。完整命中集合缺乏相關性標註，檢索 precision／recall 及科學採納判定維持 `UNMEASURED`。逐案例及八類查詢指紋記錄於 `work/ontology-v1/candidate_retrieval_replay.json`。

## 重跑

以下使用已核驗的組合來源清冊及本機原件庫。索引輸出目錄須可寫入且沒有先前的索引檔。D1 使用 `research review-development`、同一研究設定、來源清冊與原件庫，以及 `config/research_profiles/development_milestone_bindings_v1.json`。

```bash
PYTHONPATH=src python - <<'PY'
from pathlib import Path
from au_ree_v3.candidate_search import build_candidate_index, search_candidates
from au_ree_v3.geography import evaluate_infrastructure_candidates, inventory_tenements

sources = Path("work/research-yield/source-inputs/source_manifest.json")
store = Path("work/fresh/source-store")
output = Path("work/ontology-v1/reproduce")
retrieval = output / "retrieval"
build_candidate_index(sources, store, retrieval)
for purpose in ("R1", "S1", "D1", "GIS", "T1a", "T1b", "T1c", "T1d"):
    search_candidates(retrieval / "candidate_text.sqlite", purpose, retrieval / purpose)
tenements = output / "tenements.json"
inventory_tenements(Path("config/research_profiles/wa_tenement_bindings_v1.json"), sources, store, tenements)
evaluate_infrastructure_candidates(tenements, sources, output / "infrastructure_compatibility.json")
PY
python -m unittest discover -s tests -p test_candidate_search.py -v
python -m unittest discover -s tests -p test_infrastructure_compatibility.py -v
```
