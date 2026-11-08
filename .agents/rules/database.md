---
trigger: glob
globs: "database/**"
---

# 資料庫模組規則

- 依類別篩選時，`WHERE` 不要用 `OR x IS NOT NULL` 這類寬鬆分支，會讓不同大類的資料混在一起（ERR-006）。
- 比對 `spec_group` 等群組名稱時用 `COLLATE NOCASE`，避免多個來源的大小寫不一致（ERR-009）。
- 測試用 in-memory SQLite，不碰 `data/` 裡的正式資料庫。
