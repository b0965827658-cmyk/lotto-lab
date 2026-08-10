# Legacy Golden Master Full Regression & Functional Recovery

可信 Golden Master 為 `65d4a59bc6eb4cc89fa484679bd22281f460069e`（Deploy `dep-d9p9saj7uimc739f1utg`）。本次直接由該 Git object tree 的 `index.html` 與 `app.js` 建立頁面、資料、欄位及互動基準，沒有依記憶重建。

共盤點 26 項使用者功能，發現 7 項回歸並全部修復。新版語意式介面保留常用入口；Golden Master 完整分析工具以 `/legacy-tools.html` 原碼保留，繼續使用既有 API 與互動，不另建資料或計算。

Playwright + Google Chrome 實測 Desktop 1440×1000 與 Mobile 390×844：冷熱號 39/39、回測 5/5、歷史資料接收/正規化/呈現 60/60/60、自選號碼可超過 5 碼、免責聲明三段完整、水平溢位 0。完整測試為 201 passed + 2 subtests，0 failed。

Prediction、Research Brain、通知後端、Evidence、Knowledge 與 Production 均未修改。
