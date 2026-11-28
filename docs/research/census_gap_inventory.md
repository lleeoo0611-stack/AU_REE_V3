# Census 缺值與原件優先清冊

固定 V3 `2fc644dc3fadca64cf9cb75db921bd728ea4160a` 及 Stage 2 候選 `b0f1389f395c7eff4a239c0214c0bb518f2ba504f84c71f3be948404debec1da`。以 2026-11-28 Asia/Taipei 為截日，重新讀回 144 份原件及 148 個合格擷取收據。基準重建保留 4,743 個格鍵、1,115 個有值格、16 個直報、2 個正式衍生及 5 個原件描述格；42／27／21 筆 Candidate／Validated／Accepted，R1 四鍵與 S1 兩鍵，完整面板兩個。4,716 格完全一致，27 格只有版本化證據鏈差異。各科學值、來源、Scope、時序、MCDA 政策、權重、分數、排序與敏感度相同。舊候選兩次獨立離線冷重播均讀回且核對八件科學產物。S3-0 通過，F5 維持 `BLOCKED`，歷史 O2 維持 `FAIL`。[基準收據](census_gap_inventory_receipt.json)

## 固定母體後續工作

298 個唯一原件位置已有 167 個釐清欄義。143 筆待綁定是按 Project 記錄的原件出現項，對應 131 個唯一原件位置。以原件 SHA、Project 和實體表格定位合併為 36 組，加上 19 個尚無結構的原件／Project 群，55 組可在現有 CAS 檢查。另有四組歷史缺版工作受阻。清冊保留已重讀位置、表頭、已驗證 Observation ID、精確 V2 版次定位線索與最小待補證據。既有原件出現次數及舊 V2 值均未計為新增觀測。原件解析及正式採納沒有在本單元重新執行。

Wudinna 與 Yttria 的唯讀 Git 版次以原始內容重算 SHA，兩份均相符。若要研究使用，必須另建來源版本、正式取得和通過時間驗證。Splinter 的 WAMEX 入口未取得標定版次，Mt Stirling–Yttria 的歷史同網址取得不同版次，均列 `SOURCE_UNAVAILABLE`。四項歷史版次涉及 35 個欄位線索，實際缺口仍保留已成立的其他 V3 證據優先。「未找到核准 MRE」不產生確定不適用結論。原件解析留出表現沿用兩個已凍結的分母，回報時分開計算。此處沒有新增正式值或改動正式指標。

每格保留原 `value_state`、`evidence_state`、`reason` 及資料角色。缺值分類只作下一步工作用途。`SOURCE_NOT_INDEXED` 代表目前缺少可將原件內容綁到該欄位的索引，仍可能有 Project 的已取得原件。`NOT_REPORTED` 必須有欄位範圍內的明示原件證明。`AFFIRMATIVE_NOT_APPLICABLE` 必須有有證據的適用判斷。較窄的報告範圍、尚未釐清同聲明所引用的量，以及待綁定的化學定義，均可保留具體原因並排入原件重審。

## 重建

在固定 main 建立 `work/census-stage3/baseline-main`，使用 `research build` 與既有 `config/research_profiles/au_ree_census_attributes_v1.json`、`resource_bindings_v4.json` 和固定來源清單。以 `research replay --offline` 從 `work/census-stage2/typed-attributes-22f8565/{archive,cas}` 各自重播舊候選至 `cold-a`、`cold-b`。再從包含本工具的工作樹執行下列步驟。[Stage 2 建置流程](census_project_attributes.md)

```sh
ROOT=/workspace/AU_REE_V3
OUT="$ROOT/work/census-stage3"
PYTHONPATH=src python tools/census_gap_inventory.py \
  --workspace "$ROOT" --v2-root /workspace/AU_REE_V2_reference \
  --main-build "$OUT/baseline-main" --replay "$OUT/cold-a" --replay "$OUT/cold-b" \
  --out "$OUT/gaps-rebuilt"
```

產物為 `stage3_baseline.json`、逐格 `gap_funnel.csv`、去重 `census_gap_queue.jsonl`、`source_edition_recovery.csv`，以及按 Project、欄位與資料角色的 `evidence_conversion_funnel.json`。第二次生成的五份產物可按收據重新核對。新增工具、測試與這兩份文件，既有 V2、V3 原件、程式與科學成果保持歷史版本。
