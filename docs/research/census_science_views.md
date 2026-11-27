# Census 科學欄位與衍生證據

Census 仍輸出固定的 93 × 51 表格。原有礦石量、TREO 品位及含 TREO 量由同一個 SQLite 的正式選值投影。整合後的 `census_receipt.json` 同時保存各原生 Resource Statement、入選量測與衍生候選。相同的八個研究產物仍由 Release 清冊逐檔釘選。

以固定原件重建了 17 筆正式選值，涵蓋 12 個 Census 科學格、四筆 Wolverine 同估算氧化物 ppm 和一筆 Wudinna 合併 MREO ppm。四個通過同一估算資源束核驗的專案多一個「有 TREO 資源估計」證據旗標。每個旗標引用各自的礦石量、品位、含量輸入及時間決策。其餘 89 案該欄保持 `UNKNOWN`。數值科學格、選值與 MCDA 輸入均無淨增。

| 影子結果 | 對應原件及依據 |
| --- | --- |
| Wolverine Pr₆O₁₁ 0.438、Nd₂O₃ 2.044、Tb₄O₇ 0.876、NdPr 2.482 kt | 同一份原件的 Table 2 礦石量及 Table 5 全礦石 ppm。選取相同 2025-01-15 JORC 估算與 0.15% TREO 截止品位。 |
| Wudinna MREO 16.8 kt | 同估算 150 Mt 乘以直接公布的四氧化物合併 112 ppm。保留已核驗原件的化學定義與單位證據。 |

每個候選保留公式 SHA、原始數字及單位、四捨五入區間、各量測 ID、Source SHA、PDF 頁碼、表列欄位、估算日期與 Temporal Gate。已有正式 S1 的兩組份額仍可在回執引用。五個可行衍生候選尚無現行規則下的 Census 正式採納，因此維持影子層。完整氧化物總量和 HREE8 缺少已選成員，未產生 Census 值。

Wolverine 同一估算的 Table 2（PDF 第 5 頁）報告 Dy₂O₃ 為 0.85 kg/t，Table 5（第 6 頁）為 840 ppm。依顯示位數計算的區間為 845–855 ppm 和 839.5–840.5 ppm。原件提示可能有四捨五入差異，實際取整尺度未能確定。影子流程保留兩處原文及來源版次，暫停新 Dy₂O₃ 含量及依賴該量測的衍生欄。

實際測試重新讀回 Wolverine 原件 Table 5 的全部 15 個原生 ppm 欄。生產流程目前選取其中四項。標示為合成的完整輸入束另驗證全部 15 項運算、HREE8、合併 NdPr 及部分組成的阻擋。即使分項均在合理範圍內，部分氧化物加總下限超過總 TREO 上限時，該組衍生結果會隔離並保存上下界。跨來源版次、估算日、Scope、截止品位、Reserve／Target、材料分母、TREO 化學成員和出現不一致 rounding interval 的反例均已執行。原件報告的組合量保留為單一量測。

V2 固定方法規則的 100 項處置有逐項來源、SHA、現有 V3 對應及需證據門檻。各規則的相關欄位計數可重疊，能獨立歸因的效果仍為 `UNMEASURED`。[V2 規則清冊](census_v2_rule_inventory.json)
