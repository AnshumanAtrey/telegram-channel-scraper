# Changelog

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
