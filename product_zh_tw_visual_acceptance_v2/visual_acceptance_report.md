# 摘星引擎 zh-TW 真實瀏覽器視覺驗收

## 結論

使用 Playwright 搭配本機安裝的 Google Chrome，完成 Desktop 8 頁、Mobile 5 頁與 Tablet 3 頁的真實 DOM Render 與全頁截圖。所有指定頁面通過。

## 瀏覽器環境

- Browser Controller：`BROWSER_CONTROLLER_UNAVAILABLE`，依 Gate 要求停止重試。
- 替代方案：Playwright + Google Chrome Headless。
- 語系：zh-TW。
- 時區：Asia/Taipei。
- UI：與 Staging 相同的現有前端程式。
- 資料：既有唯讀 UI 資料；未建立或偽造 Production Evidence。

## 實際驗收

- Desktop 1440 × 1000：總覽、今彩539、加州天天樂、AI研究中心、證據中心、研究知識庫、通知中心、系統管理，8/8 PASS。
- Mobile 390 × 844：總覽、今彩539、加州天天樂、AI研究中心、通知中心，5/5 PASS。
- Tablet 768 × 1024：總覽、今彩539、AI研究中心，3/3 PASS。
- 所有頁面 `document.documentElement.scrollWidth === clientWidth`。
- 主 UI 禁止 enum 殘留：0。
- 中文缺字／替代字元：0。
- 非預期截字：0。

## Visual Fix

真實 Render 發現並修正兩個純視覺問題：

1. 手機切換至較後方導覽項目時，品牌圖示會跟著橫向捲動而離開畫面。修正為品牌固定、功能導覽獨立捲動。
2. 768px 平板寬度下品牌名稱會被擠成直排。修正為平板保留品牌圖示，桌機仍顯示完整品牌名稱。
3. 手機今彩539／Fantasy 5 主卡片加入合法縮排與換行限制，避免長期別文字撐寬 Grid。

修正後重新 Render 全部 16 個頁面／尺寸，未再發現 Visual Bug。

## 隔離

- Prediction：未修改。
- Research Brain：未修改。
- Notification Backend：未修改。
- API／資料契約：未修改。
- Commit／Push／Deploy：未執行。

## Gate

`A` — 真實 Browser Engine 的 Desktop／Mobile／Tablet 視覺驗收全部通過，可提交 Staging Delivery。
