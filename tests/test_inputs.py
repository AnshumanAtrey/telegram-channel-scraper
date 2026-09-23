"""Input cleaning: every pasted channel shape, the refusals, the date filter and the post cap.

Run: python -m pytest -q tests/test_inputs.py   (no network, no Apify platform)

The channel cases are the shapes Telegram itself was probed with on 2026-09-23
(recon access-edges.md section 1), not invented ones.
"""
from datetime import datetime, timezone

import pytest

from src.inputs import (DEFAULT_MAX_POSTS, MAX_POSTS_CEILING, TELEGRAM_EPOCH, normalize_channel, parse_channels,
                        parse_posted_after, resolve_limits)

NOW = datetime(2026, 9, 23, 12, 30, 0, tzinfo=timezone.utc)


# 1. every accepted shape becomes the bare channel name --------------------------------
@pytest.mark.parametrize('raw, want', [
    ('durov', 'durov'),
    ('@durov', 'durov'),
    (' @durov ', 'durov'),
    ('Durov', 'Durov'),                                   # case kept; Telegram matches case-insensitively
    ('Durov%20', 'Durov'),                                # a trailing encoded space (Telegram drops /s/ on it)
    ('https://t.me/durov', 'durov'),
    ('http://t.me/durov/', 'durov'),
    ('t.me/durov', 'durov'),
    ('t.me/s/durov', 'durov'),
    ('https://t.me/s/durov/', 'durov'),
    ('https://t.me/s/durov?before=100', 'durov'),         # query dropped
    ('https://t.me/durov/123', 'durov'),                  # post link -> its channel
    ('https://t.me/s/durov/530', 'durov'),                # anchored feed link
    ('https://t.me/durov#top', 'durov'),                  # fragment dropped
    ('https://www.t.me/durov', 'durov'),
    ('telegram.me/durov', 'durov'),
    ('telegram.me/s/durov', 'durov'),
    ('https://telegram.dog/durov', 'durov'),
    ('telegram.dog/s/durov', 'durov'),
    ('durov.t.me', 'durov'),
    ('https://durov.t.me/', 'durov'),
    ('T.ME/durov', 'durov'),
    ('tg://resolve?domain=durov', 'durov'),
    ('tg://resolve?domain=durov&post=5', 'durov'),
    ('gram', 'gram'),                                     # 4 letters: real channel, 6.6M subscribers
    ('a' * 32, 'a' * 32),                                 # longest name Telegram accepts
    ('nexta_live', 'nexta_live'),
    ('"durov"', 'durov'),                                 # pasted with quotes
])
def test_accepted_shapes(raw, want):
    assert normalize_channel(raw) == (want, None)


# 2. refusals come with a reason that names the input and says why ---------------------
@pytest.mark.parametrize('raw, says', [
    ('https://t.me/+AbCdEf123', 'private invite link'),
    ('t.me/+AbCdEf123', 'private invite link'),
    ('https://t.me/joinchat/AbCd', 'private invite link'),
    ('tg://join?invite=AbCd', 'private invite link'),
    ('https://t.me/addlist/xyz', 'folder link'),
    ('https://t.me/c/1234567890/45', 'private channel'),
    ('t.me/s/c', 'private channel'),
    ('t.me/s/joinchat', 'private invite link'),
    ('https://t.me/share/url?url=https://example.com', '"share" link, not a channel'),
    ('t.me/proxy?server=1.2.3.4', '"proxy" link, not a channel'),
    ('t.me/iv?url=x', '"iv" link, not a channel'),
    ('t.me/addstickers/Animals', '"addstickers" link, not a channel'),
    ('t.me/boost', '"boost" link, not a channel'),
    ('login', '"login" link, not a channel'),             # bare reserved word, too
    ('https://example.com/durov', 'not a Telegram link'),
    ('https://t.me/', 'without a channel name'),
    ('t.me/s/', 'without a channel name'),
    ('tg://resolve?phone=123', 'without a public channel name'),
    ('abc', 'not a valid channel name'),                  # 3 chars: Telegram redirects to telegram.org
    ('a' * 33, 'not a valid channel name'),
    ('1durov', 'not a valid channel name'),               # starts with a digit
    ('_durov', 'not a valid channel name'),
    ('durov_', 'not a valid channel name'),               # trailing underscore
    ('du__rov', 'not a valid channel name'),              # double underscore
    ('du-rov', 'not a valid channel name'),
])
def test_refusals(raw, says):
    handle, reason = normalize_channel(raw)
    assert handle is None
    assert says in reason, reason
    assert f'"{raw.strip()}"' in reason, 'the reason quotes what the user typed'
    assert '\u2014' not in reason and '\u2013' not in reason


def test_blank_is_nothing_not_an_error():
    assert normalize_channel('') == (None, None)
    assert normalize_channel('   ') == (None, None)


# 3. the whole field: split, order, dedupe -------------------------------------------------
def test_parse_channels_keeps_order_and_drops_repeats_case_insensitively():
    got = parse_channels(['durov', '@Durov', 'https://t.me/s/DUROV', 'telegram', 't.me/+x1', '', '  ', None])
    assert [(e['input'], e['handle']) for e in got] == [('durov', 'durov'), ('telegram', 'telegram'),
                                                        ('t.me/+x1', None)]
    assert 'private invite link' in got[2]['reason']


def test_parse_channels_splits_several_names_in_one_box():
    got = parse_channels(['durov, telegram\nnexta_live;gram'])
    assert [e['handle'] for e in got] == ['durov', 'telegram', 'nexta_live', 'gram']


def test_parse_channels_accepts_a_single_string_and_nothing():
    assert [e['handle'] for e in parse_channels('durov')] == ['durov']
    assert parse_channels(None) == []
    assert parse_channels([]) == []


# 4. postedAfter -----------------------------------------------------------------------------
@pytest.mark.parametrize('value, want', [
    ('2026-01-31', datetime(2026, 1, 31, tzinfo=timezone.utc)),
    ('2026-01-31T18:00:00Z', datetime(2026, 1, 31, 18, tzinfo=timezone.utc)),
    ('2026-01-31T18:00:00', datetime(2026, 1, 31, 18, tzinfo=timezone.utc)),          # no offset = UTC
    ('2026-01-31T18:00:00+05:30', datetime(2026, 1, 31, 12, 30, tzinfo=timezone.utc)),
    ('2026-01-31T18:00:00.250Z', datetime(2026, 1, 31, 18, 0, 0, 250000, tzinfo=timezone.utc)),
    ('2026-01-31 18:00', datetime(2026, 1, 31, 18, tzinfo=timezone.utc)),
    ('7 days', datetime(2026, 9, 16, 12, 30, tzinfo=timezone.utc)),
    ('1 day', datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc)),
    ('1 week', datetime(2026, 9, 16, 12, 30, tzinfo=timezone.utc)),
    ('2weeks', datetime(2026, 9, 9, 12, 30, tzinfo=timezone.utc)),
    ('3 months', datetime(2026, 6, 23, 12, 30, tzinfo=timezone.utc)),
    ('1 year', datetime(2025, 9, 23, 12, 30, tzinfo=timezone.utc)),
    ('3 Months ago', datetime(2026, 6, 23, 12, 30, tzinfo=timezone.utc)),
    ('0 days', NOW),
    ('100000 years', TELEGRAM_EPOCH),                     # nothing on Telegram is older than its launch
    ('', None),
    ('   ', None),
    (None, None),
])
def test_posted_after(value, want):
    assert parse_posted_after(value, NOW) == want


def test_month_arithmetic_clamps_to_the_last_day():
    march_31 = datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc)
    assert parse_posted_after('1 month', march_31) == datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc)
    leap_day = datetime(2028, 2, 29, tzinfo=timezone.utc)
    assert parse_posted_after('1 year', leap_day) == datetime(2027, 2, 28, tzinfo=timezone.utc)
    jan = datetime(2026, 1, 15, tzinfo=timezone.utc)
    assert parse_posted_after('2 months', jan) == datetime(2025, 11, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize('value', ['yesterday', '2026-13-01', '2026-02-30', '31/01/2026', '7 hours', '-3 days',
                                   'soon', 20260131])
def test_posted_after_rejects_with_a_usable_sentence(value):
    with pytest.raises(ValueError) as err:
        parse_posted_after(value, NOW)
    msg = str(err.value)
    assert '2026-01-31' in msg and '7 days' in msg, msg
    assert '\u2014' not in msg and '\u2013' not in msg


# 5. maxPosts ---------------------------------------------------------------------------------
CUTOFF = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize('max_posts, cutoff, want', [
    (None, None, DEFAULT_MAX_POSTS),         # empty and no date: newest 100
    ('', None, DEFAULT_MAX_POSTS),
    (0, None, DEFAULT_MAX_POSTS),            # 0 = empty (the form cannot send it)
    (None, CUTOFF, None),                    # empty with a date: every post since then
    (0, CUTOFF, None),
    (1, None, 1),
    (250, None, 250),
    (250, CUTOFF, 250),                      # both: whichever stops first
    ('40', None, 40),
    (40.0, None, 40),
    (5_000_000, None, MAX_POSTS_CEILING),
])
def test_resolve_limits(max_posts, cutoff, want):
    assert resolve_limits(max_posts, cutoff) == want


@pytest.mark.parametrize('max_posts', [-1, 'abc', '1e3', True, False, [10], float('nan'), float('inf')])
def test_resolve_limits_rejects_junk(max_posts):
    with pytest.raises(ValueError) as err:
        resolve_limits(max_posts, None)
    assert 'whole number of 1 or more' in str(err.value)


# 9. shapes real users paste from Telegram Web, chat apps and sentences (added 2026-09-24) -----
@pytest.mark.parametrize('raw, want', [
    ('https://web.telegram.org/k/#@durov', 'durov'),      # Telegram Web K
    ('https://web.telegram.org/a/#@durov', 'durov'),      # Telegram Web A
    ('web.telegram.org/k/#@durov', 'durov'),
    ('https://web.telegram.org/a/#?tgaddr=tg%3A%2F%2Fresolve%3Fdomain%3Ddurov', 'durov'),
    ('“durov”', 'durov'),                       # curly quotes from chat apps and word processors
    ('«durov»', 'durov'),
    ('`durov`', 'durov'),
    ('https://t.me/durov)', 'durov'),                     # pasted out of a sentence
    ('(https://t.me/durov).', 'durov'),
    ('t.me/durov,', 'durov'),
    ('https://t.me/boost/durov', 'durov'),                # a boost link names its channel
])
def test_pasted_shapes(raw, want):
    assert normalize_channel(raw) == (want, None)


@pytest.mark.parametrize('raw', ['-1001006503122', '1006503122', 'https://web.telegram.org/z/#-1001006503122',
                                 'web.telegram.org/k/#-1001006503122'])
def test_numeric_chat_ids_are_refused_with_a_way_out(raw):
    handle, reason = normalize_channel(raw)
    assert handle is None
    assert 'numeric chat id' in reason and '@name or t.me link' in reason


def test_telegram_web_link_without_a_name():
    handle, reason = normalize_channel('https://web.telegram.org/k/')
    assert handle is None and 'without a channel name' in reason
