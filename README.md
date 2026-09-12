# AW Watcher Ask Away

> **Adopted into the ActivityWatch organization (2026-09-08).**
> This watcher was created and developed by [Jeremiah England](https://github.com/Jeremiah-England)
> at [Jeremiah-England/aw-watcher-ask-away](https://github.com/Jeremiah-England/aw-watcher-ask-away)
> and published to [PyPI](https://pypi.org/project/aw-watcher-ask-away/) (v0.0.6).
> The full commit history is preserved here. It is being extended as **`aw-watcher-checkin`**
> — boundary-triggered check-ins (return from AFK, sustained context switch, long block)
> with short rating prompts — while keeping its AFK-return core and event shape.
> See *Origin and attribution* at the bottom. Thank you, Jeremiah.


[![PyPI - Version](https://img.shields.io/pypi/v/aw-watcher-ask-away.svg)](https://pypi.org/project/aw-watcher-ask-away)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/aw-watcher-ask-away.svg)](https://pypi.org/project/aw-watcher-ask-away)

---

This [ActivityWatch](https://activitywatch.net) "watcher" asks you what you were doing in a pop-up dialogue when you get back to your computer from an AFK (away from keyboard) break.

## Installation

```console
pipx install aw-watcher-ask-away
```

([Need to install `pipx` first?](https://pypa.github.io/pipx/installation/))

## Experimental window boundaries

The AFK-return watcher is unchanged by default. Two independent opt-in flags add
boundaries from a window bucket on the **same device** as the AFK bucket:

- `--category-switch`: the previous block lasted at least 15 minutes and the new
  category has held for at least 2 minutes. Short detours stay in the old block.
- `--long-block`: a block of at least 45 minutes closes at a sustained category
  switch or a gap of at least 5 minutes. A gap is confirmed on return to activity.

These are development flags. Daily caps, cooldowns, quiet hours and the rating
form are still pending; keep the flags off for unattended use until those and
[the dialog timeout](https://github.com/ActivityWatch/aw-watcher-checkin/pull/4) land.

Both flags require `--window-bucket ID` and `--categories FILE`. The file is a
JSON array in ActivityWatch's category format, for example:

```json
[
  {"name": ["Work", "Coding"], "rule": {"regex": "vim|Code", "ignore_case": true}},
  {"name": ["Communication"], "rule": {"regex": "Slack|Signal", "ignore_case": true}}
]
```

Use your own category definitions. Classification uses ActivityWatch's existing
engine over `app` and `title`; app changes alone are not category switches.
Unmatched events belong to `Uncategorized`. Rules are loaded at startup.

The finder intersects windows with non-AFK time and reads up to 24 hours of
history, independently of `--depth` (how recently a boundary was confirmed).
Incomplete blocks crossing that history boundary are skipped. It waits while
AFK or when AFK telemetry is more than 60 seconds stale. The end of a polling
query never closes a block. Adjacent same-category snapshots tolerate up to one
second of heartbeat jitter; overlapping snapshots do not add duration twice.

New events retain the block's timestamp/duration, clear the source ID and use
`message` as before. They add `trigger`, `category` (a category path), and
`closed_by` (`switch` or `gap`). If both rules match, `category_switch` wins and
only one event is emitted. Persisted events use the original overlap dedupe on
later polls and after restart. AFK-return events still describe the away interval,
whereas window events describe the preceding active block.

## Roadmap

Most of the improvements involve a more complicated pop-up window.

- Use `pyinstaller` or something for distribution to people who are not developers and know how to install things from PyPI.
  - Set up a website, probably with a GitHub organization.
- Handle calls better/stop asking what you were doing every couple minutes when in a call.
- See whether people would rather add data to AFK events instead of creating a separate bucket. Maybe make that an option/configurable.

## Contributing

Here are some helpful links:

- [How to create an ActivityWatch watcher](https://docs.activitywatch.net/en/latest/examples/writing-watchers.html).
- ["Manually tracking away/offline-time" forum discussion](https://forum.activitywatch.net/t/manually-tracking-away-offline-time/284)

Note: I am using this project to get experience with the `hatch` project manager.
I have never use it before and I'm probably doing some things wrong there.

## License

`aw-watcher-ask-away` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

## Origin and attribution

- **Original author:** Jeremiah England — https://github.com/Jeremiah-England/aw-watcher-ask-away
- **Original package:** https://pypi.org/project/aw-watcher-ask-away/ (0.0.2 – 0.0.6, 2023)
- **License:** MIT, © 2023 Jeremiah England — retained verbatim in `LICENSE`.
- **Adopted:** 2026-09-08 into the ActivityWatch organization, with full git history.
- **Why this one:** its `core.py` already handles the hard parts of AFK-return prompting —
  gap detection over squashed not-AFK events (so suspend/power-off count as away),
  overlap-ratio de-duplication against aw-server timestamp jitter, zero-length event
  filtering, and writing the rating as the block itself. Extending it beats rewriting it.
- **Related:** [bcbernardo/aw-watcher-ask](https://github.com/bcbernardo/aw-watcher-ask)
  — schedule-triggered ESM prompts via Zenity; a sibling approach to the same idea.
