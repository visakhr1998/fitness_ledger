# Model providers

Two features use a model: the **chat box** (`ledger ask`) and the **planner**
(`ledger plan`). Nothing else does — sync, the tracker and every number on the
dashboard work with no provider configured.

The model never calculates anything; it picks which function to call and
describes the result. That's easy work, so a small free model does about as well
as an expensive one.

| `LLM_PROVIDER` | Also set | Cost | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | free tier | [Get a key](https://aistudio.google.com/apikey) — no card needed. Default. |
| `ollama` | *(nothing)* | free | Runs locally. `ollama pull qwen3:4b`. Nothing leaves your machine. |
| `anthropic` | `ANTHROPIC_API_KEY` | paid | Also picks up an `ant auth login` profile. |
| `openai-compatible` | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | varies | Groq, OpenRouter, self-hosted vLLM. The key is optional for a local server, required for a hosted one. |

Leave `LLM_PROVIDER` empty and it picks whichever key you have, preferring the
free one. `LLM_MODEL` overrides the model for any provider.

Three things worth knowing:

- **The model must support tool calling.** The whole loop is tool calls, so one
  without it fails outright rather than giving worse answers. Ollama's `gemma3`
  has no tool support — use `qwen3` locally.
- **Gemini is asked to think as little as possible** (`LLM_REASONING_EFFORT`
  defaults to `minimal`). Thinking tokens count against the output limit without
  appearing in the reply, which truncates answers mid-sentence. Set `off` to
  leave the setting out of the request entirely, for a provider that rejects it.
- **Free tiers usually cost you data instead.** Outside the EEA, UK and
  Switzerland, Google may use free-tier prompts to improve their products,
  including human review. The chat box sends your questions along with your
  training numbers. Use `ollama` if that matters.

## The planner

`GEMINI_MODEL` defaults to `gemini-3.6-flash`, picked by testing the free
options against the same case — three weeks with no back training. Drafting a
week costs about 3 requests, comfortably inside the free tier.

**Don't hard-code a model name.** One became unavailable partway through this
project; being able to swap it in `.env` is what made that survivable.

### Backup provider

Off unless all three `COACH_FALLBACK_*` variables are set. If the main provider
returns a rate-limit error, the same pipeline runs again on a second
OpenAI-compatible provider — DeepSeek through OpenRouter costs about $0.0015 a
plan.

**Only a rate limit triggers it.** A malformed response is a real bug, and
retrying it elsewhere would hide the cause behind a second bill. Results record
which model answered, so a plan from the backup is never silent.
