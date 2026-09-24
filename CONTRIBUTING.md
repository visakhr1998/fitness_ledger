# Contributing

## Setup

```bash
pip install -e ".[dev]"          # adds pytest and httpx
pip install -e ".[dev,coach]"    # also needed to run the planner's tests
pytest
```

The planner's evaluation suite makes real model requests, so it is skipped by
default. Enable it with `RUN_COACH_EVALS=1`.

## Frontend

The dashboard is built with Vite. The built output in
`src/fitness_ledger/web/dist` is committed so that the app runs with Python
alone, which means you must rebuild after any change under `frontend/src`:

```bash
cd frontend
npm install
npm run build   # required after any frontend change
npm test        # unit tests
npm run dev     # dev server on :5173, proxying /api to :8000
```

The Python server does not reload automatically. Restart `ledger serve` after
changing Python code.

## Guidelines

- **Tests for the rules engine.** Changes to the calculation modules need unit
  tests with small, hand-written fixtures and no I/O. When changing the volume
  calculation, also check it by hand against a real session in Hevy.
- **Charts.** Use one colour and one data series per chart; to compare series,
  use separate small charts rather than overlaying them. Every chart needs a
  table view, and both light and dark themes must be checked.
- **Report results accurately.** Only report a test run or check that actually
  happened.
- **Pull requests.** Make every change on a branch and open a pull request. Do
  not commit directly to `main`.

## Reporting bugs in the MCP servers

The two MCP servers are part of this project:
[hevy-mcp](https://github.com/visakhr1998/hevy-mcp) and
[google-health-mcp-v1](https://github.com/visakhr1998/google-health-mcp-v1).

If a server returns data that is wrong, malformed or inconsistent with its own
schema, open an issue on that server's repository and also add a workaround
here.

- Before filing, confirm that the problem is in the server, not in how this app
  calls it or in the device. For example, heart rate appearing on only some
  track points looks like a truncated export but is the watch's sampling rate.
- Record the workaround in `CLAUDE.md` under "Data source quirks", so it is not
  rediscovered while the upstream fix is pending.
