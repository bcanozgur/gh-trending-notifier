import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase

from gh_trending_notifier.cli import _select_for_newsletter
from gh_trending_notifier.models import RankedRepo, ScoreBreakdown, TrendingRepo
from gh_trending_notifier.state import read_json, recently_sent_names, state_path, update_state


def _ranked(full_name: str, rank: int, score: float = 50.0) -> RankedRepo:
    owner, name = full_name.split("/")
    repo = TrendingRepo(
        rank=rank,
        owner=owner,
        name=name,
        description="",
        url=f"https://github.com/{full_name}",
        language=None,
        total_stars=100,
        forks=1,
        stars_today=10,
    )
    breakdown = ScoreBreakdown(
        practical_usefulness=score,
        ai_workflow_impact=0,
        ease_of_adoption=0,
        technical_quality=0,
        momentum=0,
        novelty=0,
        production_readiness=0,
        strategic_relevance=0,
    )
    return RankedRepo(repo=repo, enrichment=None, score=breakdown, tags=[], verdict="ok")


class DedupeLimitTests(TestCase):
    def test_caps_newsletter_at_ten_repositories(self) -> None:
        ranked = [_ranked(f"acme/repo{i}", i) for i in range(1, 16)]
        selected = _select_for_newsletter(ranked, {"repos": {}}, "2026-06-07")
        self.assertEqual(len(selected), 10)
        self.assertEqual(selected[0].repo.full_name, "acme/repo1")

    def test_recently_sent_names_respects_seven_day_window(self) -> None:
        today = date(2026, 6, 7)
        state = {
            "repos": {
                "a/yesterday": {"last_sent": (today - timedelta(days=1)).isoformat()},
                "a/sixdays": {"last_sent": (today - timedelta(days=6)).isoformat()},
                "a/sevendays": {"last_sent": (today - timedelta(days=7)).isoformat()},
                "a/never": {"last_seen": today.isoformat()},
            }
        }
        skip = recently_sent_names(state, today.isoformat(), 7)
        self.assertEqual(skip, {"a/yesterday", "a/sixdays"})

    def test_second_day_only_sends_new_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day1 = "2026-06-07"
            day2 = "2026-06-08"

            # Day 1: ten repos trending, all new -> all selected and recorded as sent.
            day1_ranked = [_ranked(f"acme/repo{i}", i) for i in range(1, 11)]
            selected1 = _select_for_newsletter(day1_ranked, {"repos": {}}, day1)
            self.assertEqual(len(selected1), 10)
            update_state(
                root, day1, day1_ranked, sent_names={r.repo.full_name for r in selected1}
            )

            # Day 2: same ten still trending plus two newcomers.
            state = read_json(state_path(root), {"repos": {}})
            day2_ranked = [_ranked(f"acme/repo{i}", i) for i in range(1, 11)] + [
                _ranked("acme/new-a", 11),
                _ranked("acme/new-b", 12),
            ]
            selected2 = _select_for_newsletter(day2_ranked, state, day2)

            names = {r.repo.full_name for r in selected2}
            self.assertEqual(names, {"acme/new-a", "acme/new-b"})
