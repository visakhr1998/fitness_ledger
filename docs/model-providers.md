# Model providers

Two features use a model: the **chat box** (`ask`, `POST /api/chat`) and the
**coach**. Nothing else does — sync, the tracker and every number on the
dashboard work with no provider set up at all.

The model never calculates anything. It picks which function to call and
describes the result, which is easy work — so a small free model does about as
well as an expensive one.

## Chat box

| `LLM_PROVIDER` | Also set | Cost | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | free tier | [AI Studio](https://aistudio.google.com/apikey) key, no card needed. Default. |
| `ollama` | *(nothing)* | free | Runs locally. `ollama pull qwen3:4b`. Nothing leaves your machine. |
| `anthropic` | `ANTHROPIC_API_KEY` | paid | Also picks up an `ant auth login` profile. |
| `openai-compatible` | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | varies | Groq, OpenRouter, self-hosted vLLM. The key is optional for a local server, required for a hosted one. |

Leave `LLM_PROVIDER` empty and it picks whichever key you have, preferring the
free one. `LLM_MODEL` overrides the model for any provider.

Three things to know:

- **The model must support tool calling.** The whole loop is tool calls, so one
  without it fails outright rather than giving worse answers. Ollama's `gemma3`
  has no tool support — use `qwen3` locally.
- **Gemini's thinking mode is off by default** (`LLM_REASONING_EFFORT=none`).
  Thinking tokens count against the output limit without showing up in the
  reply — 554 of them on an 11-token question — which cut answers off
  mid-sentence.
- **Free tiers usually cost you data instead.** Outside the EEA, UK and
  Switzerland, Google may use free-tier prompts to improve their products,
  including human review. The chat box sends your questions along with your
  training numbers. Use `ollama` if that matters to you.

## Coach

`GEMINI_MODEL=gemini-3.6-flash`, chosen by testing every free option against the
same case — three weeks with no back training.

| Model | Plans well? | Can run the monthly eval suite (~54 requests)? |
|---|---|---|
| `gemini-3.5-flash-lite` | **no** — two squats, 4 sets | yes (15/min) |
| `gemini-3.5-flash` | yes | no (5/min, 20/day ≈ two plans) |
| `gemini-2.5-flash` | — | **404, "no longer available to new users"** |
| `gemini-3.6-flash` | **yes** — 4 sessions, 64 sets | no |

Drafting one week costs about 3 requests, which every option above handles
comfortably. Only the maintainer's eval suite runs into the daily quota, which
is what the third column measures — the default is fine for normal use.

**`GEMINI_MODEL` must be set.** `config.py` still defaults to
`gemini-2.5-flash`, the retired model in the table above, so leaving the
variable out produces a 404 that reads like a bad key. `.env.example` sets it
correctly; don't delete the line.

**Don't hard-code a model name anywhere.** One became unavailable partway
through this project; the provider indirection is what made that survivable.

### Backup provider

Off unless all three `COACH_FALLBACK_*` variables are set; they ship commented
out in `.env.example`. If the main provider returns a rate-limit error, the same
pipeline runs again on a second OpenAI-compatible provider — DeepSeek through
OpenRouter costs about $0.0015 a plan.

**Only a rate limit triggers it.** A malformed response is a real bug, and
retrying it elsewhere would hide the cause behind a second bill. Half-configured
counts as not configured. Results record which model answered, so a plan from
the backup is never silent.

Known problem: the backup doesn't set a response format, so it sometimes returns
explanation wrapped around a JSON code block, which fails validation. Gemini's
own path enforces the format and doesn't have this issue.
