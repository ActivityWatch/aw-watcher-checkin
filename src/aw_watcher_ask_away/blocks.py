"""Opt-in window block detection; polling horizons are never block boundaries."""

import datetime
from collections.abc import Iterator
from dataclasses import dataclass

from aw_core import Event


@dataclass(frozen=True)
class BlockTriggers:
    category_switch: bool = False
    long_block: bool = False
    sustain: float = 120
    minimum: float = 900
    long: float = 2700
    gap: float = 300


def closed_blocks(events: list[Event], config: BlockTriggers) -> Iterator[tuple[Event, datetime.datetime]]:
    """Yield (block, confirmation time) from categorized, non-AFK window events.

    Consecutive events in the same category form runs. Short detours are absorbed
    into the preceding block, while a sustained new category closes it at the
    start of that category. A five-minute data/AFK gap also closes a block, but
    only after activity resumes. Overlapping snapshots never add duration twice.
    """
    runs: list[Event] = []
    for event in sorted(events, key=lambda e: (e.timestamp, e.duration)):
        if event.duration.total_seconds() <= 0:
            continue
        start, end = event.timestamp, event.timestamp + event.duration
        if runs:
            previous = runs[-1]
            previous_end = previous.timestamp + previous.duration
            if end <= previous_end:
                continue
            if previous.data["$category"] == event.data["$category"] and start <= previous_end + datetime.timedelta(
                seconds=1
            ):
                previous.duration = end - previous.timestamp
                continue
            start = max(start, previous_end)
        runs.append(Event(timestamp=start, duration=end - start, data={"$category": event.data["$category"]}))

    if not runs:
        return
    current = runs[0]
    for run in runs[1:]:
        current_end = current.timestamp + current.duration
        gap = (run.timestamp - current_end).total_seconds() >= config.gap
        switched = run.data["$category"] != current.data["$category"] and run.duration.total_seconds() >= config.sustain
        if gap or switched:
            duration = current.duration.total_seconds()
            trigger = None
            if switched and not gap and config.category_switch and duration >= config.minimum:
                trigger = "category_switch"
            elif config.long_block and duration >= config.long:
                trigger = "long_block"
            if trigger:
                confirmation = run.timestamp if gap else run.timestamp + datetime.timedelta(seconds=config.sustain)
                yield Event(
                    timestamp=current.timestamp,
                    duration=current.duration,
                    data={
                        "trigger": trigger,
                        "category": current.data["$category"],
                        "closed_by": "gap" if gap else "switch",
                    },
                ), confirmation
            current = run
        else:
            current.duration = run.timestamp + run.duration - current.timestamp
    # The last run can still be growing, even if it is already hours long.
