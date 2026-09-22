# 會員號碼分享與討論區：第一版規格

## 身分與狀態
使用者已選擇 LINE 登入後投稿，未登入可瀏覽公開內容。
已在 LINE Developers 確認 Provider「摘星星俱樂部」下有 LINE Login
Channel「摘星開獎會員」（2011420222）。2026-09-22檢查時為Developing，
LINE Login分頁尚無Callback URL。不要新建重複Channel，不變更Messaging API
或Production設定，不公開Channel secret。

## 已完成的基礎
community.py 是獨立SQLite資料層，尚未連接任何網站寫入路由：
- 首版今彩539，每位已驗證會員、每個開獎日期只有一組5碼；修改不加票。
- 未來7天週一至週六可分享，20:25台北時間鎖定。這是社群截止政策，不是官方開獎時間。
- 保留每次變更的號碼、理由、時間，開獎後不能補單或改單。
- 每則分享可有討論串，留言限制500字，每位會員一分鐘一則。
- 共識統計含樣本數、每號出現份數/比例、共同出現號碼對、奇偶分布。
- 沒有資料就顯示零，不建立虛構高手或樣本。
- 獨立於官方獎號、預測模型、既有私有LINE社群庫；不公開舊私有留言。

資料層member參數必須來自伺服器驗證後的session，嚴禁從request body
信任userId。模組本身不是登入、授權、CSRF或管理員權限實作。
尚未加入Docker或啟用任何公開投稿，六項資料層/既有社群測試通過。

## 完整首版要補的整合
1. LINE Login OAuth/OIDC：state、nonce、PKCE、ID token伺服器驗證、
   HttpOnly/Secure session、登出、CSRF及Staging身分鎖。以最少必要權限登入，
   LINE公開名稱不自動當公開暱稱；投稿前讓會員確認公開暱稱與內容。
2. 交流區頁面：彩種/開獎日期篩選、選號面板、分享理由、我的紀錄、
   公開投稿卡片、每則分享下的討論串與統計面板。標明開獎日期，官方期號
   未確認前不推算或杜撰期號。
3. 管理：檢舉、隱藏、封禁、垃圾連結防護、明確社群規則。
4. 歷史核對：只用已驗證官方結果結算，逐期保留命中數與樣本數。先展示
   完整歷史，不憑單次命中封為高手；不得宣稱熱門度等於中獎率。
5. 會員公開頁：分享次數、核對完成期數、逐期紀錄。不同彩種與樣本數
   不混成無法解釋的「準確率」。登入防止同帳號重複計票，不能保證一人
   只有一個LINE帳號，不宣稱完全防灌票。

## 上線條件
LINE Login callback与Render私密環境變數完成、安全測試和實際本人登入
驗證完成，才開放投稿。Developing限制及公開發佈範圍需在LINE Console
確認，不為了測試擅自切成Published。Production仍不動，未授權任何LINE推播。

參考：https://developers.line.biz/en/docs/line-login/integrate-line-login/


## 2026-09-22 網頁與登入整合
已加入 /community.html、/api/community/* 和 /auth/line/*。服務鎖與實際掛載
檢查都必須通過，才可讀寫獨立 community.sqlite3/community_auth.sqlite3。
公開 feed 不含 LINE ID；暱稱須會員明確確認。管理員使用既有 LINE_ADMIN_USER_IDS。
OAuth 使用 openid 最小 scope、state、瀏覽器綁定、nonce、PKCE、LINE 官方 verify；
一次性回呼，session token 僅以 hash 儲存，Secure/HttpOnly/SameSite=Lax。
寫入需固定 Origin 與 CSRF，登出刪除 session。應用程式日誌遮蔽 OAuth 查詢參數。
服務工作執行緒不快取 auth/API。沒有加入任何 LINE 訊息呼叫。

待完成設定（不公開 secret）：
- 既有 LINE Login Channel 2011420222 的 Callback URL：
  https://lotto-lab-candidate-a-staging.onrender.com/auth/line/callback
- 指定 Render Staging 私密環境變數 LINE_LOGIN_CHANNEL_SECRET：既有 Login Channel secret。
- LINE_LOGIN_ENABLED=1 才開啟登入。未設定時公開頁可瀏覽、所有投稿拒絕。
- 保持 Developing，先由具 Channel 角色的本人實測授權；不自動改 Published。

本版提供日期 feed、30筆分頁、每串最近30則留言、最近20筆修改顯示（資料庫完整保留）、
檢舉紀錄及管理員隱藏。官方命中結算、完整會員歷史頁、封禁帳號尚未完成。
沒有將測試樣本或既有私有 LINE 討論匯入公開資料庫。
