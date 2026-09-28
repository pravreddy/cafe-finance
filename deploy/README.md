# deploy/ — the Hetzner stack

Same shape as `avyangah-infra/stacks/guhya-pos`, kept here so the repo is self-contained
(copy the folder to avyangah-infra/stacks/cafe-finance if you prefer everything in one place).

One-time:
1. DNS: `ledger.cafegopala.in` A → the box.
2. `bash deploy/cert.sh` (Let's Encrypt via the shared certbot webroot).
3. Push to GitHub so CI builds `ghcr.io/pravreddy/cafe-finance:latest`.

**Step-by-step go-live: [GO-LIVE.md](GO-LIVE.md).**

Every deploy: `bash deploy/deploy.sh` — or just push to `main`; CI ships the same three
files and runs `remote.sh` over SSH. The first run prints the `API_TOKEN` to put into
`~/.cafe-finance/config.json` on the Mac (reconciler) and the mail puller's env.

What runs: `cafe-finance` (web; migrates + seeds on boot) and `cafe-finance-worker`
(reads queued proofs with the shared Ollama's `llama3.2-vision`, which remote.sh pulls
if missing). DB `cafe_finance` on careai-postgres; proofs on the `cafe_finance_media`
volume — add it to the nightly backup. `pull_fastpos` runs from cron at 02:15 once a
cafe has a FastPOS owner token set in /admin/.
