---
name: maintain-agent-context
description: >-
  維護 repo 內與廠商無關的 agent 指示檔。用於編輯或新增 AGENTS.md、CLAUDE.md、.cursorrules、
  .cursor/rules，把指令寫進 CLAUDE.md，或修正 .agents/skills 下已安裝的技能（含錯字；
  skills-lock.json 所列的技能不可手改，要改來源）時。
license: MIT
---

# 維護 agent 脈絡

## 唯一來源

分層定義在 **`AGENTS.md` →「分層」**：先讀它，其他地方只連結、不重述。技能格式：[Agent Skills 規格](https://agentskills.io/specification)。

## 規則

1. **預設不放 `CLAUDE.md`。** Claude Code v2.1.277 起，工作目錄及其上層都沒有 `CLAUDE.md`／`CLAUDE.local.md` 時直接讀 `AGENTS.md`（進子目錄時也讀巢狀 `AGENTS.md`）；任何 `CLAUDE.md` 都會讓它停讀，上層目錄（如容器目錄）的也算（[官方文件](https://code.claude.com/docs/en/memory#agents-md)）。個人覆寫放 `~/.claude/CLAUDE.md`（不算數）與 `.claude/settings.local.json`，不放 `CLAUDE.local.md`。只有必須支援 v2.1.277 之前、或讀不到 `AGENTS.md` 的 session（停用內建外掛）時才放 `CLAUDE.md`：以 `@AGENTS.md` 開頭、其餘只放 Claude 專屬備註，且每個有巢狀 `AGENTS.md` 的目錄都要各放一份。
2. **Cursor 只用 `.cursor/rules/*.mdc`**，且僅在需要 Cursor 專屬範圍時；`.cursorrules` 是舊制。
3. **`AGENTS.md` 是單一基準**：規則與技能用連結或引用它，不複製。
4. **技能放 `.agents/skills/`**。Claude Code 只掃 `.claude/skills/`，所以逐一連結：`.claude/skills/<name>` → `../../.agents/skills/<name>`。連的是 `<name>` 項目，不是 `skills` 目錄本身（[官方文件](https://code.claude.com/docs/en/skills)只涵蓋技能項目的 symlink）；技能只存在 `.agents/skills/` 一份。
5. **共用技能是「安裝」的**：列在 `skills-lock.json` 的技能，來源由其 `source` 欄位指定。要改就去來源改；消費端 repo 透過排程的 skills-update pipeline（或手動 `npx skills update -p -y`）取得變更。
6. **合併技能更新前審 diff**：技能以 agent 的完整權限執行；更新 pipeline 只開合併請求，不推到預設分支。

## 內容該放哪裡

| 內容 | 去處 |
|------|------|
| 確切的安裝／測試／lint 指令 | `AGENTS.md` 的指令區 |
| 該 repo 專屬的流程 | 該 repo 的 `.agents/skills/` 技能 |
| 跨 repo 重用的流程 | 共用技能 repo 的技能，用 `npx skills add` 安裝 |
| 依檔案類型或路徑限定的 Cursor 指引 | 帶 `globs:` 的 `.cursor/rules/*.mdc`（優先 `alwaysApply: false`） |
| 套件層級的覆寫 | 該套件旁的巢狀 `AGENTS.md` |
| 個人覆寫（不入庫） | `~/.claude/CLAUDE.md`、`.claude/settings.local.json` |

## 技能檢核

- 目錄名 == frontmatter `name`；`description` 寫明做什麼 + 何時用。
- `SKILL.md` 精簡；細節放 `references/` 或 `scripts/`。
- 連結權威來源（`AGENTS.md`、官方文件），不複製會漂移的事實。
