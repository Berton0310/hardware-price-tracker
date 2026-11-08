---
trigger: glob
globs: "database/**"
---

# 資料庫模組規則

- 依類別篩選時，`WHERE` 不要用 `OR x IS NOT NULL` 這類寬鬆分支，會讓不同大類的資料混在一起（ERR-006）。
- 比對 `spec_group` 等群組名稱時用 `COLLATE NOCASE`，避免多個來源的大小寫不一致（ERR-009）。
- 測試用 in-memory 或 tmp_path SQLite，不碰 `data/` 裡的正式資料庫。資料庫是 WAL 模式，一般連線就算只 SELECT，關閉時也會 checkpoint 改寫主檔；真的要檢查正式資料庫時，預設 skip、以環境變數（如 `CHECK_REAL_DB=1`）開啟，並用 `file:...?mode=ro` 唯讀連線（ERR-019）。
