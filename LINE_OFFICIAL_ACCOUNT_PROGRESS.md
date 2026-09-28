# LINE 官方帳號執行進度

2026-09-28：使用者要求立即執行並持續接續。既有 `staging-line` heartbeat 已改為 ACTIVE，每兩小時接續本任務；無實質變化不通知。發布限制不因排程啟用而解除。

## 第一批已實作

- `/line-menu.html`：六格可點擊的選單預覽、歡迎訊息草稿與素材下載；明示尚未設定到 LINE 官方帳號。
- `/line-assets/rich-menu.png`：2500 × 1686 PNG（小於 1 MB），與六格點擊區域一致。
- `/line-assets/rich-menu.json`：官方 rich menu 格式的 URI 動作草稿。全部指向指定 Staging，沒有訊息動作或直接發送功能。尚未呼叫 LINE API 建立、上傳或綁定。
- `/line-assets/welcome.txt`：歡迎文案，未設定、未發送。
- `/help.html`：彩種來源、加州日期、資料新鮮度、對獎、公開暱稱、個人紀錄、公開留言及通知適用範圍。
- 會員／留言板／號碼分享頁的登入入口保留原頁與指定區塊。登入成功返回原頁；取消、逾時及上游失敗返回原頁並提示重試。
- 新導覽目的地保存在獨立資料表，不重建既有 flows 或 sessions。目的地限定本站允許路徑，綁定 state 與原瀏覽器且一次取用。導覽紀錄與瀏覽器綁定 cookie 保留一天供逾時返回；OAuth 有效時間仍為 10 分鐘，PKCE、nonce、驗證及 session 安全屬性保持。
- 現有訪客、會員與討論頁補上使用說明及功能選單連結。

## 本機驗收證據

- 136 項相關 pytest 通過：登入導覽、既有 OIDC、社群資料與權限、對獎、LINE webhook／通知隔離與去重、Fantasy5 與台彩資料驗證。
- 新增測試包含開放重新導向拒絕、瀏覽器與 state 綁定、重放、取消／逾時／上游錯誤、舊 session 失效及既有資料庫升級保留。
- 六個點擊設定的檔案及錨點全部存在，區域覆蓋整張圖片，下載的歡迎文字與預覽一致。
- JavaScript 語法檢查通過，`git diff --check` 通過。
- 隔離本機瀏覽器實測：從「我的紀錄」進入模擬登入，取消回 `/member.html?login=failed#history`，再登入成功回 `/member.html#history`，登入提示及會員狀態正確。
- 390px 手機尺寸的選單圖片、中文字級及使用說明完成視覺檢查，無水平溢出。
- 上述登入瀏覽器測試使用隔離本機 OAuth 模擬，不向 LINE 傳送、不使用真實帳號或密鑰、不向 Staging 發測試貼文。

部署與最新線上驗收以本機 `.git/line-entry-staging-live.md` 為準；沒有該文件或沒有 Live 證據時，不能視為部署成功。先核對遠端及 Render 的實際 commit，不重複部署。

## 後續待完成

1. 指定 Staging 新版本的 health、選單、素材、help、session、未登入權限及回跳失敗路徑驗收；成功登入本機已驗證，真實手機 LINE 內建瀏覽器尚待驗收。
2. 只讀核對既有 LINE 官方帳號身分、加好友 URL／QR、目前圖文選單及 Login Channel 狀態。查到即記錄，不猜帳號、不更改設定。
3. 正式公開前補齊實際管理員聯絡與資料處理申請方式。未取得真實資訊時不編造。
4. 成品及上述資料齊備後，才由使用者明確決定 LINE Login 發布與對好友生效的選單設定；本次不執行。一般非 channel 角色使用者登入需發布後另外驗收。
5. LINE 直接回覆第三方天天樂及一般會員推播另案處理。網站天天樂入口已可使用，不能因此解除其他 Fantasy5 封鎖。

## 持續執行邊界

僅指定 repo、`codex/fantasy5-fast-official-feed` 及 Render `srv-d9pomuqd0e5s73en98eg`。Production、真實 LINE 發送、收件人及既有539通知去重不動；保留 `board-preview-umybr84j/`，不提交測試資料或 `.git` 中本機測試／密鑰檔。

圖文選單建置腳本：`lotto-lab-web/scripts/build_line_menu.py`，僅本機產生素材，無任何網路或發布操作。Pillow 僅供本機素材建置，伺服器不新增此依賴。

LINE 文件：[圖文選單與 URI 動作](https://developers.line.biz/en/docs/messaging-api/using-rich-menus/)、[Login channel 狀態](https://developers.line.biz/en/docs/line-login/getting-started/)。圖文選單草稿格式及尺寸依前者準備；尚未取得 LINE 伺服器驗證或發布結果。
