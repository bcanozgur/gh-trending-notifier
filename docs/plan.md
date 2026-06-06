# Implementation Plan

## Decision

Build a Python 3.12 CLI and run it daily from GitHub Actions. Do not build an
always-on service for the MVP. This satisfies the core constraints: the user's
computer does not need to stay on, and the project can run without a paid
server when hosted in a public GitHub repository.

## Architecture

Pipeline:

1. `fetch`: download `https://github.com/trending?since=daily`.
2. `parse`: extract every repository card from the page.
3. `enrich`: call GitHub GraphQL/REST for repo metadata, README, topics,
   license, timestamps, releases, and activity.
4. `score`: compute transparent per-dimension scores.
5. `render`: create HTML and text newsletter output.
6. `send`: deliver email through a provider adapter.
7. `persist`: write daily archive, state, and sent marker.

Proposed file layout after implementation:

```text
src/gh_trending_notifier/
  cli.py
  trending.py
  github_client.py
  scoring.py
  render.py
  email_sender.py
  state.py
tests/
  fixtures/
templates/
data/
  runs/
  sent/
.github/workflows/daily.yml
```

## Scoring Model

Use a 100-point weighted score:

| Dimension | Weight | Evidence |
| --- | ---: | --- |
| Practical usefulness | 20 | Problem clarity, docs, examples, install path |
| AI workflow impact | 18 | Agents, evals, RAG, automation, model tooling |
| Ease of adoption | 14 | Quickstart, package manager, demo, config simplicity |
| Technical quality | 14 | Tests, structure, recent maintenance, dependency sanity |
| Momentum | 12 | Stars today, rank, forks, recent activity |
| Novelty | 8 | Differentiation from common wrappers/clones |
| Production readiness | 7 | License, security posture, release maturity |
| Strategic relevance | 7 | Broader developer trend value |

Initial implementation can compute deterministic heuristics. Optional AI
summaries can be added later behind a strict budget and fallback path.

## Email Structure

Subject pattern:

`GitHub Trending: {top_theme} - {date}`

Sections:

1. One-line editorial summary.
2. Top 5 ranked picks.
3. AI workflow impact.
4. Practical tools to try today.
5. Watchlist.
6. Hype or caution notes.
7. Full reviewed table.

## State Model

Commit generated state to the repo:

- `data/runs/YYYY-MM-DD.json`: full daily result.
- `data/state.json`: first seen, last seen, previous rank, previous stars.
- `data/sent/YYYY-MM-DD.json`: idempotency marker and message metadata.

Do not commit recipient addresses or credentials. Use GitHub Actions secrets or
variables for `MAIL_TO`, `MAIL_FROM`, and provider credentials.

## Deployment Choice

Recommended:

- GitHub Actions scheduled workflow.
- Non-top-of-hour cron, for example `17 6 * * *`.
- Add `workflow_dispatch` for manual reruns.
- Use `concurrency` to prevent duplicate runs.
- Commit state only after a successful run.

Fallback options:

- Google Apps Script if email is only personal and no repo-local Python runtime
  is desired.
- GitLab scheduled pipelines if the repo is mirrored to GitLab.
- Cloudflare Workers Cron only if the project is rewritten for edge constraints.

## MVP Phases

1. Scaffold Python package, lint/test tooling, and CLI skeleton.
2. Implement Trending parser with frozen HTML fixture tests.
3. Implement GitHub enrichment with mocked API tests.
4. Implement scoring with golden tests.
5. Implement HTML/text rendering with snapshot tests.
6. Implement email sender adapters and dry-run mode.
7. Add GitHub Actions workflow and state commit flow.
8. Run an end-to-end dry run, then enable real email sending.
9. Run `gh-trending-digest doctor` before enabling scheduled sending.

## Quality Gates

- Parser test must cover a real saved Trending HTML fixture.
- Ranking test must be deterministic from fixed JSON.
- Email rendering test must produce both HTML and text.
- Dry run must create `data/runs/YYYY-MM-DD.json` without sending.
- Send mode must skip if the sent marker already exists.
- Scheduled workflow must support manual dispatch.

## Open Decisions

1. Repository visibility: public is best for free Actions usage. Private still
   works but consumes included account minutes.
2. Email provider: Resend is the cleanest developer API; Brevo has a larger
   free daily quota; SMTP is most provider-neutral.
3. Audience size: personal/small list can use simple `MAIL_TO`; public
   newsletter needs consent, unsubscribe, and stronger deliverability handling.
4. AI summarization: deterministic MVP first, optional GitHub Models later.
