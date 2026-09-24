---
name: maintain-agent-context
description: >-
  Maintain vendor-neutral agent instructions in a repo. Use when editing
  AGENTS.md, CLAUDE.md, .agents/skills, skills-lock.json, .claude/, .gemini/,
  .cursor/rules, or when asked to add .cursorrules or duplicate agent config.
---

# Maintain agent context

## Source of truth

Layer definitions live in **`AGENTS.md` → Layering** — read it first; do not restate it here or elsewhere. Skill format: [Agent Skills spec](https://agentskills.io/specification).

## Hard rules

1. **`CLAUDE.md` is import-only.** It starts with `@AGENTS.md` (official Claude Code recommendation; covers sessions that can't read `AGENTS.md` directly) and holds only Claude-specific notes. Never paste AGENTS.md content into it.
2. **Do not create `.cursorrules`.** Legacy; use `.cursor/rules/*.mdc` only for Cursor-specific scoping.
3. **Do not copy `AGENTS.md` into rules or skills.** Link or cite; keep one canonical baseline.
4. **Skills live in `.agents/skills/`.** Claude Code only scans `.claude/skills/`, so link each skill there: `.claude/skills/<name>` → `../../.agents/skills/<name>`. Symlink the `<name>` entries, not the `skills` directory itself (the [docs](https://code.claude.com/docs/en/skills) cover symlinked skill entries only). Never copy a skill into `.claude/skills/`.
5. **Shared skills are installed, not edited.** Skills listed in `skills-lock.json` come from `agent-config`. Change them there, then run `npx skills update` in the consuming repo. Never hand-edit the installed copy.

## When to put content where

- Exact install / test / lint commands → `AGENTS.md` Commands
- Repo-specific procedure → skill under `.agents/skills/` in that repo
- Procedure reused across repos → skill in `agent-config`, installed via `npx skills add`
- File-type or path-scoped Cursor guidance → `.cursor/rules/*.mdc` with `globs:` (prefer `alwaysApply: false`)
- Package-local overrides → nested `AGENTS.md` next to that package

## Skill checklist

- Directory name == frontmatter `name`; `description` says what + when (full constraints: spec above)
- Keep `SKILL.md` short; put deep detail in `references/` or `scripts/`
- Link to authoritative sources (`AGENTS.md`, official docs) instead of copying facts that can drift
