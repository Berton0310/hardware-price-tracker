@AGENTS.md

## Claude Code 補充

- Claude Code 不會自動載入 `.agents/rules/`。修改某個目錄前，先讀對應的規則檔：`crawlers.md`（crawlers/、cleaners/）、`database.md`、`analysis.md`、`app.md`、`windows-scripts.md`（.bat、.ps1、scripts/）。
- `.agents/agents/` 的角色檔是 Antigravity 格式，Claude Code 不會自動識別。派 subagent 時，在指令中要求它先讀對應的角色檔，並依 `docs/workflow.md` 的流程進行。
