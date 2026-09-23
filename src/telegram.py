"""
Talking to t.me: one shared HTTP client, retries, the channel check and the backward walk.

What real requests showed (recon 2026-09-23, about 430 requests from one residential IP)
and how this module handles it:

- The flaky part is opening connections, not throttling. Fresh connections were reset
  (Errno 54) or stalled for 31 s; one pooled keep-alive client with a 10 s connect
  timeout plus retries fixed both. No 429 was ever seen, but Retry-After is honoured
  if Telegram starts sending it.
- Every kind of failure (group, person, bot, missing, blocked) ends on an HTTP 200
  page, so the status code says nothing. /s/<name> answering 200 with the channel
  header is the only proof of a readable channel; everything else is classified from
  the markup of t.me/<name>.
- before=0, before=-5 and before=abc silently return the NEWEST page. A loop that ever
  sends one starts over from the top forever. The cursor is taken only from Telegram's
  own "load more" link, never computed, and must go down on every page.
- A page holds 20 message ids, not 20 posts: an album is one post over several ids and
  deleted posts leave gaps. So a short page, or a page of only service messages, is not
  the end. Only a missing cursor is.
- About 4 in 1,700 posts render as "Please open Telegram to view this post" with no
  text. The text is in the single-post page's og:description, cut at about 1,000
  characters with a trailing "…". For posts with no text that tag falls back to the
  channel description, which must not be passed off as the post's text.
"""
import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from apify import Actor

from .parse import Page, classify_landing, parse_embed_error, parse_og_description, parse_page

BASE_URL = 'https://t.me'
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/128.0.0.0 Safari/537.36')   # t.me serves the same page to any UA; a browser one is least odd
CONNECT_TIMEOUT_S = 10        # a stalled TLS handshake (seen: 31 s) is cheaper to retry than to wait out
READ_TIMEOUT_S = 30
MAX_CONNECTIONS = 10          # 5 channels at a time, one page each, plus the odd single-post request
MAX_ATTEMPTS = 5              # per request; delays 1, 2, 4, 8 s plus jitter between them
BACKOFF_BASE_S = 1.0
RETRY_AFTER_CAP_S = 60        # honour Retry-After, but never park a channel for longer than this
PAGE_PAUSE_S = 0.2            # between requests of one channel; 5 channels stay near 5 requests a second
ELLIPSIS = '…'                # how og:description marks text it cut

# Test seam: tests swap this for a recorder so backoff never really waits.
sleep = asyncio.sleep

RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError)


class FetchError(Exception):
    """A request that still failed after every retry. The message is a plain sentence."""


def new_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The one client every request of the run goes through (keep-alive pool).

    Redirects are never followed: a 302 from /s/<name> is the signal that the channel
    has no public feed, and a 302 from t.me/<name> to telegram.org means an invalid name.
    """
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS),
        headers={'User-Agent': USER_AGENT, 'Accept-Language': 'en-US,en;q=0.9',
                 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
        follow_redirects=False,
    )


def retry_after_seconds(value: str | None) -> float | None:
    """Retry-After as seconds (it may be a number or an HTTP date), capped; None if absent or junk."""
    if not value:
        return None
    try:
        secs = float(value)
    except ValueError:
        try:
            secs = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            return None
    return max(0.0, min(secs, RETRY_AFTER_CAP_S))


def backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter: about 1, 2, 4, 8 s after attempts 1 to 4."""
    return BACKOFF_BASE_S * 2 ** (attempt - 1) + random.uniform(0, BACKOFF_BASE_S)


async def fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET with retries on connection errors, timeouts, protocol errors, 5xx and 429.

    Any other answer (200, 302, 404) is returned as is; the caller decides what it means.
    Raises FetchError once MAX_ATTEMPTS tries have all failed.
    """
    last = ''
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = None
        try:
            resp = await client.get(url)
        except RETRYABLE_ERRORS as exc:
            last = f'{type(exc).__name__}: {exc}' if str(exc) else type(exc).__name__
        else:
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            last = f'HTTP {resp.status_code}'
            wait = retry_after_seconds(resp.headers.get('Retry-After'))
        if attempt == MAX_ATTEMPTS:
            break
        delay = wait if wait is not None else backoff_seconds(attempt)
        Actor.log.warning(f'{url}: {last}. Trying again in {delay:.1f} s ({attempt + 1} of {MAX_ATTEMPTS}).')
        await sleep(delay)
    raise FetchError(f't.me did not answer after {MAX_ATTEMPTS} tries (last error: {last})')


# -------------------------------------------------------------------------- check --
def skip_reason(status: str, handle: str) -> str:
    """Plain sentence for every way a name can fail to be a readable channel."""
    at = f'@{handle}'
    return {
        'is_group': f'{at} is a group, not a channel. Telegram only shows channels publicly.',
        'is_user': f"{at} is a person's account, not a channel.",
        'is_bot': f'{at} is a bot, not a channel.',
        'not_found': f'{at} does not exist. Check the spelling; a deleted channel looks the same.',
        'restricted': (f'{at} has turned off its public web preview, so it can only be read inside the '
                       f'Telegram app.'),
        'banned': f'{at} is blocked by Telegram and has no public web preview.',
        'private_link': f'{at} leads to a private invite, not a public channel.',
        'invalid': f'Telegram does not accept "{handle}" as a name.',
    }.get(status, f'Telegram did not show a public feed for {at}, and its page did not say why.')


async def check_channel(client: httpx.AsyncClient, handle: str) -> tuple[str, str | None, Page | None]:
    """Is this a public channel with a web feed? -> (status, reason, newest page).

    status is 'ok' (reason None, page set) or why not: 'is_group', 'is_user', 'is_bot',
    'not_found', 'restricted', 'banned', 'private_link', 'invalid', 'unknown', or
    'error' when t.me could not be reached. Costs 1 request for a channel, 2 for anything
    else, 3 for a blocked channel (to pass on Telegram's own reason).
    """
    try:
        resp = await fetch(client, f'{BASE_URL}/s/{handle}')
        if resp.status_code == 200:
            page = parse_page(resp.text)
            if page.has_channel_info:
                return 'ok', None, page
        elif not resp.is_redirect:
            return 'error', f'Could not read @{handle}: t.me answered HTTP {resp.status_code}.', None

        landing = await fetch(client, f'{BASE_URL}/{handle}')
        if landing.is_redirect:
            # t.me sends names it rejects on to telegram.org
            return 'invalid', skip_reason('invalid', handle), None
        if landing.status_code != 200:
            return 'error', f'Could not read @{handle}: t.me answered HTTP {landing.status_code}.', None
        status = classify_landing(landing.text)
        if status == 'ok':
            # The landing page never carries the feed header; if it does, /s/ still refused us.
            status = 'unknown'
        reason = skip_reason(status, handle)
        if status in ('restricted', 'banned'):
            said = await telegram_reason(client, handle)
            if said:
                reason = f'@{handle} has no public web preview. Telegram says: "{said}"'
        return status, reason, None
    except FetchError as exc:
        return 'error', f'Could not reach t.me for @{handle}. {exc}.', None


async def telegram_reason(client: httpx.AsyncClient, handle: str) -> str | None:
    """Telegram's own sentence for a blocked channel, from the embed widget of post 1."""
    try:
        resp = await fetch(client, f'{BASE_URL}/{handle}/1?embed=1&mode=tme')
    except FetchError:
        return None
    return parse_embed_error(resp.text) if resp.status_code == 200 else None


# --------------------------------------------------------------------------- walk --
@dataclass
class WalkStats:
    """How a walk went; filled in while walk_channel runs, final once it stops."""
    pages: int = 0                # feed pages read, the newest page included
    posts: int = 0                # rows yielded
    stopped_by: str = ''          # 'start' (oldest post reached), 'cap', 'cutoff', 'no_progress', 'error'
    error: str | None = None      # plain sentence when a page kept failing


def older_than(date_text: str | None, cutoff: datetime) -> bool:
    """True when the post's ISO date is before the cutoff. A post with no date is kept."""
    if not date_text:
        return False
    try:
        dt = datetime.fromisoformat(date_text)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < cutoff


def same_text(a: str | None, b: str | None) -> bool:
    """Equal once whitespace is collapsed (og tags and the header lay out line breaks differently)."""
    return ' '.join((a or '').split()) == ' '.join((b or '').split())


async def fill_orphan(client: httpx.AsyncClient, row: dict, channel_description: str | None, pause: float) -> None:
    """Recover the text of a post t.me/s only shows as "Please open Telegram to view this post"."""
    await sleep(pause)
    try:
        resp = await fetch(client, row['postUrl'])
    except FetchError as exc:
        Actor.log.warning(f'{row["postUrl"]}: could not load the text of a post the web preview hides ({exc}). '
                          f'Kept the post without text.')
        return
    text = parse_og_description(resp.text) if resp.status_code == 200 else None
    if not text or not text.strip():
        return
    if channel_description and same_text(text, channel_description):
        return   # Telegram's stand-in for a post with no text, not the post's own words
    row['text'] = text
    row['textTruncated'] = text.rstrip().endswith(ELLIPSIS)


async def walk_channel(client: httpx.AsyncClient, handle: str, first_page: Page, *,
                       cutoff: datetime | None = None, cap: int | None = None,
                       stats: WalkStats | None = None, pause: float = PAGE_PAUSE_S):
    """Yield the channel's posts one page at a time, newest first, until a stop rule fires.

    Each yielded list is one page's new rows ordered newest to oldest, and pages go back
    in time, so the whole stream runs newest to oldest. An album counts as one post.
    Stops, in this order of checks, when:
      - `cap` posts have been yielded (the last page is cut to fit exactly);
      - a post older than `cutoff` shows up (it and everything older is left out);
      - the page has no older-posts cursor, or the cursor is 1 or less (channel start);
      - the cursor did not go down (Telegram served a page we already had);
      - a page still fails after every retry (stats.error says why; rows so far stand).
    Posts are de-duplicated by id. `stats` is filled in as the walk goes.
    """
    stats = stats if stats is not None else WalkStats()
    seen: set[int] = set()
    page = first_page
    description = first_page.channel.description
    sent: int | None = None                      # the last before= value we asked for
    while True:
        stats.pages += 1
        rows: list[dict] = []
        hit_cutoff = False
        orphans = set(page.orphans)
        for row in reversed(page.posts):         # the page is oldest-first; walk it newest-first
            post_id = row['postId']
            if post_id in seen:
                continue
            seen.add(post_id)
            if cutoff and older_than(row['date'], cutoff):
                hit_cutoff = True
                break
            if post_id in orphans:
                await fill_orphan(client, row, description, pause)
            rows.append(row)
            if cap is not None and stats.posts + len(rows) >= cap:
                break
        if rows:
            stats.posts += len(rows)
            yield rows

        if cap is not None and stats.posts >= cap:
            stats.stopped_by = 'cap'
            return
        if hit_cutoff:
            stats.stopped_by = 'cutoff'
            return
        cursor = page.before
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor <= 1:
            stats.stopped_by = 'start'
            return
        if sent is not None and cursor >= sent:
            Actor.log.warning(f'@{handle}: Telegram answered before={sent} with a page that does not go further '
                              f'back (next cursor {cursor}). Treating it as the start of the channel.')
            stats.stopped_by = 'no_progress'
            return

        await sleep(pause)
        try:
            resp = await fetch(client, f'{BASE_URL}/s/{handle}?before={cursor}')
        except FetchError as exc:
            stats.stopped_by, stats.error = 'error', f'{exc}, while reading posts older than #{cursor}'
            return
        sent = cursor
        if resp.status_code != 200:
            stats.stopped_by = 'error'
            stats.error = f't.me answered HTTP {resp.status_code} for posts older than #{cursor}'
            return
        page = parse_page(resp.text)
        if not page.has_channel_info:
            stats.stopped_by = 'error'
            stats.error = f'Telegram stopped showing the channel at posts older than #{cursor}'
            return
