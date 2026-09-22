@AGENTS.md

## Claude Code

- `AGENTS.md` (imported above) is the canonical baseline. Do not copy its content here; add only Claude-specific notes below.
- Shared skills live in `.agents/skills/`. Claude Code only discovers `.claude/skills/`, so that path is a symlink to `../.agents/skills`. Add new skills under `.agents/skills/`.
- Personal overrides → `CLAUDE.local.md` / `.claude/settings.local.json` (gitignored).
