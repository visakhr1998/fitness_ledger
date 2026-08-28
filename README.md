# Fitness ledger

A personal training tracker. It counts your **effective sets per muscle group
each week** and compares them against a target, and it tracks your running the
same way — so you can see when one is quietly eating into the other.

An *effective set* is a working set, warmups excluded. The muscle doing the work
gets full credit; muscles assisting get half.

Lifting data comes from Hevy. Runs, sleep and resting heart rate come from
Google Health. It can also draft next week's training for you to approve.

## What it does

- **Tracks volume per muscle group.** Effective sets per week against a target.
- **Answers questions about your training.** How much chest work did I do last
  week? What am I neglecting? Is my running getting more efficient?
- **Measures running efficiency.** Distance adjusted for hills, divided by
  heartbeats, so a hilly run and a flat one of similar length compare fairly.
- **Drafts your week.** A planner proposes sessions from your goals, what you've
  been doing, and which days you have free — and says what it couldn't fit in.
- **Never changes anything on its own.** Warnings are shown, not acted on.
  Sending a workout to Hevy needs you to confirm it against a before/after
  comparison.

## Do you qualify?

- A **Hevy Pro** subscription. The API key is Pro-only, generated at
  [hevy.com/settings?developer](https://hevy.com/settings?developer). Without it
  there is no lifting data and most of this app is empty.
- A **Google account** with data in Google Health/Fit, for runs and sleep.
- **About an hour**, and a willingness to run commands in a terminal.

If any of those is a no, stop here — it'll save you the hour.

## Step 0 — Tools

| | Check you have it | If not |
|---|---|---|
| **Python 3.11+** | `python --version` | [python.org/downloads](https://www.python.org/downloads/) — tick "Add Python to PATH" |
| **Node 20+** | `node --version` | [nodejs.org](https://nodejs.org/) — take the LTS build |
| **git** | `git --version` | [git-scm.com/downloads](https://git-scm.com/downloads) |

Node is needed to *run* the Google Health helper below, not just to change the
frontend.

## Step 1 — The two helper apps (~30 min, the hardest part)

Your data reaches this app through two small programs that run on your own
machine, each holding its own credentials — which is why none live in this repo.

**This is the fiddliest part of the install.** Both need building from source.
If that's unfamiliar territory, budget an hour and don't start late at night.

Put them somewhere short with no spaces in the path, like `C:\ledger\` or
`~/ledger/` — it makes Step 3 much easier.

| Clone and build | Gives you | You'll end up with |
|---|---|---|
| [hevy-mcp](https://github.com/visakhr1998/hevy-mcp) | lifting history | a runnable program, plus its own `.env` holding your Hevy API key |
| [google-health-mcp-v1](https://github.com/visakhr1998/google-health-mcp-v1) | runs, sleep, resting HR | a built `dist/index.js`, plus a token file from signing in to Google |

Follow each repo's own README to the end, then **write down the full paths** —
you need four of them in Step 3.

## Step 2 — Install the ledger

**Windows (PowerShell):**

```powershell
git clone https://github.com/visakhr1998/fitness_ledger.git
cd fitness_ledger
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

If activating fails with *"running scripts is disabled on this system"*, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again. That
allows scripts for your own user account only.

**macOS / Linux:**

```bash
git clone https://github.com/visakhr1998/fitness_ledger.git
cd fitness_ledger
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Your prompt now starts with `(.venv)` — that's how you know it worked, and it's
what puts the `ledger` command on your path. `pip install` prints a lot of text
and often a yellow warning or two; as long as the last line says *Successfully
installed*, you're fine.

Week planning is a separate install, because it pulls in around 120 more
packages: `pip install -e ".[coach]"`.

## Step 3 — Point it at the helper apps

Open `.env` and fill in the four paths from Step 1. Use full paths.

```ini
# Windows
HEVY_MCP_COMMAND=C:\ledger\hevy-mcp\hevy-mcp.exe
HEVY_MCP_ENV=HEVY_DOTENV=C:\ledger\hevy-mcp\.env
HEALTH_MCP_COMMAND=node
HEALTH_MCP_ARGS=C:\ledger\google-health-mcp-v1\dist\index.js
HEALTH_MCP_ENV=GOOGLE_HEALTH_TOKEN_PATH=C:\ledger\google-health-mcp-v1\token.json

# macOS / Linux
HEVY_MCP_COMMAND=/Users/you/ledger/hevy-mcp/hevy-mcp
HEVY_MCP_ENV=HEVY_DOTENV=/Users/you/ledger/hevy-mcp/.env
HEALTH_MCP_COMMAND=node
HEALTH_MCP_ARGS=/Users/you/ledger/google-health-mcp-v1/dist/index.js
HEALTH_MCP_ENV=GOOGLE_HEALTH_TOKEN_PATH=/Users/you/ledger/google-health-mcp-v1/token.json
```

Also set `LOCAL_UTC_OFFSET_MINUTES` to your own offset in minutes — 60 for UK
summer, 120 for most of Europe, −300 for US Eastern. It decides which day a 10pm
session counts as.

## Check it worked

```bash
ledger doctor
```

Working output:

```
Hevy MCP           ok    12 tools
Google Health MCP  ok    9 tools
Database           0 workouts
Model provider     none configured (ask and plan unavailable)
```

`0 workouts` is expected before your first sync, and `none configured` is fine —
a model is optional. If either server says anything other than `ok`, see
[when it doesn't work](#when-it-doesnt-work).

## Your first sync

```bash
ledger sync       # a few minutes; ~48 requests for a few hundred workouts
ledger serve      # then open http://localhost:8000
```

Leave the `ledger serve` window open while you use the dashboard. Ctrl-C stops
it.

Sensible per-muscle targets are already set, so `ledger volume` works
immediately. `ledger targets` lists all sixteen muscle groups with your current
numbers; change one with `ledger targets --set chest=16`.

## Coming back tomorrow

The virtual environment only lasts as long as that terminal window. Each time:

```bash
cd path/to/fitness_ledger
.venv\Scripts\Activate.ps1     # Windows
source .venv/bin/activate      # macOS / Linux
ledger serve
```

If `ledger` says *command not found*, you skipped the activate step.

## Commands

| Command | What it tells you |
|---|---|
| `volume [--window]` | Every muscle group against its target |
| `muscle <name> [--window]` | How much you did for one muscle group |
| `neglected [--window]` | What you've been skipping |
| `trend [--weeks] [--muscle]` | Volume over time |
| `progress <exercise>` | Estimated one-rep max over time |
| `progression` | Whether each lift is ready for more weight |
| `runs` · `health` | Runs; sleep, resting HR, steps |
| `insights` | Run the warning rules |
| `targets [--set chest=16]` | Show or change per-muscle targets |
| `exercises <query>` | Search the exercise catalog |
| `unavailable <date>` | Mark a day you can't train, then draft again |
| `export [--out]` | Dump every table to JSON |

Time windows accept `this-week`, `last-week`, `last-4-weeks`, `last-30-days`,
`last-3-months`, `2026-07`, or `2026-07-01:2026-07-31`. Two of those are not
interchangeable: `last-N-weeks` counts only finished weeks, `last-N-days`
includes today — see [how the numbers work](docs/how-it-works.md#two-kinds-of-time-window).

## Optional: the chat box and week planning

Both need a model provider. A free Gemini key from
[AI Studio](https://aistudio.google.com/apikey) needs no card — put it in `.env`
as `GEMINI_API_KEY`. Or run a model locally with `LLM_PROVIDER=ollama`, and
nothing leaves your machine. See [model providers](docs/model-providers.md).

| Command | |
|---|---|
| `ask "how much chest did I do last week?"` | Questions in plain English |
| `goals --add strength_1rm=100 --subject "Bench Press"` | Set a goal to plan toward |
| `goals --set-running 25/3` | 25 km a week across 3 runs |
| `plan [--week]` | Draft a week — also needs `pip install -e ".[coach]"` |

The planner picks exercises and days; it never picks how many sets, which comes
from your weekly targets. Accepting a week only records it here. Sending a day
to Hevy is a separate step where you see exactly what will be created first.

## When it doesn't work

| Symptom | Usually means |
|---|---|
| `ledger: command not found` | The venv isn't active — see *Coming back tomorrow* |
| `running scripts is disabled` (Windows) | PowerShell's execution policy — see Step 2 |
| `doctor` can't reach Hevy | `HEVY_MCP_COMMAND` isn't a full path, or isn't executable |
| `doctor` can't reach Google Health | The server isn't built — `HEALTH_MCP_ARGS` must point at `dist/index.js` |
| Google Health worked, now doesn't | The OAuth token expired; re-run that server's sign-in |
| Empty Gym tab after sync | Hevy returned nothing — check the API key is Pro-active |
| Sessions land on the wrong day | `LOCAL_UTC_OFFSET_MINUTES` — see Step 3 |

## Documentation

- **[How the numbers work](docs/how-it-works.md)** — what an effective set is,
  time windows, adding weight, what the warnings mean, and running efficiency.
  *Read this one.*
- [Model providers](docs/model-providers.md) — only if you want the chat box or
  the planner.
- [Architecture](docs/architecture.md) — code layout and internals.
  *For contributors.*

## Privacy

No credentials live in this repo — the two helper apps hold their own, and
`.env` here holds paths and settings only. `data/` is git-ignored because the
database contains your training history. If you turn on the chat box with a
hosted model, your questions and training numbers go to that provider; `ollama`
keeps everything local. See [SECURITY.md](SECURITY.md).

## What this isn't

- **Not automatic.** Nothing reaches Hevy without you confirming it first. Hevy
  has no delete endpoint, so anything written has to be removed by hand in the
  app.
- **Not medical advice.** Sleep and heart-rate data are shown as your own
  history, never as a recommendation.
- **Not multi-user.** One person, no accounts. Share it by cloning it and
  pointing it at your own data.

## Licence

MIT. See [LICENSE](LICENSE). Contributing notes are in
[CONTRIBUTING.md](CONTRIBUTING.md).
