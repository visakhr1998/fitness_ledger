# Contributing

```bash
pip install -e ".[dev]"     # adds pytest and httpx
pytest                       # add ".[coach]" too for the planner's tests
```

The frontend is a Vite build. Editing `frontend/src` changes nothing until you
rebuild, and the built output **is committed** so a clone runs with Python alone:

```bash
cd frontend && npm install && npm run build   # required after any frontend change
npm run dev                                   # :5173, proxies /api to :8000
```

`uvicorn` runs without reload, so restart the server after Python changes.

## What a change needs

- **Rules-engine changes need unit tests** with hand-computable fixtures and no
  I/O. When touching the volume maths, check it against a real session you can
  read off Hevy and verify by hand.
- **Charts: one colour, one series per chart.** If you need to compare, use
  separate small charts rather than overlaying lines. Every chart needs a table
  version, and both light and dark themes must be checked.
- **Never fabricate a result.** Don't claim a test run or a passing check that
  didn't happen.
- **Every change goes through a pull request.** Branch, commit, open a PR. Never
  commit straight to `main`.

The coach's evaluation suite is skipped by default because it costs real model
requests. Enable it with `RUN_COACH_EVALS=1`.

## Reporting upstream bugs upstream

The two MCP servers are part of this project:
[hevy-mcp](https://github.com/visakhr1998/hevy-mcp) and
[google-health-mcp-v1](https://github.com/visakhr1998/google-health-mcp-v1).

When one returns data that is wrong, malformed, or contradicts its own schema,
open an issue on that repo *as well as* working around it here. Two rules,
learned the hard way:

- **Confirm it's the server, not our usage and not the device.** Sparse heart
  rate in an export looks like truncation and is really the watch's sampling
  rate. An unconfirmed report costs more than a gap.
- **Record the workaround in `CLAUDE.md` under data-source quirks**, so the next
  person doesn't rediscover it while waiting for a fix. A filed issue does not
  remove the need for the guard.
