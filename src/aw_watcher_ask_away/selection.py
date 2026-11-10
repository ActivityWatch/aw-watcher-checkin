"""Decide which window-block closes earn a prompt.

A normal working day has many more substantive block closes than anyone will
answer, so window check-ins need a daily cap, a minimum spacing and quiet hours.
The cap also needs a selection rule. Taking the first N closes of the day puts
every prompt in the morning and never samples the afternoon or evening, which
are the hours most likely to feel tired or flat. That skews the collected ratings
upward. Instead the waking window is split into ``max_per_day`` equal slots with
at most one prompt per slot, i.e. time-stratified experience sampling.

Every decision is causal (no lookahead) and keyed on the block itself, so a block
rediscovered by a later poll gets its original answer instead of a second chance.
AFK-return check-ins are not gated here: they already land at a natural break.
"""

import datetime
from collections import OrderedDict
from dataclasses import dataclass, field

from aw_core import Event

from aw_watcher_ask_away.core import logger

_REMEMBERED_DECISIONS = 512


@dataclass(frozen=True)
class SelectionPolicy:
    max_per_day: int = 6
    min_gap: float = 2700
    """Minimum seconds between two window check-ins."""
    quiet_start: int = 22
    """Local hour (inclusive) at which quiet hours begin."""
    quiet_end: int = 8
    """Local hour (exclusive) at which quiet hours end and the waking day starts."""
    stratify: bool = True

    def __post_init__(self):
        if self.max_per_day < 1:
            msg = "max_per_day must be at least 1"
            raise ValueError(msg)
        if not (0 <= self.quiet_start < 24 and 0 <= self.quiet_end < 24):  # noqa: PLR2004
            msg = "quiet hours must be within 0-23"
            raise ValueError(msg)

    @property
    def waking_hours(self) -> float:
        return (self.quiet_start - self.quiet_end) % 24 or 24

    def is_quiet(self, local: datetime.datetime) -> bool:
        if self.quiet_start == self.quiet_end:
            return False
        offset = (local.hour - self.quiet_end) % 24
        return offset >= self.waking_hours

    def waking_day(self, local: datetime.datetime) -> datetime.date:
        """The calendar day the waking period containing ``local`` started on."""
        return (local - datetime.timedelta(hours=self.quiet_end)).date()

    def slot(self, local: datetime.datetime) -> int:
        offset = (local.hour + local.minute / 60 - self.quiet_end) % 24
        return min(int(offset / (self.waking_hours / self.max_per_day)), self.max_per_day - 1)


@dataclass
class PromptSelector:
    policy: SelectionPolicy
    tz: datetime.tzinfo
    _decided: OrderedDict[tuple[datetime.datetime, str], bool] = field(default_factory=OrderedDict)
    _slots: dict[datetime.date, set[int]] = field(default_factory=dict)
    _counts: dict[datetime.date, int] = field(default_factory=dict)
    _last: datetime.datetime | None = None

    def admit(self, block: Event) -> bool:
        """Return True if this block close should be prompted for, and record it."""
        key = (block.timestamp, str(block.data.get("category")))
        if key in self._decided:
            return False
        close = (block.timestamp + block.duration).astimezone(self.tz)
        reason = self._rejection(close)
        admitted = reason is None
        self._decided[key] = admitted
        if len(self._decided) > _REMEMBERED_DECISIONS:
            self._decided.popitem(last=False)
        if admitted:
            day = self.policy.waking_day(close)
            self._counts[day] = self._counts.get(day, 0) + 1
            self._slots.setdefault(day, set()).add(self.policy.slot(close))
            self._last = close
        else:
            logger.info(f"Skipping block check-in closing at {close.isoformat()}: {reason}.")
        return admitted

    def _rejection(self, close: datetime.datetime) -> str | None:
        policy = self.policy
        if policy.is_quiet(close):
            return "quiet hours"
        if self._last is not None and abs((close - self._last).total_seconds()) < policy.min_gap:
            return "too soon after the previous check-in"
        day = policy.waking_day(close)
        if self._counts.get(day, 0) >= policy.max_per_day:
            return "daily cap reached"
        if policy.stratify and policy.slot(close) in self._slots.get(day, set()):
            return "this part of the day already has a check-in"
        return None
