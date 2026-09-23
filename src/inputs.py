"""
Input cleaning for the three form fields: channels, maxPosts, postedAfter.

Pure functions, no network. What they handle comes from live requests to t.me
(recon 2026-09-23), not from guesses:

- People paste every shape of Telegram link: @name, t.me/name, t.me/s/name, a post
  link t.me/name/123, telegram.me and telegram.dog mirrors, name.t.me, and the
  app-only tg://resolve?domain=name. All of them become the bare channel name.
- Telegram's own name rule is 4 to 32 characters, a letter first, no trailing
  underscore and no double underscore. The folk rule of "5 or more" is wrong: the
  4-letter channel "gram" has 6.6M subscribers and a working web preview.
- Links that can never have a public feed (private invites, folder links, private
  channel post links, Telegram's own routes like t.me/share) are refused up front
  with a sentence saying why, so they cost no request and no charge.
- The date box sends either YYYY-MM-DD or "{n} {unit}" ("3 weeks"). API callers can
  send anything, so a full ISO datetime is accepted too, and anything else is a
  clear error rather than a silently ignored filter.
"""
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

HANDLE_RE = re.compile(r'^(?=.{4,32}$)[A-Za-z](?!.*__)[A-Za-z0-9_]*[A-Za-z0-9]$')
TELEGRAM_HOSTS = {'t.me', 'telegram.me', 'telegram.dog'}
# First path segments that are Telegram routes, not channels (t.me/share/url?..., t.me/proxy?...).
RESERVED_PATHS = {'share', 'iv', 'proxy', 'socks', 'addstickers', 'addemoji', 'boost', 'login', 'setlanguage',
                  'contact', 'bg', 'addtheme', 'confirmphone', 'invoice', 'giftcode', 'nft', 'm'}
DEFAULT_MAX_POSTS = 100       # blank maxPosts and blank postedAfter: the newest 100 posts per channel
MAX_POSTS_CEILING = 1_000_000  # same as the input schema's maximum
# Telegram launched in August 2013; nothing older exists, so a huge relative span clamps here.
TELEGRAM_EPOCH = datetime(2013, 8, 1, tzinfo=timezone.utc)

RELATIVE_RE = re.compile(r'^(\d+)\s*(day|week|month|year)s?(?:\s+ago)?$', re.I)
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
SPLIT_RE = re.compile(r'[\s,;]+')
SCHEME_RE = re.compile(r'^[a-z][a-z0-9+.-]*://', re.I)
WEB_CLIENT_RE = re.compile(r'^(?:https?://)?web\.telegram\.org(?:/|$)', re.I)
NUMERIC_ID_RE = re.compile(r'^-?\d+$')                 # 1006503122 or -1001006503122: a chat id, not a name
QUOTES = ' "\'<>`“”‘’«»'
TRAILING = ').,;:!?]}'

DATE_HELP = ('Use a date like 2026-01-31, a date and time like 2026-01-31T18:00:00Z, or a span back from now '
             'like "7 days", "2 weeks", "3 months" or "1 year". Leave it empty to skip the date filter.')


# ------------------------------------------------------------------------ channels --
def normalize_channel(raw) -> tuple[str | None, str | None]:
    """One pasted value -> (channel name, None) or (None, plain-English reason it was refused).

    'https://t.me/s/durov?before=100' -> ('durov', None)
    't.me/+AbCdEf123'                 -> (None, '"t.me/+AbCdEf123" is a private invite link. ...')
    The name keeps the case it was typed in; Telegram matches names case-insensitively
    and the parser reports the channel's own spelling.
    """
    original = str(raw).strip()
    # Quotes from chat apps and word processors, and punctuation from pasting a link out of a sentence.
    s = unquote(original).strip().strip(QUOTES).lstrip('([{').rstrip(TRAILING).strip()
    if not s:
        return None, None                                            # nothing there; not worth a reason
    shown = f'"{original}"'

    if NUMERIC_ID_RE.match(s):
        return None, numeric_id(shown)
    if WEB_CLIENT_RE.match(s):
        # Telegram Web (web.telegram.org/k/#@durov, /a/#@durov, /a/#?tgaddr=tg://resolve?domain=durov).
        fragment = unquote(urlsplit(s if SCHEME_RE.match(s) else f'https://{s}').fragment)
        addr = parse_qs(fragment.lstrip('?')).get('tgaddr')
        name = (parse_qs(urlsplit(addr[0]).query).get('domain') or [''])[0] if addr else fragment
        name = name.lstrip('@')
        if NUMERIC_ID_RE.match(name):
            return None, numeric_id(shown)
        if not name:
            return None, f'{shown} is a Telegram Web link without a channel name in it.'
        s = name

    if s.lower().startswith('tg:'):
        # App-only links: tg://resolve?domain=durov is a channel, tg://join?invite=... is private.
        u = urlsplit(s)
        if (u.netloc or u.path.lstrip('/')).lower() == 'join':
            return None, private_invite(shown)
        name = (parse_qs(u.query).get('domain') or [''])[0].lstrip('@')
        if not name:
            return None, f'{shown} is a Telegram app link without a public channel name in it.'
        s = name
    else:
        s = s.lstrip('@').rstrip('/')
        if '/' in s or '.' in s:
            u = urlsplit(s if SCHEME_RE.match(s) else f'https://{s}')
            host = (u.hostname or '').lower().removeprefix('www.')
            segs = [p for p in u.path.split('/') if p]
            if host.endswith('.t.me') and host.count('.') == 2:        # durov.t.me
                segs = [host.split('.')[0]] + segs
            elif host not in TELEGRAM_HOSTS:
                return None, (f'{shown} is not a Telegram link. Enter a public channel name like durov, '
                              f'or a link like t.me/durov.')
            if segs and segs[0].lower() == 's':                      # t.me/s/durov: the feed itself
                segs = segs[1:]
            if len(segs) > 1 and segs[0].lower() == 'boost':         # t.me/boost/durov: a boost link names the channel
                segs = segs[1:]
            if not segs:
                return None, f'{shown} is a Telegram link without a channel name in it.'
            first = segs[0]
            refused = refuse_path(first, shown)
            if refused:
                return None, refused
            s = first.lstrip('@')                                    # t.me/durov/123 -> durov

    refused = refuse_path(s, shown)
    if refused:
        return None, refused
    if not HANDLE_RE.match(s):
        return None, (f'{shown} is not a valid channel name. Telegram names are 4 to 32 letters, digits or '
                      f'underscores, start with a letter and do not end with an underscore.')
    return s, None


def numeric_id(shown: str) -> str:
    return (f'{shown} is a numeric chat id, which only a logged-in Telegram app can open. Open the channel in '
            f'Telegram and paste its @name or t.me link instead.')


def private_invite(shown: str) -> str:
    return (f'{shown} is a private invite link. Private channels and groups have no public web page, '
            f'so they cannot be read without joining them in the Telegram app.')


def refuse_path(segment: str, shown: str) -> str | None:
    """Reason when a path segment is a Telegram route that can never be a public channel."""
    low = segment.lower()
    if low.startswith('+') or low == 'joinchat':
        return private_invite(shown)
    if low == 'addlist':
        return (f'{shown} is a folder link, not a channel. Open it in Telegram and add the channels '
                f'inside it here one by one.')
    if low == 'c':
        return (f'{shown} links to a post in a private channel (t.me/c/...). Private channels have no '
                f'public web page.')
    if low in RESERVED_PATHS:
        return f'{shown} is a Telegram "{low}" link, not a channel.'
    return None


def parse_channels(value) -> list[dict]:
    """Every entry of the channels field, in input order: {input, handle, reason}.

    handle is None (and reason says why) for refused entries. Several names in one box
    ("durov, telegram" or one per line) are split. Repeats of the same channel are
    dropped, case-insensitively, keeping the first. Blank entries are dropped silently.
    """
    if value is None:
        items = []
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]                                              # an API caller sent one string
    entries, seen = [], set()
    for item in items:
        if item is None:
            continue
        for token in SPLIT_RE.split(str(item).strip()):
            handle, reason = normalize_channel(token) if token else (None, None)
            if not handle and not reason:
                continue
            if handle:
                if handle.lower() in seen:
                    continue
                seen.add(handle.lower())
            entries.append({'input': token, 'handle': handle, 'reason': reason})
    return entries


# -------------------------------------------------------------------------- limits --
def parse_posted_after(value, now: datetime) -> datetime | None:
    """The date filter as a UTC instant (inclusive), or None when it is empty.

    '2026-01-31'            -> 2026-01-31 00:00:00 UTC
    '2026-01-31T18:00:00Z'  -> that instant; no offset means UTC
    '7 days' / '3 months'   -> now minus that span (months and years are calendar months and years)
    Anything else raises ValueError with a sentence the user can act on.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'"Posted on or after" must be text, not {value!r}. {DATE_HELP}')
    s = value.strip()
    if not s:
        return None
    m = RELATIVE_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        try:
            if unit == 'day':
                cutoff = now - timedelta(days=n)
            elif unit == 'week':
                cutoff = now - timedelta(weeks=n)
            elif unit == 'month':
                cutoff = months_back(now, n)
            else:
                cutoff = months_back(now, 12 * n)
        except (OverflowError, ValueError):
            return TELEGRAM_EPOCH
        return max(cutoff, TELEGRAM_EPOCH)
    try:
        if DATE_RE.match(s):
            parsed = datetime.strptime(s, '%Y-%m-%d')
        else:
            parsed = datetime.fromisoformat(s.replace('z', 'Z'))
    except ValueError:
        raise ValueError(f'"{value}" is not a date the post filter understands. {DATE_HELP}') from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def months_back(moment: datetime, months: int) -> datetime:
    """Same day-of-month `months` calendar months earlier, clamped to the month's last day
    (31 March minus 1 month = 28 or 29 February)."""
    total = moment.year * 12 + (moment.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    if year < 1:
        raise OverflowError('before year 1')
    next_first = datetime(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_first - timedelta(days=1)).day
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def resolve_limits(max_posts, cutoff: datetime | None) -> int | None:
    """Posts to deliver per channel; None means no cap (read back to the date).

    Empty and no date -> 100 (the newest 100 posts per channel).
    Empty with a date -> None: every post on or after that date.
    A number          -> that cap; the date, when set, can still stop a channel sooner.
    0 counts as empty (the common "no limit" convention); the form itself cannot send it.
    Raises ValueError for anything that is not a whole number of 1 or more.
    """
    if max_posts is None or max_posts == '' or (not isinstance(max_posts, bool) and max_posts == 0):
        return None if cutoff else DEFAULT_MAX_POSTS
    bad = ValueError(f'"Max posts per channel" must be a whole number of 1 or more, not {max_posts!r}. '
                     f'Leave it empty to get the newest {DEFAULT_MAX_POSTS} posts, or every post since the '
                     f'date you set.')
    if isinstance(max_posts, bool):
        raise bad
    if isinstance(max_posts, str):
        if not re.fullmatch(r'\s*\d+\s*', max_posts):
            raise bad
        max_posts = int(max_posts)
    if not isinstance(max_posts, (int, float)) or not math.isfinite(max_posts):
        raise bad
    n = int(max_posts)
    if n == 0:
        return None if cutoff else DEFAULT_MAX_POSTS
    if n < 1:
        raise bad
    return min(n, MAX_POSTS_CEILING)
