# Fitness ledger

A personal training tracker for people who both lift and run. It counts
effective sets per muscle group each week and compares them with a target, and
it tracks running against a weekly distance, so you can see when one kind of
training is crowding out the other.

An effective set is a working set (warm-ups are excluded). The muscle doing the
work gets full credit and assisting muscles get half.

Lifting data comes from [Hevy](https://www.hevyapp.com/). Runs, sleep and
resting heart rate come from Google Health. Everything runs on your own machine.

## Features

- Weekly volume per muscle group, measured against targets you can edit.
- Running efficiency: distance adjusted for hills, divided by heartbeats.
- Warning rules for dropped volume, neglected muscles, stalled lifts and poor
  sleep. They report; they never change your training.
- Goals entered in plain English. A sentence such as "sub-4 marathon in
  November, bench 100 kg, pull-ups from 5 to 10, no running on Wednesdays"
  becomes goals and weekly rules, which you review before saving.
- A weekly plan drafted from your goals, recent training and free days. Set
  counts come from your targets, not from the model, and the draft is checked
  against your rules before you see it.
- Optional write-back to Hevy, one day at a time, after you confirm a
  before/after comparison.

## Requirements

- A **Hevy Pro** subscription. The API key is only available on Pro and is
  generated at [hevy.com/settings?developer](https://hevy.com/settings?developer).
- A Google account with data in Google Health or Google Fit.
- Python 3.11 or later, Node.js 20 or later, and git.
- About an hour for the first install. You will need to run commands in a
  terminal.

Node.js is required to run the Google Health helper, not only to build the
frontend.

## Installation

### 1. Build the two helper apps

The ledger reads your data through two small MCP servers that run locally and
hold their own credentials. Build both before installing the ledger. This is the
longest step.

| Repository | Provides | Result |
|---|---|---|
| [hevy-mcp](https://github.com/visakhr1998/hevy-mcp) | Lifting history | An executable, and a `.env` file containing your Hevy API key |
| [google-health-mcp-v1](https://github.com/visakhr1998/google-health-mcp-v1) | Runs, sleep, resting heart rate | A built `dist/index.js`, and a token file from signing in to Google |

Follow each repository's README, and note the full paths to the files above.
A short path without spaces, such as `C:\ledger\` or `~/ledger/`, makes the next
steps easier.

### 2. Install the ledger

Windows (PowerShell):

```powershell
git clone https://github.com/visakhr1998/fitness_ledger.git
cd fitness_ledger
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

If PowerShell reports that running scripts is disabled, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

macOS and Linux:

```bash
git clone https://github.com/visakhr1998/fitness_ledger.git
cd fitness_ledger
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Once the virtual environment is active, your prompt starts with `(.venv)` and
the `ledger` command is available.

Week planning is an optional extra because it adds roughly 118 packages:

```bash
pip install -e ".[coach]"
```

### 3. Configure

Open `.env` and set the paths from step 1:

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

Also set `LOCAL_UTC_OFFSET_MINUTES` to your UTC offset in minutes (for example
60 for UK summer time, 120 for central Europe, -300 for US Eastern). It decides
which calendar day a late-evening session belongs to.

### 4. Check the setup

```bash
ledger doctor
```

A working setup prints something like:

```
  hevy          OK  -- account Your Name, 22 tools
  google-health OK  -- profile age 34, 11 tools
  database      .../fitness_ledger/data/ledger.db (0 workouts cached)
  model         NO PROVIDER (ask disabled; set GEMINI_API_KEY)
```

Zero workouts is expected before the first sync, and no model provider is fine
because a model is optional. If either server shows `FAIL`, see
[Troubleshooting](#troubleshooting).

### 5. Sync and open the dashboard

```bash
ledger sync     # a few minutes the first time
ledger serve    # then open http://localhost:8000
```

Keep the `ledger serve` terminal open while you use the dashboard, and press
Ctrl+C to stop it.

Default targets are set for all sixteen muscle groups. You can change them on
the Gym screen under **Edit weekly targets**, or with
`ledger targets --set chest=16`.

## Daily use

The virtual environment applies only to the terminal it was activated in. To
start the app again later:

```bash
cd path/to/fitness_ledger
.venv\Scripts\Activate.ps1     # Windows
source .venv/bin/activate      # macOS / Linux
ledger serve
```

The dashboard has four screens:

- **Goals** — enter goals and weekly rules in plain English, and track progress.
- **Run** — distance, heart rate and running efficiency.
- **Gym** — volume per muscle group, tonnage, exercise progression and targets.
- **Week** — draft a plan, accept or reject it, mark a day as unavailable, and
  send a day to Hevy.

## Command reference

| Command | Description |
|---|---|
| `doctor` | Check the helper apps, database and model provider |
| `sync [--weeks] [--full]` | Fetch new data from Hevy and Google Health |
| `serve` | Start the dashboard on port 8000 |
| `volume [--window]` | Every muscle group against its target |
| `muscle <name> [--window]` | Volume for one muscle group |
| `neglected [--window]` | Muscle groups furthest below target |
| `trend [--weeks] [--muscle]` | Weekly volume over time |
| `progress <exercise>` | Estimated one-rep max over time |
| `progression` | Whether each lift is ready for more weight |
| `runs`, `health` | Runs; sleep, resting heart rate and steps |
| `insights` | Run the warning rules |
| `targets [--set chest=16]` | Show or change weekly targets |
| `exercises <query>` | Search the exercise catalog |
| `unavailable <date>` | Mark a day you cannot train |
| `export [--out]` | Export every table to JSON |

Commands that need a model provider:

| Command | Description |
|---|---|
| `ask "how much chest did I do last week?"` | Ask a question in plain English |
| `goals --add strength_1rm=100 --subject "Bench Press"` | Add a one-rep-max goal |
| `goals --add reps=10 --subject "Pull Up"` | Add a rep goal (reps in one set) |
| `goals --set-running 25/3` | Set a weekly running target: 25 km over 3 runs |
| `plan [--week]` | Draft a week (requires the `coach` extra) |

Time windows accept `this-week`, `last-week`, `last-4-weeks`, `last-30-days`,
`last-3-months`, `2026-07`, or a range such as `2026-07-01:2026-07-31`. Note that
`last-N-weeks` counts only completed weeks, while `last-N-days` includes today;
see [How the numbers work](docs/how-it-works.md#time-windows).

## Model provider (optional)

The chat box, the Goals box and week planning need a model provider. The
simplest option is a free Gemini key from
[Google AI Studio](https://aistudio.google.com/apikey), set as `GEMINI_API_KEY`
in `.env`. To keep everything on your machine, run a local model with
`LLM_PROVIDER=ollama`. See [Model providers](docs/model-providers.md) for all
options.

The planner chooses exercises and days. Set counts come from your weekly
targets. Accepting a plan only records it in the ledger; sending a day to Hevy
is a separate step that shows exactly what will be created.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ledger: command not found` | The virtual environment is not active. See [Daily use](#daily-use). |
| PowerShell says running scripts is disabled | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. |
| `doctor` cannot reach Hevy | `HEVY_MCP_COMMAND` is not a full path, or the file is not executable. |
| `doctor` cannot reach Google Health | The server is not built. `HEALTH_MCP_ARGS` must point to `dist/index.js`. |
| Google Health stopped working | The OAuth token has expired. Sign in again through the health server. |
| The Gym screen is empty after a sync | Hevy returned no data. Check that the API key belongs to a Pro account. |
| Sessions appear on the wrong day | `LOCAL_UTC_OFFSET_MINUTES` is wrong. See [Configure](#3-configure). |

## Documentation

- [How the numbers work](docs/how-it-works.md) — effective sets, time windows,
  progression, goals, planning, warnings and running efficiency.
- [Model providers](docs/model-providers.md) — setting up the chat box, Goals
  box and planner.
- [Architecture](docs/architecture.md) — code layout and internals, for
  contributors.
- [Contributing](CONTRIBUTING.md) and [Security](SECURITY.md).

## Privacy

No credentials are stored in this repository. The helper apps keep their own,
and `.env` contains only paths and settings. The database in `data/` is
git-ignored. If you use a hosted model provider, your questions and training
figures are sent to that provider; `ollama` keeps them local. See
[SECURITY.md](SECURITY.md).

## Limitations

- Nothing is written to Hevy without your confirmation. Hevy has no delete
  endpoint, so anything written must be removed by hand in the Hevy app.
- Sleep and heart-rate data are shown as your own history, never as medical
  advice.
- The app is single-user. To use it, clone it and point it at your own data.

## Licence

MIT. See [LICENSE](LICENSE).
