# Census 原件文字屬性

新設定 `au_ree_census_attributes_v1.json` 固定原有 93 案、51 欄、來源清冊及研究截日，另鎖定屬性定位和既有開發里程碑的輸入 SHA。原設定仍可核對封存研究。屬性證據向原件回讀，語意、時間與用途通過後才自動選入 Census 描述欄。

`AttributeAssertion` 保存來源術語、原件版次、證據定位、實體、範圍及原報告日期。共用 V3 `Source`、`Entity`、`EvidenceAnchor`、Temporal Gate 與語意概念對應。 `ValidatedAttribute` 和 `AttributeSelection` 分別固定判定及用途。選值綁定觀測發布、研究設定和原件清冊。完整收據保存在既有 `census_receipt.json`，欄格沿用欄級證據清冊。

| 原件案例 | Census 結果 |
| --- | --- |
| Mt Weld ，實體第 38 頁 JORC 地質附錄 | 以原件確認的碳酸岩風化殼超基因富集型呈現礦床代碼及名稱，合計 2 格 |
| Balranald，實體第 1 頁 | 顯示已完成最終可行性研究，原報告事件時間保留為 `late 2022`，合計 3 格 |
| Mt Weld，實體第 6 頁 | 地質賦存敘述保留為 `HOST_DESCRIPTION` 候選 |
| Koppamurra，實體第 1、51 頁 | PFS 完成日不足，DFS 工程仍屬計畫 |
| Donald，實體第 56、239 頁 | Phase 1 DFS、Phase 2 PFS 已完成的原件敘述均保留各自範圍，專案欄位缺乏整體範圍證據 |

5 個欄格均標為 `VALIDATED_DESCRIPTIVE`，涵蓋兩種、兩筆獨立文字屬性。數值觀測、正式數值欄及 MCDA 用途直接沿用既有成果。驗證使用同一來源清冊並比較全部 4,743 格。獨立 Oracle 透過 Poppler 重讀 PDF，逐一比對確切頁碼、文字位置及候選／選值結果。另以省略一份 Mt Weld 原件的隔離物件庫測試缺件處理。

## 重跑

以下命令在 AU_REE_V3 執行，輸出使用新目錄。

```sh
SHA=$(git rev-parse HEAD)
SOURCES=work/research-yield/source-inputs/source_manifest.json
ASSETS=work/fresh/source-store
BINDINGS=config/research_profiles/resource_bindings_v4.json
au-ree-v3 research build \
  --profile config/research_profiles/au_ree_2026-11-28.json \
  --bindings "$BINDINGS" --sources "$SOURCES" --assets "$ASSETS" \
  --code-commit "$SHA" --out work/census-stage2-attributes/baseline
au-ree-v3 research build \
  --profile config/research_profiles/au_ree_census_attributes_v1.json \
  --bindings "$BINDINGS" --sources "$SOURCES" --assets "$ASSETS" \
  --code-commit "$SHA" --out work/census-stage2-attributes/candidate
python tools/verify_census_attributes.py \
  --before work/census-stage2-attributes/baseline \
  --after work/census-stage2-attributes/candidate --assets "$ASSETS" \
  --out work/census-stage2-attributes/independent_verification.json
AU_REE_MILESTONE_MANIFEST="$SOURCES" AU_REE_MILESTONE_ASSETS="$ASSETS" \
  python -m unittest discover -s tests -p test_project_attributes.py -v
```

與正式 TREO 衍生值整合的固定候選為 `b0f1389f395c7eff4a239c0214c0bb518f2ba504f84c71f3be948404debec1da`，程式提交 `22f85657ef7ef79abe651c1ba3485302dafd2d1a`。追加兩次冷重播、五個屬性證據視窗及原件 Oracle 均通過。5 格描述欄新增後總計 1,115 格有值，18 格正式科學數值及六個比較鍵維持。[整合驗證收據](census_project_attribute_verification.json)
