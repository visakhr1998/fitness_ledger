# Model providers

Three features use a language model:

- the **chat box** (`ledger ask`), which answers questions about your training;
- the **Goals box**, which turns a sentence into proposed goals, weekly rules
  and days off;
- the **planner** (`ledger plan`), which drafts a week.

Everything else works without a model, including sync, the tracker and every
number on the dashboard.

The model never calculates anything. It chooses which function to call and
describes the result, so a small free model performs about as well as a large
paid one.

## Choosing a provider

| `LLM_PROVIDER` | Also set | Cost | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | Free tier | Default. [Get a key](https://aistudio.google.com/apikey); no card required. |
| `ollama` | Nothing | Free | Runs locally with `ollama pull qwen3:4b`. No data leaves your machine. |
| `anthropic` | `ANTHROPIC_API_KEY` | Paid | Also uses an `ant auth login` profile if present. |
| `openai-compatible` | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Varies | For Groq, OpenRouter or a self-hosted server. The key is optional for a local server. |

If `LLM_PROVIDER` is empty, the ledger uses whichever key is set, preferring the
free option. `LLM_MODEL` overrides the model for any provider.

## Requirements and settings

**Tool calling is required.** Every request is a tool call, so a model without
tool support fails outright. Ollama's `gemma3` does not support tools; use
`qwen3` instead.

**Gemini reasoning is kept to a minimum.** `LLM_REASONING_EFFORT` defaults to
`minimal`, because thinking tokens count against the output limit and can cut
answers short. Set it to `off` to omit the parameter for a provider that
rejects it.

**Response times vary widely.** Five identical requests to `gemini-3.6-flash`
took 2.9, 15.0, 26.5, 103.7 and 6.4 seconds. Two settings control this for the
chat box and Goals box:

| Setting | Default | Meaning |
|---|---|---|
| `LLM_TIMEOUT_SECONDS` | `30` | Maximum wait per request; `0` disables the limit |
| `LLM_MAX_RETRIES` | `1` | Retries after a timeout |

The planner reaches the model through ADK rather than the same client, so it has
its own timeout, `COACH_TIMEOUT_SECONDS` (default 120).

## Separate provider for the planner

`COACH_PROVIDER` and `COACH_MODEL` override `LLM_PROVIDER` and `LLM_MODEL` for
planning only. Leave them empty to use the same provider for both.

This is useful because the two workloads differ. The chat box is used often and
benefits from a fast model; the planner runs a few times a week and needs a
model that can plan a whole week. In a test on the same case with the same
prompt, `deepseek-v4-flash-0731` returned an empty week and `gemini-3.6-flash`
returned four sessions and 64 sets.

## The planner's default model

`GEMINI_MODEL` defaults to `gemini-3.6-flash`, chosen by testing the free models
on the same case: three weeks with no back training.

Each planning attempt uses two or three requests (two when no running target is
set). A draft that is empty or breaks a rule is retried, up to
`COACH_MAX_PLAN_ATTEMPTS` attempts (default 3), so one plan can use up to about
nine requests.

Keep the model name in `.env` rather than in code. Model versions are withdrawn:
`gemini-2.5-flash`, for example, now returns a 404 for new users.

## Backup provider for the planner

A backup provider is used only when all three `COACH_FALLBACK_*` variables are
set. If the main provider returns a rate-limit error, the same planning pipeline
runs on a second OpenAI-compatible provider. DeepSeek through OpenRouter costs
about $0.0015 per plan.

Only rate-limit errors trigger the backup. Other errors are reported, because
retrying a genuine fault on a second provider would hide it. Each plan records
which model produced it.

## Data sharing

Outside the EEA, the UK and Switzerland, Google may use prompts sent to its free
tier to improve its products, including through human review. All three
features send your training figures, and the chat box and Goals box also send
what you type. Use `ollama` if this matters to you.
