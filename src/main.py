"""
Telegram Channel Scraper - reads public Telegram channels through t.me/s/<channel>, no login.

What the web preview does, and how this actor handles it (recon 2026-09-23 over 60+
channels; the paging and retry traps are in telegram.py, the markup traps in parse.py):

- Only public channels have a web feed. Groups, people, bots, missing names and blocked
  channels all answer with an ordinary page, so each one is recognised and reported
  with a plain reason in OUTPUT instead of an empty, unexplained result.
- Five channels are read at once. Pages inside a channel go one after another, newest
  first, because each page's link to older posts comes from the page before it.
- One dataset row per post, charged as one "post" event, pushed page by page. A
  spending limit or a near time limit stops the run between pages and keeps every row
  already delivered; the count of delivered rows comes from the charge result, because
  the SDK silently drops rows it cannot charge.
- No summary row in the dataset, so CSV and Excel exports hold posts only. The run
  summary is the OUTPUT record, refreshed about every 10 s and final at the end.
- A status message that can never fail the run. SDK 3.x validates the run object
  against an enum that lacks the new APIFY_AI run origin; the message is stored
  server-side before that parse, so a parse error is logged, not raised.
"""
import asyncio
import time
from contextlib import aclosing
from datetime import datetime, timezone

from apify import Actor

from .inputs import parse_channels, parse_posted_after, resolve_limits
from .telegram import WalkStats, check_channel, new_client, walk_channel

POST_EVENT = 'post'           # pay-per-event name for one post row; must equal the event key in .actor/store.json
CONCURRENT_CHANNELS = 5       # channels read at once; pages inside a channel stay sequential
RUN_SAFETY_MARGIN_S = 60      # leave time to write the summary before the platform kills us
                              # (a quarter of the run instead, for runs shorter than 4 minutes)
STATUS_EVERY_S = 5            # status line at most this often
OUTPUT_EVERY_S = 10           # progress OUTPUT at most this often: every key-value write is billed, and on the
                              # platform the per-page refresh cost more than the compute (build 1.0.1 prefill:
                              # 15 writes $0.00075 vs compute $0.00011); the final summary is always written
PUBLIC_KEYS = ('input', 'channel', 'channelId', 'status', 'reason', 'posts', 'newestDate', 'oldestDate')

STOP_REASONS = {
    'limit': 'your spending limit for this run was reached',
    'time': 'the run was about to reach its time limit',
}
STOP_ADVICE = {
    'limit': 'Raise the limit to get the rest.',
    'time': 'Raise the run timeout, or ask for fewer posts.',
}


async def safe_status(message: str) -> None:
    """Set the run's status message without ever failing the run.

    The message is stored by the API before the SDK parses the response; SDK 3.x
    then validates the run object against an enum missing the APIFY_AI origin and
    raises. That raise used to turn 8 finished runs a week into FAILED.
    """
    try:
        await Actor.set_status_message(message)
    except Exception as exc:  # noqa: BLE001
        Actor.log.debug(f'status message stored but not confirmed by the SDK: {exc}')


def seconds_left_in_run() -> float | None:
    """Seconds until the platform kills this run, or None when not on the platform."""
    config = getattr(Actor, 'configuration', None) or getattr(Actor, 'config', None)
    timeout_at = getattr(config, 'timeout_at', None) if config else None
    if not timeout_at:
        return None
    now = datetime.now(timezone.utc)
    return (timeout_at - now).total_seconds()


# ----------------------------------------------------------------------- summary --
def when(dt: datetime) -> str:
    """2026-01-31 18:00 UTC, with seconds only when the cutoff has them."""
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC' if dt.second else '%Y-%m-%d %H:%M UTC')


def describe_scope(cap: int | None, cutoff: datetime | None, cap_was_default: bool) -> str:
    """What the run was asked for, in words: shown in the log and the final message."""
    if cap and cutoff:
        return f'up to {cap:,} posts per channel, none older than {when(cutoff)}'
    if cutoff:
        return f'every post since {when(cutoff)}'
    if cap_was_default:
        return f'the newest {cap:,} posts per channel, the default when no limit and no date are set'
    return f'the newest {cap:,} posts per channel'


def parse_date(text: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def record_delivery(entry: dict, rows: list[dict]) -> None:
    """Count delivered rows on a channel's entry and widen its newest/oldest date span."""
    entry['posts'] += len(rows)
    for row in rows:
        dt = parse_date(row.get('date'))
        if not dt:
            continue
        if not entry['newestDate'] or dt > parse_date(entry['newestDate']):
            entry['newestDate'] = row['date']
        if not entry['oldestDate'] or dt < parse_date(entry['oldestDate']):
            entry['oldestDate'] = row['date']


def percent_done(channels: list[dict], cap: int | None, cutoff: datetime | None, now: datetime) -> int:
    """Rough progress: finished channels count fully, a running one by posts/cap or by how
    far back in time it has got towards the cutoff, whichever is further along."""
    if not channels:
        return 0
    total = 0.0
    for e in channels:
        if e['status'] in ('ok', 'partial', 'skipped'):
            total += 1
        elif e['status'] == 'running':
            part = e['posts'] / cap if cap else 0.0
            oldest = parse_date(e['oldestDate'])
            if cutoff and oldest and now > cutoff:
                part = max(part, (now - oldest) / (now - cutoff))
            total += min(part, 0.99)
    return min(99, int(100 * total / len(channels)))


def progress_line(channels: list[dict], pct: int) -> str:
    """One sentence for the run's status message while the run is going."""
    done = sum(e['status'] in ('ok', 'partial', 'skipped') for e in channels)
    posts = sum(e['posts'] for e in channels)
    running = [f'@{e["channel"]}' for e in channels if e['status'] == 'running' and e['channel']]
    now = f', now reading {", ".join(running)}' if running else ''
    return f'{pct}% done: {posts:,} posts so far, {done} of {len(channels)} channels finished{now}.'


def public(entry: dict) -> dict:
    return {k: entry[k] for k in PUBLIC_KEYS}


def final_message(channels: list[dict], scope: str, stop: str | None) -> str:
    """One plain paragraph: what was delivered, then every channel that was cut short or skipped."""
    read = [e for e in channels if e['status'] in ('ok', 'partial')]
    total = sum(e['posts'] for e in channels)
    if read:
        head = f'Got {total:,} posts from {len(read)} of {len(channels)} channels: {scope}.'
    else:
        head = f'No posts: none of the {len(channels)} channels could be read.'
    parts = [head]
    if stop:
        parts.append(f'The run stopped early because {STOP_REASONS[stop]}. Every post counted here was delivered.')
    for e in channels:
        name = f'@{e["channel"]}' if e['channel'] else f'"{e["input"]}"'
        if e['status'] == 'skipped':
            parts.append(f'Skipped: {e["reason"]}')
        elif e['reason']:
            parts.append(f'{name}: {e["reason"]}')
    return ' '.join(parts)


def ok_note(stats: WalkStats, posts: int, cap: int | None, cutoff: datetime | None) -> str | None:
    """A sentence for a channel that finished normally but with fewer posts than asked for."""
    if stats.stopped_by == 'cutoff' and posts == 0:
        return f'No posts on or after {when(cutoff)}.'
    if stats.stopped_by == 'no_progress':
        return f'Telegram stopped paging further back after {posts:,} posts; treated that as the first post.'
    if stats.stopped_by == 'start' and posts == 0:
        return 'The web preview shows no posts for this channel.'
    if stats.stopped_by == 'start' and cap and posts < cap:
        return f'Reached the oldest post the web preview shows, after {posts:,} posts.'
    return None


# -------------------------------------------------------------------------- main --
async def main() -> None:
    async with Actor:
        Actor.log.info('Telegram Channel Scraper starting')
        inp = await Actor.get_input() or {}

        entries = parse_channels(inp.get('channels'))
        refused = [e for e in entries if not e['handle']]
        for e in refused:
            Actor.log.warning(f'Skipped: {e["reason"]}')
        if not any(e['handle'] for e in entries):
            hint = refused[0]['reason'] if refused else 'No channel given.'
            await Actor.fail(status_message=(
                f'{hint} Enter a public Telegram channel, for example durov. You can paste @durov or a '
                f't.me link, one channel per line.'))
            return

        started = datetime.now(timezone.utc)
        try:
            cutoff = parse_posted_after(inp.get('postedAfter'), started)
            cap = resolve_limits(inp.get('maxPosts'), cutoff)
        except ValueError as exc:
            await Actor.fail(status_message=str(exc))
            return
        cap_was_default = cap is not None and inp.get('maxPosts') in (None, '', 0)
        scope = describe_scope(cap, cutoff, cap_was_default)

        pricing = Actor.get_charging_manager().get_pricing_info()
        Actor.log.info(f'Pricing seen by the run: model={pricing.pricing_model or "none"} '
                       f'payPerEvent={pricing.is_pay_per_event} '
                       f'events={",".join(pricing.per_event_prices) or "-"} '
                       f'maxTotalChargeUsd={pricing.max_total_charge_usd}')
        Actor.log.info(f'{sum(1 for e in entries if e["handle"])} channel(s) to read: {scope}.')

        channels = [{'input': e['input'], 'channel': None, 'channelId': None,
                     'status': 'waiting' if e['handle'] else 'skipped', 'reason': e['reason'],
                     'posts': 0, 'newestDate': None, 'oldestDate': None} for e in entries]
        run = {'stop': None, 'last_status': 0.0, 'last_output': 0.0}
        claimed: dict[int, str] = {}           # channel id -> the input that already read it
        progress_lock = asyncio.Lock()
        slots = asyncio.Semaphore(CONCURRENT_CHANNELS)

        async def report() -> None:
            """Refresh the OUTPUT progress record and the status line, each at most every few seconds. Best effort."""
            async with progress_lock:
                pct = percent_done(channels, cap, cutoff, datetime.now(timezone.utc))
                line = progress_line(channels, pct)
                if time.monotonic() - run['last_output'] >= OUTPUT_EVERY_S:
                    run['last_output'] = time.monotonic()
                    try:
                        await Actor.set_value('OUTPUT', {'status': 'running', 'percentDone': pct, 'message': line,
                                                         'channels': [public(e) for e in channels]})
                    except Exception as exc:  # noqa: BLE001
                        Actor.log.warning(f'Could not refresh the OUTPUT progress record: {exc}')
                if time.monotonic() - run['last_status'] >= STATUS_EVERY_S:
                    run['last_status'] = time.monotonic()
                    await safe_status(line)

        left_at_start = seconds_left_in_run()
        margin = RUN_SAFETY_MARGIN_S if left_at_start is None else min(RUN_SAFETY_MARGIN_S, left_at_start / 4)

        def check_time() -> None:
            left = seconds_left_in_run()
            if left is not None and left < margin and not run['stop']:
                run['stop'] = 'time'

        async def read_channel(entry: dict, handle: str, client) -> None:
            status, reason, page = await check_channel(client, handle)
            if status != 'ok':
                entry.update(status='skipped', reason=reason)
                Actor.log.warning(f'Skipped: {reason}')
                return
            ch = page.channel
            entry.update(channel=ch.handle or handle, channelId=ch.id)
            if ch.id is not None:
                # Several names can lead to one channel (stickerpacks and stickerpack): read it once.
                if ch.id in claimed:
                    entry.update(status='skipped', reason=(
                        f'@{handle} is the same channel as "{claimed[ch.id]}", which this run already reads.'))
                    Actor.log.warning(f'Skipped: {entry["reason"]}')
                    return
                claimed[ch.id] = entry['input']
            subs = f'{ch.subscribers:,} subscribers' if ch.subscribers is not None else 'subscriber count hidden'
            Actor.log.info(f'@{entry["channel"]} ({ch.title or "untitled"}, {subs}): reading posts.')

            stats = WalkStats()
            cut_short = None                   # 'limit' or 'time' when this channel was stopped from outside
            async with aclosing(walk_channel(client, handle, page, cutoff=cutoff, cap=cap, stats=stats)) as pages:
                async for rows in pages:
                    if run['stop']:            # another channel hit the limit while this page loaded
                        cut_short = run['stop']
                        break
                    res = await Actor.push_data(rows, charged_event_name=POST_EVENT)
                    # The SDK pushes only the rows it could charge, so count what it charged.
                    delivered = res.charged_count if pricing.is_pay_per_event else len(rows)
                    record_delivery(entry, rows[:delivered])
                    if res.event_charge_limit_reached:
                        run['stop'] = 'limit'
                    check_time()
                    Actor.log.info(f'@{entry["channel"]}: {entry["posts"]:,} posts so far, back to '
                                   f'{entry["oldestDate"] or "an undated post"}.')
                    await report()
                    if run['stop']:
                        # A channel that just got its last wanted post is complete, not cut short.
                        if delivered < len(rows) or not (cap and entry['posts'] >= cap):
                            cut_short = run['stop']
                        break

            if cut_short:
                entry.update(status='partial', reason=(f'Stopped after {entry["posts"]:,} posts because '
                                                       f'{STOP_REASONS[cut_short]}. {STOP_ADVICE[cut_short]}'))
            elif stats.error:
                entry.update(status='partial', reason=(
                    f'Stopped after {entry["posts"]:,} posts because {stats.error}. Those posts are saved; '
                    f'run it again to get the rest.'))
            else:
                entry.update(status='ok', reason=ok_note(stats, entry['posts'], cap, cutoff))
            Actor.log.info(f'@{entry["channel"]}: finished, {entry["posts"]:,} posts, status {entry["status"]}.'
                           + (f' {entry["reason"]}' if entry['reason'] else ''))

        async def run_channel(entry: dict, handle: str, client) -> None:
            async with slots:
                check_time()
                stop = run['stop']
                if stop:
                    entry.update(status='skipped', reason=(f'@{handle} was not read because {STOP_REASONS[stop]} '
                                                           f'before its turn. {STOP_ADVICE[stop]}'))
                    return
                entry['status'] = 'running'
                try:
                    await read_channel(entry, handle, client)
                except Exception as exc:  # noqa: BLE001  one channel must never crash the run
                    Actor.log.exception(f'@{handle}: unexpected error')
                    what = (f'Stopped after {entry["posts"]:,} posts' if entry['posts']
                            else f'@{handle} could not be read')
                    entry.update(status='partial' if entry['posts'] else 'skipped', reason=(
                        f'{what} because of an unexpected error ({exc}). This is on us, not you: please report it.'))
                await report()

        async with new_client() as client:
            await asyncio.gather(*(run_channel(c, e['handle'], client)
                                   for c, e in zip(channels, entries, strict=True) if e['handle']))

        # ---- summary: the final OUTPUT record (no summary row in the dataset)
        message = final_message(channels, scope, run['stop'])
        await Actor.set_value('OUTPUT', {'status': 'done', 'totalPosts': sum(e['posts'] for e in channels),
                                         'channels': [public(e) for e in channels], 'message': message})
        await safe_status(message[:500])
        Actor.log.info(message)

        if not any(e['status'] in ('ok', 'partial') for e in channels):
            await Actor.fail(status_message=message[:500])


if __name__ == '__main__':
    asyncio.run(main())
