import datetime

import pytest
from aw_core import Event

from aw_watcher_ask_away.selection import PromptSelector, SelectionPolicy

UTC = datetime.UTC


def block(hour: float, minutes: float = 20, category: str = "Work", day: int = 1) -> Event:
    """A block that closes at ``hour`` (UTC) on 2026-01-``day``."""
    close = datetime.datetime(2026, 1, day, tzinfo=UTC) + datetime.timedelta(hours=hour)
    duration = datetime.timedelta(minutes=minutes)
    return Event(timestamp=close - duration, duration=duration, data={"category": category})


def admitted(selector: PromptSelector, hours: list[float], day: int = 1) -> list[float]:
    return [h for h in hours if selector.admit(block(h, day=day))]


def test_dense_day_is_spread_across_waking_hours():
    # A block closes every 30 minutes from 08:00 to 21:30.
    hours = [8 + i / 2 for i in range(28)]
    policy = SelectionPolicy(max_per_day=4, min_gap=0)
    stratified = admitted(PromptSelector(policy, UTC), hours)
    greedy = admitted(PromptSelector(SelectionPolicy(max_per_day=4, min_gap=0, stratify=False), UTC), hours)

    assert greedy == [8, 8.5, 9, 9.5]
    # 14 waking hours / 4 slots = 3.5h per slot; first close in each slot wins.
    assert stratified == [8, 11.5, 15, 18.5]


def test_quiet_hours_suppress_prompts():
    selector = PromptSelector(SelectionPolicy(min_gap=0), UTC)
    assert admitted(selector, [7.5, 22, 23.5]) == []
    assert selector.admit(block(8.5))


def test_min_gap_between_prompts():
    selector = PromptSelector(SelectionPolicy(max_per_day=24, min_gap=45 * 60, stratify=False), UTC)
    assert admitted(selector, [9, 9.5, 9.75, 10.5]) == [9, 9.75, 10.5]


def test_daily_cap_resets_on_the_next_waking_day():
    policy = SelectionPolicy(max_per_day=2, min_gap=0, stratify=False)
    selector = PromptSelector(policy, UTC)
    assert admitted(selector, [9, 10, 11], day=1) == [9, 10]
    assert admitted(selector, [9, 10, 11], day=2) == [9, 10]


def test_waking_day_can_cross_midnight():
    # Night owl: quiet from 04:00 to 12:00, so 01:00 belongs to the previous waking day.
    policy = SelectionPolicy(max_per_day=1, min_gap=0, quiet_start=4, quiet_end=12, stratify=False)
    selector = PromptSelector(policy, UTC)
    assert selector.admit(block(13, day=1))
    assert not selector.admit(block(25, day=1))  # 01:00 on day 2, same waking day
    assert selector.admit(block(13, day=2))


def test_rediscovered_block_is_not_given_a_second_chance():
    selector = PromptSelector(SelectionPolicy(max_per_day=2, min_gap=0), UTC)
    first = block(9)
    assert selector.admit(first)
    # The next poll yields the same block again (it is unposted until answered).
    assert not selector.admit(block(9))
    rejected = block(22)
    assert not selector.admit(rejected)
    assert not selector.admit(block(22))


def test_local_timezone_drives_quiet_hours():
    stockholm = datetime.timezone(datetime.timedelta(hours=1))
    selector = PromptSelector(SelectionPolicy(min_gap=0), stockholm)
    # 21:30 UTC is 22:30 local: quiet.
    assert not selector.admit(block(21.5))
    # 07:30 UTC is 08:30 local: awake.
    assert selector.admit(block(7.5, day=2))


def test_no_quiet_hours():
    policy = SelectionPolicy(max_per_day=24, min_gap=0, quiet_start=0, quiet_end=0)
    assert policy.waking_hours == 24
    assert admitted(PromptSelector(policy, UTC), [3, 23.5]) == [3, 23.5]


@pytest.mark.parametrize("kwargs", [{"max_per_day": 0}, {"quiet_start": 24}, {"quiet_end": -1}])
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError, match="must"):
        SelectionPolicy(**kwargs)
