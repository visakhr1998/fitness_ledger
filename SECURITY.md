# Security

This is a single-user application you run on your own machine. There is no
hosted service, no accounts, and no version-support matrix — you run whatever
you last pulled.

## Where your data lives

- **Your training history** is in a local SQLite file under `data/`, which is
  git-ignored. It never leaves your machine unless you send it somewhere.
- **Your Hevy API key** lives in the hevy-mcp server's own `.env`, not here.
- **Your Google OAuth token** lives in the health server's own token file, not
  here.
- **This repo's `.env`** holds file paths and settings only. If you find a
  credential in a commit, that is a bug — please report it.

Two things do leave your machine, and only if you opt in:

- **The chat box and the planner** send your training figures and your questions
  to whichever model provider you configure. Setting `LLM_PROVIDER=ollama` keeps
  this local. See [docs/model-providers.md](docs/model-providers.md).
- **Hevy write-back** sends a routine you have explicitly confirmed. Nothing is
  sent without that confirmation, and Hevy has no delete endpoint, so anything
  written must be removed by hand in the app.

## Reporting something

Open an issue at
[github.com/visakhr1998/fitness_ledger/issues](https://github.com/visakhr1998/fitness_ledger/issues).
If it involves a leaked credential or anything you would rather not post
publicly, use GitHub's
[private vulnerability reporting](https://github.com/visakhr1998/fitness_ledger/security/advisories/new)
instead.

This is a personal project maintained in spare time — expect a reply in days,
not hours.
