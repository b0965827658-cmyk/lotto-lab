# Staging 管理員 LINE 結果通知

只部署 `codex/line-admin-staging` 至 Render `lotto-lab-candidate-a-staging`
（`srv-d9pomuqd0e5s73en98eg`）。不要套用 repository 的 Production blueprint。

必要設定：

- `LINE_NOTIFICATIONS_ENABLED=true`
- `LINE_NOTIFICATION_RUNTIME=staging`
- `LINE_NOTIFICATION_LOOP_ENABLED=true`
- 沿用服務既有 `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_CHANNEL_SECRET` 和 `LINE_ADMIN_USER_IDS`。
- 持久化磁碟必須掛載 `/api/health`；通知 SQLite 預設儲存在該磁碟。
- `LINE_NOTIFICATION_TESTER_USER_IDS` 可選，設定後只能縮小管理員名單。
  未設定時使用既有管理員名單；空管理員名單一律關閉通知。

管理員輸入「通知開啟」後才訂閱；可用「通知設定 539」縮小彩種，
或「通知關閉」取消。非管理員不得訂閱或收到 push。
開獎前一小時提醒無發送路徑，只有官方結果通知。
台灣彩種只經台灣彩券最新／官方歷史 adapter，六合彩只經香港賽馬會已完成結果 adapter。
發送入口再次檢查服務、磁碟、管理員、官方來源、彩種、期別、日期、主號與特別號。
排程只送當天官方結果，未驗證／來源失敗時不發送。

所有結果使用 `result:<game>:<period>` 作為持久化去重鍵。
同一管理員同一期只發送一次；網路重試沿用 LINE retry key，24 小時後不再重試。
LINE API 接受不代表使用者已讀或裝置確實收到。

部署前先確認 Staging 的最新 Live commit、進行中部署及 Auto-Deploy 設定。
Auto-Deploy 關閉時，先 Save only 儲存環境設定，push 後手動部署新 commit 一次。
不要對相同 commit 重複部署，也不要重設 SQLite 去重紀錄來重送測試。

測試：

```sh
python -m pytest tests/test_line_admin_staging.py tests/test_line_webhook.py tests/test_line_notifications.py tests/test_line_social.py tests/test_marksix_official.py tests/test_taiwan_official_history.py tests/test_lottery_registry.py -q
```
