"""Regression tests for server restarts, failed inserts and long or unusual absences."""

import datetime as dt
import os
import subprocess
import sys
from unittest import mock

import aw_core
import requests

from aw_watcher_ask_away import core
from aw_watcher_ask_away.core import AWAskAwayClient, AWAskAwayState


def ev(start: dt.datetime, secs: float, status: str) -> aw_core.Event:
    return aw_core.Event(timestamp=start, duration=secs, data={"status": status})


def make_client(buckets: dict, afk_events: list[aw_core.Event]) -> mock.MagicMock:
    c = mock.MagicMock()
    c.client_hostname = "host"
    c.get_buckets.return_value = buckets
    c.get_events.side_effect = lambda b, **_: [] if b.startswith(core.WATCHER_NAME) else afk_events
    return c


def recent_gap_events() -> list[aw_core.Event]:
    now = core.get_utc_now()
    return [
        ev(now - dt.timedelta(seconds=10), 10, "not-afk"),
        ev(now - dt.timedelta(seconds=1000), 990, "afk"),
        ev(now - dt.timedelta(seconds=2000), 1000, "not-afk"),
    ]


def test_server_restart_is_logged_not_raised():
    c = make_client({"aw-watcher-afk_host": {}}, [])
    s = AWAskAwayClient(c)
    c.get_events.side_effect = requests.ConnectionError("Connection refused")
    assert list(s.get_new_afk_events_to_note(600, 300)) == []


def test_failed_insert_is_not_marked_seen():
    c = make_client({"aw-watcher-afk_host": {}}, recent_gap_events())
    s = AWAskAwayClient(c)
    [gap] = list(s.get_new_afk_events_to_note(600, 300))
    c.insert_event.side_effect = requests.ConnectionError("down")
    assert s.post_event(gap, "lunch") is False
    # Nothing reached the server, so the gap must be offered again.
    assert len(list(s.get_new_afk_events_to_note(600, 300))) == 1


def test_successful_insert_is_marked_seen():
    c = make_client({"aw-watcher-afk_host": {}}, recent_gap_events())
    s = AWAskAwayClient(c)
    [gap] = list(s.get_new_afk_events_to_note(600, 300))
    assert s.post_event(gap, "lunch") is True
    assert c.insert_event.call_args.args[1].data[core.DATA_KEY] == "lunch"
    assert list(s.get_new_afk_events_to_note(600, 300)) == []


def test_absence_longer_than_a_day_is_prompted():
    now = core.get_utc_now()
    gap = dt.timedelta(days=1, seconds=60)
    events = [
        ev(now - dt.timedelta(seconds=10), 10, "not-afk"),
        ev(now - dt.timedelta(seconds=10) - gap - dt.timedelta(seconds=100), 100, "not-afk"),
    ]
    assert len(list(AWAskAwayState([]).get_unseen_afk_events(events, 600, 300))) == 1


def test_empty_afk_bucket_does_not_crash():
    s = AWAskAwayClient(make_client({"aw-watcher-afk_host": {}}, []))
    assert list(s.get_new_afk_events_to_note(600, 300)) == []


def test_own_host_afk_bucket_preferred_over_others():
    c = make_client({"aw-watcher-afk_host": {}, "aw-watcher-afk_oldhost": {}}, [])
    assert AWAskAwayClient(c).afk_bucket_id == "aw-watcher-afk_host"


def test_prompt_text_long_absence_and_dst():
    # Run in a subprocess so TZ applies from interpreter start, like a long-running watcher.
    code = (
        "import datetime as d, aw_core\n"
        "from aw_watcher_ask_away.core import prompt_text\n"
        "jan = aw_core.Event(timestamp=d.datetime(2026,1,15,12,tzinfo=d.UTC), duration=d.timedelta(hours=25))\n"
        "jul = aw_core.Event(timestamp=d.datetime(2026,7,15,12,tzinfo=d.UTC), duration=60)\n"
        "print(prompt_text(jan)); print(prompt_text(jul))\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env={**os.environ, "TZ": "America/New_York"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert out[0] == "What were you doing from 07:00 - 08:00 (1500.0 minutes)?"
    assert out[1] == "What were you doing from 08:00 - 08:01 (1.0 minutes)?"
