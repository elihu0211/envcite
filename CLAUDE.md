@AGENTS.md

## Claude Code

- `AGENTS.md` (imported above) is the canonical baseline. Do not copy its content here; add only Claude-specific notes below.
- Shared skills live in `.agents/skills/`. Claude Code only discovers `.claude/skills/`, so each skill is linked there individually: `.claude/skills/<name>` → `../../.agents/skills/<name>`. Add a new skill under `.agents/skills/`, then add its symlink.
- Personal overrides → `CLAUDE.local.md` / `.claude/settings.local.json` (gitignored).
