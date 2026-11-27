# 獨立副本與復原

正式封存依完整 Release ID 定位。備份操作會複製並重新讀回清冊列出的原件、證據、程式、設定、環境和研究產物。副本可加入既有封存，原研究 Release ID 及其內容維持原值。

## 設定遠端儲存

AWS S3 adapter 需要 AWS CLI v2，且其 `s3api put-object` 須支援 `--if-none-match` 和 SHA-256 checksum。請使用已核准的 AWS CLI profile 或標準 IAM 身分，並在儲存設定檔填入已核准 bucket、Region 及實際 bucket owner。執行身分須可查詢 bucket 區域及擁有者、讀回物件並以條件式請求建立物件。

```json
{
  "schema_version": "s3-research-replica-v1",
  "bucket": "approved-research-backup",
  "region": "ap-southeast-2",
  "expected_bucket_owner": "123456789012",
  "prefix": "au-ree-v3",
  "profile": "au-ree-backup"
}
```

備份前由 AWS STS 查驗請求身分，再以 AWS S3 查驗實際 bucket、預期擁有者和 Region。服務請求使用 AWS HTTPS 端點。副本證明包含服務、區域與儲存身分指紋。條件式寫入拒絕覆蓋既有物件，成功保留各物件的 SHA-256。傳輸憑證由 AWS CLI 的標準身分機制管理。

## 複製、讀回與離線重播

```bash
au-ree-v3 research replicate \
  --release-root "$RELEASE_ROOT" --release-id "$RELEASE_ID" --store "$PRIMARY_STORE" \
  --replica-s3-config "$REPLICA_SETTINGS" --out work/backup

au-ree-v3 research restore \
  --release-root "$RELEASE_ROOT" --release-id "$RELEASE_ID" --store "$RESTORED_STORE" \
  --replica-s3-config "$REPLICA_SETTINGS" --out work/restore

au-ree-v3 research replay --offline \
  --release-root "$RELEASE_ROOT" --release-id "$RELEASE_ID" --store "$RESTORED_STORE" \
  --out work/restored-replay
```

本地清冊遺失時，復原會依 Release ID 從遠端 SHA 物件取回清冊。遠端復原完成後，離線重播只需還原的本地 content store。SHA 不符時，備份及復原收據會記錄失敗；對應的既存物件留供調查。

`--replica PATH` 可做本地第二份副本的讀回演練。僅提供資料夾、不同本地磁碟或自由填寫的故障域標籤，F5 維持 `BLOCKED`。透過未辨識的網路或 object-store mount 提供主儲存時，同樣需要獨立存放證據。完成遠端讀回後，正式發布仍須通過對應研究 gate、離線重播及指標回滾。

本輪環境沒有配置遠端儲存身分與 bucket。套件測試執行 AWS CLI 呼叫介面的模擬測試，真實封存的本地讀回、故障與回滾收據另行記錄。

## 本輪核驗與工程量測

沿用同一封存版本、同一程式和原件比較，兩組各執行三次離線冷建，8 件科學產物的指紋全數相同。中位耗時由 4.162 秒至 4.776 秒，父 Python 程序中位峰值 RSS 由 58,484 KiB 至 58,580 KiB。交替執行時未控制 OS page cache，保留單次量測供查核。

封存組裝沿用同一份讀回清單，完成後只另外核對新寫入的清冊物件。等範圍的單次封存各通過 268 件主儲存和副本讀回，兩端各自的讀取呼叫從 803 次減至 537 次。封存耗時為 6.830 秒及 4.843 秒，兩次科學產物指紋相同。本輪未實測遠端傳輸和等範圍 CI 成本。

四對模組的引用、CLI、測試與 AST 審計均指出不同責任。舊 Oracle 流程與新研究組裝各自保留，共用證據解析、Census 欄位及決策算法維持原有引用。四對均未發現符合既有 AST 門檻的精確重複函式組。本地第二副本和主儲存各讀回 268 件物件，故障測試拒絕已毀損的物件，指標回滾維持封存與正式指標。 [工程收據](engineering_responsibility_audit.json)
