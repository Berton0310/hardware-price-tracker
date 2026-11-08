# 實作與驗證流程（主 Agent 用）

主 Agent 擔任協調者，負責規格、派工、判決與文件。`implementer` 與 `verifier` 定義在 `.agents/agents/`。subagent 拿不到你的對話紀錄，派工訊息要自帶所有需要的資訊。

## 1. 釐清規格

- 只針對會影響資料結構、對外函式介面，或難以回復的決策發問。例如：欄位型別、無效價格怎麼處理、UI 要不要顯示某個指標。
- 其他細節採合理預設，並在派工訊息與最後的回報中列出「採用的假設」。
- 規格拍板後，把驗收條件寫進對應 `docs/modules/0X_*.md` 的「驗收標準」段落。implementer 和 verifier 都讀這一份，不靠你轉述。

## 2. 派工給 implementer

派工前先記錄 `tests/` 的快照：

```powershell
Get-ChildItem tests -Recurse -File | Get-FileHash | Select-Object Path,Hash | Export-Csv logs\tests_snapshot.csv -NoTypeInformation
```

派工訊息要包含：目標、要讀的模組文件、驗收標準的位置、哪些函式簽名不得改。

implementer 回報後，確認 `tests/` 沒被動過：

```powershell
Compare-Object (Import-Csv logs\tests_snapshot.csv) (Get-ChildItem tests -Recurse -File | Get-FileHash | Select-Object Path,Hash) -Property Path,Hash
```

沒有任何輸出才算通過；有輸出就退回。專案改用 git 之後，可以改用 `git diff --stat -- tests/`。

## 3. 派工給 verifier

- 只給它驗收標準的位置與被修改的檔案清單，不轉述 implementer 的實作思路，避免兩邊共用同一套假設。
- 專案是 git repo 時，派工選 `branch` 工作區，讓驗證在獨立的 worktree 進行。

## 4. 判決

- verifier 的回報裡一定要有「實際執行驗證」的指令與輸出（改到 CLI、`.bat`／`.ps1` 或 `app/` 時）。缺了就退回補做。
- **PASS**：verifier 新增的測試留在 `tests/` 當回歸測試；更新 `docs/progress.md`；向使用者回報結果與採用的假設。
- **FAIL**：把 verifier 回報的失敗案例與 stack trace 原文交給 implementer 修正，再重跑第 2、3 步。
- 同一個問題來回 3 輪仍然失敗：停下來，向使用者回報目前狀態與卡住的原因，由使用者決定下一步。

## 5. 錯誤紀錄

- 只有你寫 `ERROR_LOG.md`，subagent 只在回報中描述錯誤。
- 只記非顯而易見、可能再犯的錯誤（拼字、少 import 這類不記）。
- 每記一筆，就要同時：
  1. 把預防規則補進 `.agents/rules/` 對應的檔案，標上 ERR 編號。
  2. 確認有一個回歸測試會在同樣的錯誤再次發生時失敗。
