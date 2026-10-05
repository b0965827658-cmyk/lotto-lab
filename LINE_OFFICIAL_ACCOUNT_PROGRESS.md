# LINE 官方帳號執行進度

## 2026-09-30 最新方向：功能直接在聊天室

使用者明確更正：輸入指令就直接回覆，不要再把五項功能導向網站。此節優先於下方 URI 入口規劃。

- 五項圖文選單草稿改為 message action：彩種、歷史紀錄、直播專區、公開留言板、使用說明。按鈕送出文字指令，回覆有 quick reply；只有直播播放入口使用 URI。
- 新增 line_chat.py：彩種選擇、官方最近一期與最近5期、本人投稿紀錄分頁、網站公開留言與回覆閱讀、台彩/港彩直播入口。公開留言本批只讀，沒有把一般聊天發布到留言板，也不匯入旧 LINE 私人討論。
- 本人紀錄僅使用簽章通過的一對一來源 userId；群組/多人聊天室拒絕。預覽使用既有 OIDC session，忽略 URL 傳來的 member/userId；同 Provider 的 userId/sub 對應既有會員。
- /line-menu.html 改聊天室互動驗收頁；/api/community/chat-preview 僅組裝訊息，不呼叫 LINE、不訂閱、不發文。只有指定 Staging 與持久化磁碟可用；匿名不能查看私人紀錄。
- 實際 LINE 新回覆預設關閉，須 LINE_CHAT_COMMANDS_ENABLED=1、指定 Staging 身分與磁碟全部符合才启用。發布與真實發送邊界維持；本次部署驗收不發 LINE。
- 新版 signed webhook 去重獨立於推播 enabled 狀態，保存在 line_chat_events.sqlite3，不重設既有通知 ledger。
- Fantasy5 仍保留 LINE/歷史/模型/推播封鎖。天天樂指令顯示聊天室尚未開放，不回傳第三方號碼。網站第三方查詢不變。
- 112 項相關測試通過（聊天室/簽章/去重/通知/OIDC/回跳/留言/會員結算/Stepzero）。最初測試資料未填理由與測試模組別名已修正；實測發現去重依附推播 enabled 後已改成獨立儲存並重跑通過。
- 基線核對：Git/遠端/Render Live d3bec70e，原Live dep-dat6i1g473hc73ev2i8g；Render 已由使用者重新登入。新部署完成證據見根目錄 STAGING_CHECK 最新段落，不憑本節推定已上線。
- 網站隱私聯絡信箱仍待提供；不影響這次隔離預覽程式準備，不把未填草稿公開。真實 LINE 選單/新版回覆啟用及歡迎替換，需以成品向使用者提出具體發布決定。

2026-09-28：使用者要求立即執行並持續接續。既有 `staging-line` heartbeat 已改為 ACTIVE，每兩小時接續本任務；無實質變化不通知。發布限制不因排程啟用而解除。

## 2026-09-28 選單調整（依使用者最新要求）

此節優先於下方第一批六格設計。主選單改為五項：彩種、歷史紀錄、直播專區、公開留言板、使用說明。

- 彩種合併為一格，連 `/start.html`；移除主選單的「我要對獎」。
- 「我的紀錄」改為「歷史紀錄」，仍連本人會員紀錄 `/member.html#history`；資料內容與權限不變。
- 直播專區連 `/live.html`，提供「台彩直播」「港彩直播」兩個可直接開啟外部平台的入口。
- 台彩：`https://www.youtube.com/@48ilottery48/streams`。台彩 FAQ 指向三立全民 i 彩券轉播，三立節目頁／官方頻道交叉核對；瀏覽器直播頁已列出 2026-09-28 當日開獎回放。使用頻道直播頁，不固定連舊影片。
- 港彩：`https://www.mytvsuper.com/tc/live/82/TVB-Plus/`。從 myTV SUPER 官方 TVB Plus 節目表的「觀看直播」連結取得；官方直播頁標示 GEO_BLOCK／香港澳門地區限制。網站已明示限制，未聲稱台灣可播放或目前正在播六合彩。
- 香港馬會官方公告確認六合彩攪珠由 TVB Plus 82 台轉播。來源：https://member.hkjc.com/member/chinese/about-membership/news-and-announcements/index.aspx/ 。沒有繞過地區限制、安裝 APP 或登入直播平台。
- 原三立 live.setn.com 直播頁的瀏覽操作遭網站安全政策阻擋，已停止；改用獨立且可正常檢視的全民 i 彩券官方 YouTube 頻道，沒有透過代理或其他介面存取受阻頁面。
- 2500×1686 PNG、五區 URI 設定、預覽點擊區與歡迎文案同步更新。
- 本機桌面與390px手機尺寸瀏覽器已驗收新版選單及直播頁；沒有水平溢出。「歷史紀錄」可點到 `/member.html#history`，登入href保留該目的位置。
- 本次為靜態導覽調整：檢查五區完整覆蓋且無重疊、目的頁與錨點存在、PNG 尺寸與文案同步；不新增模仿靜態文案的測試，不更改驗證或通知程式。
- 部署前基線：本機／遠端／Live a86a93a，dep-dat3sim0tbcc739pclfg。新部署結果以根目錄 `LINE_OFFICIAL_ACCOUNT_STAGING_CHECK.md` 最新段落為準；該文件是本機交接記錄，不為文件更新重新部署。

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

部署與最新線上驗收以根目錄 `LINE_OFFICIAL_ACCOUNT_STAGING_CHECK.md` 為準；沒有該文件或沒有 Live 證據時，不能視為部署成功。先核對遠端及 Render 的實際 commit，不重複部署。

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
