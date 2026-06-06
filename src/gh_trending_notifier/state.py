from __future__ import annotations

import hashlib
import json
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path
from typing import Any

from gh_trending_notifier.models import Newsletter, RankedRepo


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def state_path(root: Path) -> Path:
    return root / "data" / "state.json"


def run_path(root: Path, date: str) -> Path:
    return root / "data" / "runs" / f"{date}.json"


def sent_path(root: Path, date: str) -> Path:
    return root / "data" / "sent" / f"{date}.json"


def preview_paths(root: Path, date: str) -> tuple[Path, Path]:
    preview_dir = root / "data" / "previews"
    return preview_dir / f"{date}.html", preview_dir / f"{date}.txt"


def has_sent(root: Path, date: str) -> bool:
    return sent_path(root, date).exists()


def record_run(
    root: Path,
    newsletter: Newsletter,
    reviewed: list[RankedRepo] | None = None,
) -> Path:
    path = run_path(root, newsletter.date)
    payload = newsletter.to_dict()
    # The rendered HTML/text are written to the preview files below, so drop them
    # from the run archive to avoid storing the same large blobs twice per day.
    payload.pop("html", None)
    payload.pop("text", None)
    # `newsletter.ranked` only holds the repos actually featured (top N after
    # dedupe). Persist the complete reviewed set so the archive keeps the full
    # audit trail of every repo and score considered for this run.
    if reviewed is not None:
        payload["reviewed"] = [item.to_dict() for item in reviewed]
    write_json(path, payload)
    html_path, text_path = preview_paths(root, newsletter.date)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(newsletter.html, encoding="utf-8")
    text_path.write_text(newsletter.text, encoding="utf-8")
    return path


def recently_sent_names(state: dict, today: str, window_days: int) -> set[str]:
    """Repo full_names that were included in a sent newsletter within the last
    `window_days` days (so they should be skipped to avoid re-sending)."""
    if window_days <= 0:
        return set()
    try:
        today_date = date_type.fromisoformat(today)
    except ValueError:
        return set()
    skip: set[str] = set()
    for name, entry in state.get("repos", {}).items():
        last_sent = entry.get("last_sent")
        if not last_sent:
            continue
        try:
            sent_date = date_type.fromisoformat(last_sent)
        except (ValueError, TypeError):
            continue
        if 0 <= (today_date - sent_date).days < window_days:
            skip.add(name)
    return skip


def update_state(
    root: Path,
    date: str,
    ranked: list[RankedRepo],
    sent_names: set[str] | None = None,
) -> Path:
    path = state_path(root)
    current = read_json(path, {"repos": {}})
    repos = current.setdefault("repos", {})
    for item in ranked:
        entry = repos.setdefault(item.repo.full_name, {})
        entry.setdefault("first_seen", date)
        entry["last_seen"] = date
        entry["last_rank"] = item.repo.rank
        entry["last_score"] = item.score.total
        entry["last_stars_today"] = item.repo.stars_today
        entry["last_total_stars"] = item.repo.total_stars
        if sent_names and item.repo.full_name in sent_names:
            entry["last_sent"] = date
    current["last_run"] = date
    write_json(path, current)
    return path


def _date_from_stem(stem: str) -> date_type | None:
    try:
        return date_type.fromisoformat(stem)
    except ValueError:
        return None


def prune_old_data(root: Path, today: str, retention_days: int) -> int:
    """Delete dated run/preview/sent files and drop state entries not seen within
    `retention_days` days, so committed history stays bounded. Returns the number
    of files removed. `retention_days <= 0` disables pruning."""
    if retention_days <= 0:
        return 0
    try:
        cutoff = date_type.fromisoformat(today) - timedelta(days=retention_days)
    except ValueError:
        return 0

    data_dir = root / "data"
    removed = 0
    dated_files = [
        (data_dir / "runs", "*.json"),
        (data_dir / "previews", "*.html"),
        (data_dir / "previews", "*.txt"),
        (data_dir / "sent", "*.json"),
    ]
    for directory, pattern in dated_files:
        if not directory.exists():
            continue
        for path in directory.glob(pattern):
            file_date = _date_from_stem(path.stem)
            if file_date is not None and file_date < cutoff:
                path.unlink()
                removed += 1

    state_file = state_path(root)
    state = read_json(state_file, None)
    if isinstance(state, dict) and isinstance(state.get("repos"), dict):
        repos = state["repos"]
        stale = []
        for name, entry in repos.items():
            last_seen = entry.get("last_seen") if isinstance(entry, dict) else None
            seen_date = _date_from_stem(last_seen) if isinstance(last_seen, str) else None
            if seen_date is not None and seen_date < cutoff:
                stale.append(name)
        if stale:
            for name in stale:
                del repos[name]
            write_json(state_file, state)
    return removed


def record_sent(root: Path, date: str, provider: str, recipients: list[str], message_id: str) -> Path:
    recipient_hashes = [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in recipients]
    path = sent_path(root, date)
    write_json(
        path,
        {
            "date": date,
            "provider": provider,
            "recipient_hashes": recipient_hashes,
            "message_id": message_id,
        },
    )
    return path
