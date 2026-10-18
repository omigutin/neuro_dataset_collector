from __future__ import annotations

from neuro_dataset_collector.config import ConsistencyConfig
from neuro_dataset_collector.consistency import FlipAction, StreamConsistency

KEY = ("v", "1")


def _feed(tracker: StreamConsistency[int], labels: list[str]) -> list[tuple[int, str | None, FlipAction]]:
    decisions = []
    for i, label in enumerate(labels):
        for d in tracker.observe(KEY, label, i):
            decisions.append((d.item, d.proposed, d.action))
    return decisions


def test_isolated_flip_is_suspect() -> None:
    tracker: StreamConsistency[int] = StreamConsistency(ConsistencyConfig(enabled=True, context=3, max_run=2))
    decisions = _feed(tracker, ["FULL"] * 3 + ["EMPTY"] + ["FULL"] * 3)
    assert decisions == [(3, "FULL", FlipAction.SUSPECT)]


def test_long_run_is_new_state() -> None:
    tracker: StreamConsistency[int] = StreamConsistency(ConsistencyConfig(enabled=True, context=3, max_run=2))
    assert _feed(tracker, ["FULL"] * 3 + ["EMPTY"] * 3 + ["FULL"] * 3) == []


def test_not_enough_context_before() -> None:
    tracker: StreamConsistency[int] = StreamConsistency(ConsistencyConfig(enabled=True, context=3, max_run=2))
    assert _feed(tracker, ["FULL"] * 2 + ["EMPTY"] + ["FULL"] * 3) == []


def test_review_always_never_proposes() -> None:
    config = ConsistencyConfig(enabled=True, context=2, max_run=2, review_always=["UNKNOWN"])
    tracker: StreamConsistency[int] = StreamConsistency(config)
    decisions = _feed(tracker, ["FULL"] * 2 + ["UNKNOWN"] * 2 + ["FULL"] * 2)
    assert decisions == [(2, None, FlipAction.SUSPECT), (3, None, FlipAction.SUSPECT)]


def test_trusted_flip_auto_fix() -> None:
    config = ConsistencyConfig(enabled=True, context=2, max_run=1, trusted_flips={"EMPTY": ["FULL"]})
    tracker: StreamConsistency[int] = StreamConsistency(config)
    assert _feed(tracker, ["FULL", "FULL", "EMPTY", "FULL", "FULL"]) == [(2, "FULL", FlipAction.AUTO_FIX)]


def test_mixed_short_run_flagged() -> None:
    tracker: StreamConsistency[int] = StreamConsistency(ConsistencyConfig(enabled=True, context=2, max_run=2))
    decisions = _feed(tracker, ["FULL", "FULL", "EMPTY", "UNKNOWN", "FULL", "FULL"])
    assert [d[0] for d in decisions] == [2, 3]


def test_flicker_inside_recovery_is_one_run() -> None:
    tracker: StreamConsistency[int] = StreamConsistency(ConsistencyConfig(enabled=True, context=2, max_run=2))
    decisions = _feed(tracker, ["FULL", "FULL", "EMPTY", "FULL", "EMPTY", "FULL", "FULL"])
    assert [d[0] for d in decisions] == [2, 4]


def test_reset_forgets_run() -> None:
    tracker: StreamConsistency[int] = StreamConsistency(ConsistencyConfig(enabled=True, context=2, max_run=2))
    _feed(tracker, ["FULL", "FULL", "EMPTY"])
    tracker.reset()
    assert _feed(tracker, ["FULL", "FULL"]) == []
