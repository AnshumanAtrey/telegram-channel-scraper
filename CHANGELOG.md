# Changelog

## 2026-11-13

### Changed
- `postedAfter` reads what hourly monitors, pipelines and AI agents send through the API: spans in hours and
  minutes (`1 hour`, `24h`, `last 30 minutes`, `15m`), `today` and `yesterday`, Unix timestamps in seconds or
  milliseconds, RFC 2822 dates and dates written as `2026/01/31` or `20260131`. The README promised hourly
  monitoring, but `1 hour` failed the run. A second pass the same day added the phrasings agents and monitoring
  tools write: `an hour ago`, `within the last 24 hours`, `since yesterday`, `now-1h`, ISO 8601 durations
  (`PT1H`, `P1D`), month names (`Jan 31, 2026`) and a trailing `UTC`. Anything unclear still fails with a
  sentence saying what to send; `11/12/2026` stays refused because day and month cannot be told apart.
- A run whose every entry can never have a public page (private invite links, numeric chat ids, names Telegram
  would refuse) now ends SUCCEEDED with the reason for each entry in the status message and `OUTPUT`, and nothing
  charged, the same way names Telegram answers for have ended since 2026-10-29.
- Why now: one paying user's weekday runs (100 to 350 a day) failed 834 times in 30 days, which took the
  account's run success from 99% to 79%. Those runs are not shared with the developer, so their inputs and logs
  are unknown; replays on Apify of 60 channels, low memory and spending caps all succeeded. These are the input
  paths that ended a run FAILED, fixed on that evidence.
- `channels` is required (`minItems` 1), so Apify refuses a call without any channel before a run is created.

## 2026-11-11 (example pages)

- Two example pages stay on the Store: the post export and the 7-day monitor. The media links page stays saved but
  unpublished (`"publish": false` in `.actor/tasks.json`): the account can publish 100 example pages across all its
  actors.

## 2026-10-29

### Changed
- A run where Telegram answered for every name (deleted, a group, a person, a bot, no web
  preview) now ends SUCCEEDED with the reasons in the status message and `OUTPUT`, and nothing
  charged. It used to end FAILED, which counted against the actor's reliability: a 284-run batch on
  2026-10-27 ended 13 runs that way. A run still fails when it read nothing and t.me could not be
  reached for a name. A time or spending limit that leaves no room for a channel is the user's
  setting, so that run also succeeds and says why.
- Apify SDK 4 (`apify>=4.1,<5`): SDK 3.x raised on runs started by Apify's AI assistant.
- Store discounts: $0.27 per 1,000 posts on Scale, $0.24 on Business and above; Free and Starter
  stay at $0.30.
- Three example tasks on the Store (`.actor/tasks.json`, published by CI).

## [1.0] - 2026-09-23

First release. Reads public Telegram channels through the web preview (t.me/s/<channel>) with no
login, one dataset row per post.

### Added
- Three inputs: `channels` (names, @names, t.me / telegram.me / telegram.dog links, post links,
  tg://resolve links, Telegram Web links, boost links; quotes and trailing punctuation are cleaned;
  numeric chat ids and private links are refused with a reason), `maxPosts` per channel and `postedAfter` (a date, a date and time, or a span
  like "7 days"). The form fills in 100 posts per channel. Through the API, with neither limit
  set each channel gives its 100 newest posts; with only a date, every post since that date.
- 27 keys on every row, always present: the post, channel details, views, reactions (emoji,
  custom emoji, paid stars), forwards, replies (also across channels), body links, inline buttons
  with row and column, link previews, polls, service messages, and every media item with
  Telegram's own link, size, duration, thumbnail, file name and size, and a stable `fileId`.
- Channels that cannot be read (groups, personal accounts, bots, missing names, private invite
  links, channels with the web preview off, channels Telegram blocks) are skipped with a plain
  reason in the `OUTPUT` summary; blocked channels carry Telegram's own words. The run fails only
  when no channel could be read (narrowed on 2026-10-29, above).
- Posts the web preview shows only as "Please open Telegram to view this post" get their text
  from the post's own page (`textTruncated` when Telegram cut it at about 1,000 characters).
- Pay per event: one `post` event per delivered row at $0.0003, no start fee. A spending limit
  stops the run between pages and keeps every charged row.

### Measured on the platform (build 1.0.2, 2026-09-23, `runs/`)
- Default input, 200 posts: 6.5 s at 256 MB. 2,400 posts from 5 channels: 64 s. 2,000 posts from
  one channel: 142 s at 256 MB, 125 s at 512 MB, 220 s at 128 MB.
- Developer platform cost $0.0048 to $0.0063 per 1,000 posts, $0.005 of it the per-row dataset
  write fee. The progress `OUTPUT` record is written at most every 10 s: rewriting it after every
  page (build 1.0.1) cost more than the compute.
- Works from Apify's servers without a proxy. One channel (@rtnews) is blocked there by Telegram
  ("violated local laws") and is reported as skipped.
