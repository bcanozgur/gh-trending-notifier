from __future__ import annotations

import argparse
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gh_trending_notifier.email_sender import parse_recipients, send_newsletter
from gh_trending_notifier.github_client import GitHubClient
from gh_trending_notifier.render import build_newsletter
from gh_trending_notifier.scoring import rank_repositories
from gh_trending_notifier.state import (
    has_sent,
    read_json,
    record_run,
    record_sent,
    state_path,
    update_state,
)
from gh_trending_notifier.trending import TRENDING_URL, fetch_trending_html, parse_trending_repos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a daily GitHub Trending newsletter.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Fetch, score, render, and optionally send.")
    run_parser.add_argument(
        "--date",
        default=None,
        help="ISO date, default: today in APP_TIMEZONE or UTC.",
    )
    run_parser.add_argument(
        "--timezone",
        default=os.getenv("APP_TIMEZONE", "UTC"),
        help="Timezone used when --date is omitted.",
    )
    run_parser.add_argument("--repo-root", default=".", help="Repository root for data files.")
    run_parser.add_argument("--html-file", default=None, help="Use a local Trending HTML file.")
    run_parser.add_argument("--skip-enrichment", action="store_true", help="Skip GitHub API enrichment.")
    run_parser.add_argument("--send", action="store_true", help="Send email after rendering.")
    run_parser.add_argument(
        "--email-provider",
        default=os.getenv("EMAIL_PROVIDER", "smtp"),
        choices=["smtp", "resend", "brevo"],
    )

    doctor_parser = subparsers.add_parser("doctor", help="Check local deployment readiness.")
    doctor_parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")

    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    if args.command == "doctor":
        return doctor_command(args)
    parser.error("unknown command")
    return 2


def run_command(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    date = args.date or default_run_date(args.timezone)

    if args.send and has_sent(repo_root, date):
        print(f"Newsletter for {date} already sent; skipping.")
        return 0

    document = _load_trending_document(args.html_file)
    repos = parse_trending_repos(document)
    if not repos:
        raise SystemExit("No repositories parsed from GitHub Trending.")

    state = read_json(state_path(repo_root), {"repos": {}})
    enrichments = {}
    if not args.skip_enrichment:
        token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
        enrichments = GitHubClient(token=token).enrich_many(repos)

    ranked = rank_repositories(repos, enrichments=enrichments, previous_state=state, today=date)
    newsletter = build_newsletter(date, ranked)
    run_file = record_run(repo_root, newsletter)

    print(f"Parsed {len(repos)} Trending repositories from {TRENDING_URL}.")
    print(f"Wrote run archive: {run_file}")
    print(f"Subject: {newsletter.subject}")
    write_github_step_summary(newsletter, run_file)

    if args.send:
        recipients = parse_recipients(os.getenv("MAIL_TO"))
        result = send_newsletter(newsletter, args.email_provider, recipients)
        sent_file = record_sent(
            root=repo_root,
            date=date,
            provider=result.provider,
            recipients=result.recipients,
            message_id=result.message_id,
        )
        update_state(repo_root, date, ranked)
        print(f"Sent via {result.provider}; marker: {sent_file}")
    else:
        update_state(repo_root, date, ranked)
        print("Dry run only; email not sent.")

    return 0


def _load_trending_document(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return fetch_trending_html()


def default_run_date(timezone_name: str, now: datetime | None = None) -> str:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"Unknown timezone: {timezone_name}") from exc
    reference = now or datetime.now(timezone)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return reference.astimezone(timezone).date().isoformat()


def write_github_step_summary(newsletter, run_file: Path) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    top_rows = "\n".join(
        f"- {item.repo.full_name}: {item.score.total:.1f}/100 ({', '.join(item.tags)})"
        for item in newsletter.ranked[:5]
    )
    content = (
        "## GitHub Trending Newsletter\n\n"
        f"- Date: `{newsletter.date}`\n"
        f"- Subject: `{newsletter.subject}`\n"
        f"- Archive: `{run_file}`\n\n"
        "### Top picks\n"
        f"{top_rows}\n"
    )
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(content)


def doctor_command(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    checks = [
        _check_path(repo_root / "pyproject.toml", "pyproject.toml exists"),
        _check_path(repo_root / ".github" / "workflows" / "daily.yml", "daily workflow exists"),
        _check_git_repo(repo_root),
        _check_git_remote(repo_root),
        _check_env("MAIL_TO", required=False),
        _check_env("MAIL_FROM", required=False),
        _check_provider_env(os.getenv("EMAIL_PROVIDER", "smtp")),
    ]

    for ok, message in checks:
        status = "ok" if ok else "warn"
        print(f"{status}: {message}")

    required_ok = checks[0][0] and checks[1][0] and checks[2][0] and checks[3][0]
    if not required_ok:
        print("Deployment is not ready: initialize a Git repo, add a GitHub remote, and push first.")
        return 1
    if not all(ok for ok, _ in checks[4:]):
        print("Dry-run is ready. Real email sending still needs provider secrets.")
        return 0
    print("Deployment preflight passed.")
    return 0


def _check_path(path: Path, label: str) -> tuple[bool, str]:
    return path.exists(), label


def _check_git_repo(repo_root: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False, "git command is not available"
    ok = result.stdout.strip() == "true"
    return ok, "directory is a Git repository" if ok else "directory is not a Git repository"


def _check_git_remote(repo_root: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False, "origin remote is not configured"
    ok = result.returncode == 0 and bool(result.stdout.strip())
    return ok, "origin remote is configured" if ok else "origin remote is not configured"


def _check_env(name: str, required: bool) -> tuple[bool, str]:
    value = os.getenv(name)
    if value:
        return True, f"{name} is set"
    if required:
        return False, f"{name} is required"
    return False, f"{name} is not set; required only for real email sending"


def _check_provider_env(provider: str) -> tuple[bool, str]:
    required_by_provider = {
        "smtp": ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"],
        "resend": ["RESEND_API_KEY"],
        "brevo": ["BREVO_API_KEY"],
    }
    missing = [name for name in required_by_provider.get(provider, []) if not os.getenv(name)]
    if missing:
        return False, f"{provider} provider missing secrets: {', '.join(missing)}"
    return True, f"{provider} provider secrets are set"


if __name__ == "__main__":
    raise SystemExit(main())
