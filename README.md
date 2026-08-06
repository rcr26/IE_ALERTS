# Insider Trade Alerts

Same logic as before, with real error visibility added. It was almost certainly failing every run because `SEC_USER_AGENT` and/or `NTFY_TOPIC` weren't set as repo secrets — the script exits immediately (before contacting SEC or ntfy) if either is blank. That matches getting a failure email every time and zero notifications ever.

## Setup

1. New repo → upload these files, same structure (`.github/workflows/insider-alerts.yml` path must stay exact).
2. Settings → Secrets and variables → Actions → New repository secret:
   - `SEC_USER_AGENT` — format matters: `Your Name your@email.com` (a space between name and email, real-looking address). SEC rejects generic/malformed agents with a 403.
   - `NTFY_TOPIC` — an unguessable topic name, e.g. `insider-alerts-7f2k9x`. Subscribe to it in the ntfy app first.
3. Actions tab → enable workflows → run `insider-alerts.yml` manually once (Run workflow button). Check the run's log:
   - You'll now see `SEC_USER_AGENT set: True/False (len=N)` and `NTFY_TOPIC set: True/False (len=N)` as the first two lines — confirms secrets are actually reaching the job.
   - Any SEC or ntfy HTTP error now prints the actual status code and response body instead of a generic failure.
4. First run only seeds `seen.json` — no alert on that run by design. After that it checks every 15 minutes.

## On "as fast as possible"

`*/15` is close to the practical floor for free GitHub-hosted scheduled workflows — GitHub explicitly treats `schedule` as best-effort and can delay or skip runs under load, and disables schedules entirely after 60 days with no commits. Since this workflow commits `seen.json` on every run with a diff, disablement shouldn't be a real risk here, but expect occasional multi-minute drift regardless of the cron value — that's GitHub's scheduler, not something fixable in this script. `workflow_dispatch` is still there for manual runs any time.

## If it still fails after this

Paste the actual step log from the failed Action run (Actions tab → the red run → the failed step) — the added logging will point at the exact cause (missing secret, SEC 403 with its message body, or an ntfy error) rather than needing to guess.
