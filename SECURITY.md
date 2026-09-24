# Security

Fitness ledger is a single-user application that runs on your own machine.
There is no hosted service and there are no user accounts. Only the latest
version on `main` is supported.

## Where your data is stored

| Data | Location |
|---|---|
| Training history | A local SQLite database under `data/`, which is git-ignored |
| Hevy API key | The hevy-mcp server's own `.env` file |
| Google OAuth token | The Google Health server's own token file |
| This repository's `.env` | File paths and settings only |

A credential committed to this repository would be a bug. Please report it.

## Data that leaves your machine

Data is sent elsewhere only if you enable these features:

- **Model features.** The chat box, Goals box and planner send your training
  figures, and anything you type into them, to the model provider you configure.
  Set `LLM_PROVIDER=ollama` to keep this on your machine. See
  [docs/model-providers.md](docs/model-providers.md).
- **Hevy write-back.** A routine is sent to Hevy only after you confirm it. Hevy
  has no delete endpoint, so a routine that has been written must be removed by
  hand in the Hevy app.

## Reporting a vulnerability

Open an issue at
[github.com/visakhr1998/fitness_ledger/issues](https://github.com/visakhr1998/fitness_ledger/issues).
For a leaked credential, or anything you would rather not report publicly, use
GitHub's
[private vulnerability reporting](https://github.com/visakhr1998/fitness_ledger/security/advisories/new).

This is a personal project maintained in spare time, so expect a reply within a
few days.
