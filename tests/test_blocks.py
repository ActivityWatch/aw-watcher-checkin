import datetime
from copy import deepcopy

from aw_core import Event
from aw_transform.classify import Rule

from aw_watcher_ask_away import core
from aw_watcher_ask_away.blocks import BlockTriggers, closed_blocks
from aw_watcher_ask_away.core import AWAskAwayClient

START = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def window(start, duration, category="Work"):
    return Event(timestamp=START + datetime.timedelta(seconds=start), duration=duration, data={"$category": [category]})


def blocks(events, **kwargs):
    return [event for event, _ in closed_blocks(events, BlockTriggers(**kwargs))]


def test_flags_off_and_open_long_block():
    assert blocks([window(0, 3600), window(3600, 120, "Chat")]) == []
    assert blocks([window(0, 7200)], category_switch=True, long_block=True) == []
    assert blocks([], category_switch=True) == []


def test_switch_waits_for_sustain_then_labels_previous_block():
    assert blocks([window(0, 900), window(900, 119, "Chat")], category_switch=True) == []
    events = [window(0, 900), window(900, 120, "Chat")]
    original = deepcopy(events)
    result = list(closed_blocks(events, BlockTriggers(category_switch=True)))
    event, confirmed = result[0]
    assert event.timestamp == START
    assert event.duration.total_seconds() == 900
    assert event.data == {"trigger": "category_switch", "category": ["Work"], "closed_by": "switch"}
    assert confirmed == START + datetime.timedelta(seconds=1020)
    assert events == original


def test_short_blocks_and_detours():
    assert blocks([window(0, 899), window(899, 120, "Chat")], category_switch=True) == []
    events = [window(0, 900), window(900, 60, "Chat"), window(960, 900), window(1860, 120, "Chat")]
    result = blocks(events, category_switch=True)
    assert len(result) == 1
    assert result[0].duration.total_seconds() == 1860


def test_fragmented_categories_and_overlapping_snapshots():
    events = [window(0, 900), window(900, 70, "Chat"), window(950, 70, "Chat")]
    result = blocks(list(reversed(events)), category_switch=True)
    assert len(result) == 1
    assert result[0].duration.total_seconds() == 900
    # Duplicate snapshots must not turn sixty seconds of Chat into two minutes.
    assert blocks([window(0, 900), window(900, 60, "Chat"), window(900, 60, "Chat")], category_switch=True) == []


def test_zero_length_and_jittered_adjacent_windows():
    events = [window(0, 900), window(900, 0, "Chat"), window(900, 60, "Chat"), window(960.001, 60, "Chat")]
    assert len(blocks(events, category_switch=True)) == 1


def test_long_block_closes_on_switch_or_observed_gap():
    result = blocks([window(0, 2700), window(2700, 120, "Chat")], long_block=True)
    assert result[0].data["trigger"] == "long_block"
    assert blocks([window(0, 2699), window(3000, 120)], long_block=True) == []
    result = blocks([window(0, 2700), window(3000, 10)], long_block=True)
    assert result[0].data["closed_by"] == "gap"
    assert result[0].duration.total_seconds() == 2700
    assert blocks([window(0, 2700), window(2999, 10)], long_block=True) == []


def test_switch_has_priority_and_gap_does_not_count_as_switch():
    events = [window(0, 2700), window(2700, 120, "Chat")]
    assert len(blocks(events, category_switch=True, long_block=True)) == 1
    assert blocks(events, category_switch=True, long_block=True)[0].data["trigger"] == "category_switch"
    assert blocks([window(0, 900), window(1200, 120, "Chat")], category_switch=True) == []


class MemoryClient:
    client_hostname = "test"

    def __init__(self, windows, afk, saved=()):
        self.events = {"window": windows, "afk_test": afk, "aw-watcher-ask-away_test": list(saved)}
        self.requests = []

    def get_buckets(self):
        return {key: {} for key in self.events}

    def get_events(self, bucket, **kwargs):
        self.requests.append((bucket, kwargs))
        return deepcopy(self.events[bucket])

    def insert_event(self, bucket, event):
        self.events[bucket].append(deepcopy(event))


def afk_event(start, duration, status="not-afk"):
    event = window(start, duration)
    event.data = {"status": status}
    return event


def client_for(monkeypatch, windows, afk=None, now=1020, saved=()):
    monkeypatch.setattr(core, "get_utc_now", lambda: START + datetime.timedelta(seconds=now))
    raw = []
    for source in windows:
        event = deepcopy(source)
        event.data = {"app": event.data["$category"][0], "title": ""}
        raw.append(event)
    transport = MemoryClient(raw, afk if afk is not None else [afk_event(0, now)], saved)
    client = AWAskAwayClient(transport)
    return client, transport


CATEGORIES = [([name], Rule({"regex": name, "select_keys": ["app", "title"]})) for name in ("Work", "Chat")]


def poll(client, **kwargs):
    return list(client.get_new_window_events_to_note("window", CATEGORIES, BlockTriggers(**kwargs), seconds=600))


def test_client_category_rules_post_reload_and_jitter_dedupe(monkeypatch):
    client, transport = client_for(monkeypatch, [window(0, 900), window(900, 120, "Chat")])
    event = poll(client, category_switch=True)[0]
    client.post_event(event, "focused")
    assert poll(client, category_switch=True) == []
    transport.events["window"][0].timestamp += datetime.timedelta(milliseconds=1)
    reloaded = AWAskAwayClient(transport)
    assert poll(reloaded, category_switch=True) == []
    saved = transport.events[client.bucket_id][0]
    assert saved.id is None
    assert saved.data["message"] == "focused"
    assert saved.duration.total_seconds() == 900
    assert transport.events["window"][0].data == {"app": "Work", "title": ""}
    assert all(options["limit"] == -1 for bucket, options in transport.requests if bucket == "window")


def test_client_disabled_does_not_fetch_windows(monkeypatch):
    client, transport = client_for(monkeypatch, [])
    transport.requests.clear()
    assert poll(client) == []
    assert transport.requests == []


def test_client_empty_current_afk_stale_and_zero_length_wake(monkeypatch):
    windows = [window(0, 900), window(900, 120, "Chat")]
    for afk in ([], [afk_event(0, 1020, "afk")], [afk_event(0, 900)], [afk_event(0, 900, "afk"), afk_event(1020, 0)]):
        client, _ = client_for(monkeypatch, windows, afk)
        assert poll(client, category_switch=True) == []


def test_client_long_window_is_clipped_at_afk_and_waits_for_return(monkeypatch):
    windows = [window(0, 3600)]
    afk = [afk_event(0, 2700), afk_event(2700, 600, "afk"), afk_event(3300, 300)]
    client, _ = client_for(monkeypatch, windows, afk, now=3600)
    result = poll(client, long_block=True)
    assert len(result) == 1
    assert result[0].duration.total_seconds() == 2700
    assert result[0].data["closed_by"] == "gap"


def test_client_history_and_recency_boundaries(monkeypatch):
    client, _ = client_for(monkeypatch, [window(0, 900), window(900, 2000, "Chat")], now=2900)
    assert poll(client, category_switch=True) == []
    # A partial block at the history boundary cannot be labelled as complete.
    client, _ = client_for(
        monkeypatch, [window(-86400, 87300), window(900, 120, "Chat")], afk=[afk_event(-86400, 87420)]
    )
    assert poll(client, category_switch=True) == []


def test_conflicting_latest_status_waits_independently_of_response_order(monkeypatch):
    windows = [window(0, 900), window(900, 120, "Chat")]
    statuses = [afk_event(1000, 20, "afk"), afk_event(1000, 20)]
    for latest in (statuses, list(reversed(statuses))):
        client, _ = client_for(monkeypatch, windows, [afk_event(0, 1000), *latest])
        assert poll(client, category_switch=True) == []
    # A later unambiguous active sample releases the boundary.
    client, _ = client_for(monkeypatch, windows, [afk_event(0, 1000), *statuses, afk_event(1010, 10)])
    assert len(poll(client, category_switch=True)) == 1
