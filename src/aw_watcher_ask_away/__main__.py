# ruff: noqa: EM101
import argparse
import json
import time
from collections.abc import Iterable
from itertools import chain
from pathlib import Path

import aw_core
from aw_client.client import ActivityWatchClient
from aw_core.log import setup_logging
from aw_transform.classify import Rule
from requests.exceptions import ConnectionError

from aw_watcher_ask_away.blocks import BlockTriggers
from aw_watcher_ask_away.core import (
    DATA_KEY,
    LOCAL_TIMEZONE,
    WATCHER_NAME,
    AWAskAwayClient,
    AWWatcherAskAwayError,
    logger,
)


def prompt(event: aw_core.Event, recent_events: Iterable[aw_core.Event], timeout_seconds: float = 0.0):
    # The dialog creates a Tk root; CLI validation and --help need no display.
    import aw_watcher_ask_away.dialog as aw_dialog  # noqa: PLC0415

    # TODO: Allow for customizing the prompt from the prompt interface.
    start_time_str = event.timestamp.astimezone(LOCAL_TIMEZONE).strftime("%I:%M")
    end_time_str = (event.timestamp + event.duration).astimezone(LOCAL_TIMEZONE).strftime("%I:%M")
    prompt = f"What were you doing from {start_time_str} - {end_time_str} ({event.duration.seconds / 60:.1f} minutes)?"
    title = "Block Checkin" if event.data.get("trigger") else "AFK Checkin"

    return aw_dialog.ask_string(
        title, prompt, [event.data[DATA_KEY] for event in recent_events], timeout_seconds=timeout_seconds
    )


def get_state_retries(client: ActivityWatchClient):
    """When the computer is starting up sometimes the aw-server is not ready for requests yet.

    So we sit and retry for a while before giving up.
    """
    for _ in range(10):
        try:
            # This works because the constructor of AWAskAwayState tries to get bucket names.
            # If it didn't we'd need to do something else here.
            return AWAskAwayClient(client)
        except ConnectionError:
            logger.exception("Cannot connect to client.")
            time.sleep(10)  # 10 * 10 = wait for 100s before giving up.
    raise AWWatcherAskAwayError("Could not get a connection to the server.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--depth", type=float, default=10, help="The number of minutes to look into the past for events."
    )
    parser.add_argument(
        "--frequency", type=float, default=5, help="The number of seconds to wait before checking for AFK events again."
    )
    parser.add_argument(
        "--length", type=float, default=5, help="The number of minutes you need to be away before reporting on it."
    )
    parser.add_argument(
        "--dialog-timeout",
        type=float,
        default=0,
        help=(
            "Auto-dismiss an unanswered check-in dialog after this many minutes, recording the "
            "boundary as unlabeled. 0 disables the timeout; the dialog then waits forever, which "
            "blocks the watcher's polling loop — avoid on unattended/agent desktops."
        ),
    )
    parser.add_argument("--testing", action="store_true", help="Run in testing mode.")
    parser.add_argument("--verbose", action="store_true", help="I want to see EVERYTHING!")
    parser.add_argument(
        "--category-switch", action="store_true", help="Experimental: prompt after a sustained category switch."
    )
    parser.add_argument(
        "--long-block", action="store_true", help="Experimental: prompt when a long window block closes."
    )
    parser.add_argument("--window-bucket", help="Window bucket on the same device as the AFK bucket.")
    parser.add_argument("--categories", type=Path, help="JSON array of ActivityWatch category definitions (name/rule).")
    args = parser.parse_args()
    triggers = BlockTriggers(category_switch=args.category_switch, long_block=args.long_block)
    categories = []
    if triggers.category_switch or triggers.long_block:
        if not args.window_bucket or not args.categories:
            parser.error("window triggers require --window-bucket and --categories")
        with args.categories.open() as category_file:
            categories = [
                (c["name"], Rule({**c["rule"], "select_keys": ["app", "title"]})) for c in json.load(category_file)
            ]
        if not categories:
            parser.error("--categories must contain at least one category")

    # Set up logging
    setup_logging(
        WATCHER_NAME,
        testing=args.testing,
        verbose=args.verbose,
        log_stderr=True,
        log_file=True,
    )

    try:
        client = ActivityWatchClient(  # pyright: ignore[reportPrivateImportUsage]
            client_name=WATCHER_NAME, testing=args.testing
        )
        with client:
            state = get_state_retries(client)
            logger.info("Successfully connected to the server.")

            while True:
                for event in chain(
                    state.get_new_afk_events_to_note(seconds=args.depth * 60, durration_thresh=args.length * 60),
                    state.get_new_window_events_to_note(args.window_bucket, categories, triggers, args.depth * 60),
                ):
                    response = prompt(event, state.state.recent_events, timeout_seconds=args.dialog_timeout * 60)
                    if response:
                        logger.info(response)
                        state.post_event(event, response)
                    elif args.dialog_timeout > 0:
                        # Nobody answered (or the user dismissed). Keep the boundary as
                        # unlabeled so prompt compliance is measurable, and so the same
                        # gap is not asked about again.
                        logger.info("Check-in dialog went unanswered; recorded boundary as unlabeled.")
                        state.post_event(event, "", unlabeled=True)
                time.sleep(args.frequency)
    except Exception as e:
        # A modal error box blocks forever when nobody is at the display, so the
        # service never exits and never restarts. In unattended mode log instead.
        if args.dialog_timeout > 0:
            logger.exception("Unhandled exception; exiting so the service can restart.")
        else:
            from tkinter import messagebox  # noqa: PLC0415

            messagebox.showerror("AW Watcher Ask Away: Error", f"An unhandled exception occurred: {e}")
        raise


if __name__ == "__main__":
    main()
