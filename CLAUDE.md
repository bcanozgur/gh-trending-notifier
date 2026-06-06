# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**GitHub Trending Digest** is a Python 3.12 CLI that generates daily intelligence emails from [GitHub Trending](https://github.com/trending). It:

1. Parses trending repositories from GitHub's daily trending page
2. Enriches them with GitHub API metadata (topics, license, README excerpt, etc.)
3. Ranks repositories using an 8-factor scoring system (practical usefulness, AI workflow impact, ease of adoption, technical quality, momentum, novelty, production readiness, strategic relevance)
4. Renders a concise HTML/text newsletter
5. Sends via SMTP, Resend, or Brevo (adapter pattern)
6. Stores run history and state in git (JSON files under `data/`)
7. Runs once daily (06:17 UTC) via GitHub Actions `schedule` + manual `workflow_dispatch`

**Philosophy**: Deterministic, explainable scoring. No always-on server. Free hosting via public GitHub Actions. State committed to git.

## Architecture

### Module Structure

```
src/gh_trending_notifier/
├── cli.py              # Entry point (run, doctor commands)
├── trending.py         # Parse github.com/trending HTML
├── github_client.py    # GitHub API enrichment (GraphQL + REST)
├── models.py           # TrendingRepo, RepoEnrichment, RankedRepo, ScoreBreakdown
├── scoring.py          # rank_repositories() — 8-factor scoring logic
├── render.py           # build_newsletter() — HTML/text generation
├── email_sender.py     # send_newsletter() — adapter for SMTP/Resend/Brevo
└── state.py            # read/write JSON state: runs/, sent/, state.json
```

### Data Flow

1. **Fetch**: `trending.py:fetch_trending_html()` → parse HTML → list[TrendingRepo]
2. **Enrich**: `github_client.py:GitHubClient.enrich_many()` → dict[str, RepoEnrichment]
3. **Rank**: `scoring.py:rank_repositories()` → list[RankedRepo] with scores
4. **Render**: `render.py:build_newsletter()` → Newsletter (subject + HTML + plaintext)
5. **Send**: `email_sender.py:send_newsletter()` → provider-specific delivery
6. **Store**: `state.py` records run metadata and sent markers in git

### Key Design Decisions

- **Adapter pattern for email**: `email_sender.py` dispatches to SMTP/Resend/Brevo based on `EMAIL_PROVIDER` env var.
- **JSON state, not DB**: All durable state lives in `data/` (runs, sent records, state.json). No external database.
- **Frozen dataclasses**: Models are immutable (`@dataclass(frozen=True)`).
- **Scoring terms**: `scoring.py` defines `AI_TERMS` and `DEV_TOOL_TERMS` keyword sets for categorization.

## Common Commands

### Run Tests

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

### Dry-Run from Fixture (No Network, No Credentials)

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m gh_trending_notifier.cli run \
  --date 2026-06-07 \
  --html-file tests/fixtures/trending_daily.html \
  --skip-enrichment
```

### Live Dry-Run from GitHub Trending (No Email Send)

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src GH_TOKEN=ghp_xxx python -m gh_trending_notifier.cli run
```

Optionally add `--timezone Europe/Istanbul` to override APP_TIMEZONE env var.

### Check Deployment Readiness

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m gh_trending_notifier.cli doctor
```

Checks for email config, GitHub token, templates, etc.

### Send Email After Rendering

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  EMAIL_PROVIDER=smtp \
  MAIL_FROM=sender@example.com \
  MAIL_TO=recipients@example.com \
  SMTP_HOST=smtp.example.com \
  SMTP_PORT=587 \
  SMTP_USERNAME=user \
  SMTP_PASSWORD=pass \
  python -m gh_trending_notifier.cli run --send
```

For Resend or Brevo, swap `EMAIL_PROVIDER` and set the corresponding API key env var instead of SMTP_* vars.

## Key Files & Responsibilities

| File | Purpose |
|------|---------|
| `cli.py` | CLI parser; `run` (fetch→rank→render→send) and `doctor` (readiness check) |
| `trending.py` | Scrape & parse `github.com/trending` HTML; extract TrendingRepo list |
| `github_client.py` | GitHub API calls (GraphQL + REST); fetch metadata, README, releases, etc. |
| `models.py` | Data models: TrendingRepo, RepoEnrichment, ScoreBreakdown, RankedRepo, Newsletter |
| `scoring.py` | 8-factor ranking algorithm; applies AI/dev-tool keywords, momentum decay, etc. |
| `render.py` | Convert RankedRepo list → Newsletter HTML/plaintext; uses Jinja2 templates |
| `email_sender.py` | Send via SMTP/Resend/Brevo; parse recipients; return SendResult with message ID |
| `state.py` | JSON I/O: run archives (runs/), sent markers (sent/), state.json (repo history) |

## State Storage

Under `data/`:

```
data/
├── state.json          # { "repos": { "owner/name": { "seen_on": [dates], "best_rank": N, ... } } }
├── runs/               # YYYY/MM/DD_HHMMSS.json — archive of each run (repos + scores + newsletter)
└── sent/               # YYYY/MM/DD.txt — timestamp of last successful send for that date
```

The workflow commits these files back to git after each run (see `.github/workflows/daily.yml`).

## Email Configuration

### Environment Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `EMAIL_PROVIDER` | `smtp` | `smtp` \| `resend` \| `brevo` |
| `MAIL_FROM` | — | Sender address |
| `MAIL_TO` | — | Comma-separated recipients |
| `SMTP_HOST` | — | SMTP server hostname |
| `SMTP_PORT` | — | SMTP port (typically 587 or 465) |
| `SMTP_USERNAME` | — | SMTP auth username |
| `SMTP_PASSWORD` | — | SMTP auth password |
| `RESEND_API_KEY` | — | Resend API key |
| `BREVO_API_KEY` | — | Brevo API key |

### Dry-Run vs. Sending

- `--send` flag + valid credentials → sends email
- No `--send` or missing credentials → renders only (dry-run)
- Sent marker in `data/sent/` prevents re-sending the same date

## GitHub Actions Workflow

**File**: `.github/workflows/daily.yml`

- **Trigger**: Cron `17 6 * * *` (06:17 UTC daily) + manual `workflow_dispatch` with optional `--send` input
- **Concurrency**: Singleton group; in-progress runs are not cancelled
- **Permissions**: `contents: write` to commit state
- **Env vars**: All email/GitHub config read from Actions secrets & variables
- **Steps**:
  1. Checkout with persist-credentials
  2. Setup Python 3.12
  3. Run tests (`python -m unittest discover -s tests`)
  4. Generate newsletter (conditionally send based on `SEND_EMAIL` var or workflow_dispatch input)
  5. Commit state changes to `data/` if present

**Repository setup** (from README):

```bash
git init -b main
git add .
git commit -m "Build GitHub Trending notifier MVP"
git remote add origin git@github.com:<you>/gh-trending-digest.git
git push -u origin main
```

Then configure Actions secrets & variables in GitHub UI:

- `MAIL_FROM`, `MAIL_TO` (required for any send)
- `SMTP_*` for SMTP, or `RESEND_API_KEY` / `BREVO_API_KEY` for API providers
- `APP_TIMEZONE` variable (defaults to `Europe/Istanbul` in workflow)
- `SEND_EMAIL` variable: set to `true` to enable production sends

## Development Notes

### Testing

- Tests in `tests/`; fixtures in `tests/fixtures/` (e.g., sample HTML)
- Use `--html-file` + `--skip-enrichment` for fast fixture-based tests without network
- `PYTHONDONTWRITEBYTECODE=1` prevents `.pyc` clutter

### Scoring Logic

The 8-factor system in `scoring.py:rank_repositories()` computes:

1. **Practical usefulness** — based on stars and stars-today
2. **AI workflow impact** — keyword match against `AI_TERMS`
3. **Ease of adoption** — inverse correlation with issue count
4. **Technical quality** — license, test prevalence, etc.
5. **Momentum** — stars-today vs. total-stars ratio
6. **Novelty** — creation/update recency
7. **Production readiness** — non-fork, not-archived, release presence
8. **Strategic relevance** — dev-tool keywords (`DEV_TOOL_TERMS`)

Each factor is normalized and weighted; total is the sum (capped at 100).

### Newsletter Rendering

- Templates in `templates/` (Jinja2 format)
- `render.py:build_newsletter()` produces both HTML and plaintext versions
- Newsletter object contains `subject`, `html_body`, `plaintext_body`, and run metadata

### Adding Support for a New Email Provider

1. Add provider detection in `email_sender.py:send_newsletter()`
2. Implement a send function (e.g., `_send_via_newprovider()`)
3. Return `SendResult` with provider name, recipients list, and message ID
4. Add env var documentation (e.g., `NEWPROVIDER_API_KEY`)

## Configuration & Deployment Readiness

Run `python -m gh_trending_notifier.cli doctor` to validate:

- Required env vars for chosen email provider
- Template files exist
- Data directories writable
- (Optional) GitHub token validity

## Building & Distribution

- **Package name**: `gh-trending-digest`
- **Entry point**: `gh-trending-digest` CLI command (via pyproject.toml `[project.scripts]`)
- **Build**: `hatchling` (requires `hatchling>=1.25`)
- **Python**: 3.12+ (strict)

To build a wheel:

```bash
python -m build  # if build is installed
```

Or let pip install from source:

```bash
pip install -e .
```

## Environment Variables Reference

**Optional, override defaults:**

- `APP_TIMEZONE` — IANA timezone (default: `Europe/Istanbul`)
- `GH_TOKEN` — GitHub API token for enrichment (read-only; optional for dry-run)
- `NEWSLETTER_MAX_REPOS` — max repositories featured per newsletter (default: `10`)
- `NEWSLETTER_DEDUPE_DAYS` — suppress a repo for N days after it was sent, so each
  edition only carries repos not already sent in that window (default: `7`; set `0` to disable)
- `PYTHONPATH=src` — must be set for import discovery
- `PYTHONDONTWRITEBYTECODE=1` — suppress `.pyc` generation (recommended for CLI)
