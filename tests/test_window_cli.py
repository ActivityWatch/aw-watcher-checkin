import json
import sys

import pytest

from aw_watcher_ask_away import __main__ as cli

from .test_blocks import client_for, window


@pytest.mark.parametrize("flag", ["--category-switch", "--long-block"])
def test_opt_in_requires_explicit_sources(monkeypatch, flag, capsys):
    monkeypatch.setattr(sys, "argv", ["aw-watcher-ask-away", flag])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "window triggers require" in capsys.readouterr().err


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("response", ["focused", None])
def test_polling_loop_posts_window_block_only_when_enabled(monkeypatch, tmp_path, enabled, response):
    client, transport = client_for(monkeypatch, [window(0, 900), window(900, 120, "Chat")])
    rules = tmp_path / "categories.json"
    rules.write_text(json.dumps([{"name": [name], "rule": {"regex": name}} for name in ("Work", "Chat")]))
    args = ["aw-watcher-ask-away", "--dialog-timeout", "5"]
    if enabled:
        args += ["--category-switch", "--window-bucket", "window", "--categories", str(rules)]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "get_state_retries", lambda _: client)
    prompts = []

    def answer(event, _recent, timeout_seconds):
        prompts.append(event)
        assert timeout_seconds == 300
        return response

    monkeypatch.setattr(cli, "prompt", answer)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(cli, "ActivityWatchClient", lambda **_: Connection())

    polls = 0

    def stop_after_poll(_):
        nonlocal polls
        polls += 1
        if polls == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", stop_after_poll)
    with pytest.raises(KeyboardInterrupt):
        cli.main()
    saved = transport.events[client.bucket_id]
    assert len(saved) == len(prompts) == int(enabled)
    if enabled:
        assert saved[0].data["trigger"] == "category_switch"
        assert saved[0].data["message"] == (response or "")
        assert saved[0].data.get("unlabeled", False) == (response is None)
    else:
        assert not any(bucket == "window" for bucket, _ in transport.requests)
