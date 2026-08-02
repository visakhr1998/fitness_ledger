---
description: Re-derive CLAUDE.md from the current state of the repo
argument-hint: "[optional: what changed, e.g. 'finished v0.3']"
allowed-tools: Read, Edit, Write, Glob, Grep, Bash(git log:*), Bash(git status:*), Bash(git diff:*), Bash(*pytest*)
---

Update `CLAUDE.md` so it matches what this repo actually is right now.

Context the user gave about what changed (may be empty): $ARGUMENTS

## Gather before writing

Do not update from memory of the conversation — read the repo.

1. `git log --oneline -20` and `git status --short` — what has landed since the
   last CLAUDE.md change, and what is uncommitted.
2. `README.md` — the user-facing description. CLAUDE.md complements it; it does
   not duplicate it.
3. `src/fitness_ledger/` — the module list and what each does. Check whether the
   architecture diagram in CLAUDE.md still matches reality.
4. `src/fitness_ledger/config.py` and `.env.example` — the current tunables and
   their defaults. Every value in the conventions table must be real.
5. `tests/` — run the suite and use the actual count and result. If it fails, say
   so in your reply and do not write a passing count into the file.

## Rules for the content

- **Only what a future session could not derive quickly from the code.** No API
  listings, no function signatures, no restating the module tree. Decisions,
  constraints, gotchas and conventions.
- **Every command must be one you have verified exists** in `cli.py` or
  `pyproject.toml`. No aspirational commands.
- **Preserve the design principles and the constrained-week priority order
  verbatim** unless the user explicitly changed them. They come from the product
  plan and are not yours to reword.
- **Preserve the "Data source quirks" entries.** They were each found the hard
  way. Add new ones; only remove one if the code no longer handles it.
- **Preserve "Don't do these yet"**, but move items out as versions land — e.g.
  when v0.3 ships, write-back and ADK stop being prohibitions and the `drift`
  rule becomes implementable.
- Keep the version line at the top accurate: which version is complete and what
  the next one covers.
- Match the existing tone: direct, concrete, reasons given. Keep it scannable —
  tables and short bullets, not paragraphs.

## Finish

- Show the user a short summary of what changed in the file and why: added,
  removed, corrected. If nothing needed changing, say that rather than making
  edits for their own sake.
- Do not commit. Leave that to the user.
