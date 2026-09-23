"""The runtime against real t.me pages: channel check, backward walk, retries, and the whole actor.

Run: python -m pytest -q tests/test_walk.py   (no network: httpx.MockTransport serves saved pages)

Every page under tests/fixtures/walk/ was fetched from t.me on 2026-09-23 (recon). The
tchantest pages chain through Telegram's own cursors: the newest page says "older posts
start before #74", the before=74 page says 54, and so on down to the channel's first
post. One link is deliberately overlapped: no before=34 page was saved, so the before=41
page (posts 21 to 40) answers it, which re-serves posts 34 to 40 and exercises the
de-duplication exactly the way an arbitrary cursor does on the live site.

The last group runs `python -m src.main` in a subprocess with the same mock transport and
a throwaway local storage folder, then reads the dataset, the OUTPUT record and the exit
code, including a pay-per-event run whose spending limit cuts the dataset short.
"""
import asyncio
import gzip
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from src import telegram
from src.parse import parse_page

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / 'tests' / 'fixtures' / 'walk'


def page(name: str) -> str:
    return gzip.decompress((FIXTURES / f'{name}.html.gz').read_bytes()).decode('utf-8')


class FakeTelegram:
    """A stand-in for t.me. routes: '/s/x?before=74' -> fixture name | httpx.Response | exception |
    list of those (one per call, the last one repeats). Records every path asked for."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.url.raw_path.decode()
        self.calls.append(key)
        if key not in self.routes:
            raise AssertionError(f'unexpected request {key}')
        answer = self.routes[key]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, text=page(answer), headers={'content-type': 'text/html; charset=utf-8'})


def redirect(location: str) -> httpx.Response:
    return httpx.Response(302, headers={'location': location})


TCHANTEST = {
    '/s/tchantest': 'tchantest_head',                   # posts 74..94, cursor 74
    '/s/tchantest?before=74': 'tchantest_before74',     # 54..73, cursor 54
    '/s/tchantest?before=54': 'tchantest_before54',     # 34..53, cursor 34
    '/s/tchantest?before=34': 'tchantest_before41',     # 21..40 (overlap 34..40), cursor 21
    '/s/tchantest?before=21': 'tchantest_before21',     # 1..20, no cursor: the channel's start
}


@pytest.fixture
def naps(monkeypatch):
    """Every sleep the runtime asks for, recorded instead of waited."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(telegram, 'sleep', fake_sleep)
    return slept


def run(coro):
    return asyncio.run(coro)


async def walk(server: FakeTelegram, handle: str, **kw):
    """check_channel then walk_channel through the fake; -> (pages of rows, stats)."""
    async with telegram.new_client(httpx.MockTransport(server)) as client:
        status, reason, first = await telegram.check_channel(client, handle)
        assert (status, reason) == ('ok', None)
        stats = telegram.WalkStats()
        pages = [rows async for rows in telegram.walk_channel(client, handle, first, stats=stats, pause=0, **kw)]
    return pages, stats


def ids(pages) -> list[int]:
    return [r['postId'] for rows in pages for r in rows]


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(timezone.utc)


# 1. termination ------------------------------------------------------------------------------
def test_full_walk_follows_telegrams_cursors_to_the_first_post(naps):
    server = FakeTelegram(dict(TCHANTEST))
    pages, stats = run(walk(server, 'tchantest'))
    assert server.calls == ['/s/tchantest', '/s/tchantest?before=74', '/s/tchantest?before=54',
                            '/s/tchantest?before=34', '/s/tchantest?before=21']
    # 17 + 20 + 20 posts, then 13 new ones (34..40 were already delivered), then 18
    assert [len(rows) for rows in pages] == [17, 20, 20, 13, 18]
    got = ids(pages)
    assert got == sorted(got, reverse=True), 'newest to oldest across the whole stream'
    assert len(got) == len(set(got)) == 88
    assert got[0] == 94 and got[-1] == 1
    assert (stats.stopped_by, stats.pages, stats.posts, stats.error) == ('start', 5, 88, None)


def test_short_pages_and_albums_do_not_end_the_walk(naps):
    # The newest page renders 17 posts for 20 ids (album 84 = ids 84..87): the walk must go on.
    head = parse_page(page('tchantest_head'))
    assert len(head.posts) == 17 and head.before == 74
    server = FakeTelegram(dict(TCHANTEST))
    pages, _ = run(walk(server, 'tchantest'))
    assert len(pages) == 5


def test_a_channel_with_no_posts_is_ok_and_costs_one_request(naps):
    server = FakeTelegram({'/s/telegram': 'telegram_no_posts'})
    pages, stats = run(walk(server, 'telegram'))
    assert pages == [] and server.calls == ['/s/telegram']
    assert (stats.stopped_by, stats.posts) == ('start', 0)


# 2. cursor guards -------------------------------------------------------------------------
@pytest.mark.parametrize('bad', ['0', '-5', 'abc', '1'])
def test_never_sends_a_cursor_telegram_would_answer_with_the_newest_page(naps, bad):
    # before=0, -5 and abc silently return the NEWEST page on the live site; before=1 is empty.
    html = page('tchantest_head').replace('data-before="74"', f'data-before="{bad}"')
    html = html.replace('before=74', f'before={bad}')          # the rel=prev fallback link too
    server = FakeTelegram({'/s/tchantest': httpx.Response(200, text=html)})
    pages, stats = run(walk(server, 'tchantest'))
    assert server.calls == ['/s/tchantest'], 'no second request at all'
    assert len(ids(pages)) == 17 and stats.stopped_by == 'start'


def test_no_progress_guard_stops_a_page_that_does_not_go_back(naps):
    # Telegram answers before=74 with the newest page again (its fallback for bad cursors):
    # its cursor is 74 again, so the walk stops instead of looping, and nothing repeats.
    server = FakeTelegram({'/s/tchantest': 'tchantest_head', '/s/tchantest?before=74': 'tchantest_head'})
    pages, stats = run(walk(server, 'tchantest'))
    assert server.calls == ['/s/tchantest', '/s/tchantest?before=74']
    assert [len(rows) for rows in pages] == [17]
    assert stats.stopped_by == 'no_progress'


# 3. maxPosts ------------------------------------------------------------------------------
def test_cap_counts_an_album_as_one_post(naps):
    server = FakeTelegram(dict(TCHANTEST))
    pages, stats = run(walk(server, 'tchantest', cap=17))
    got = ids(pages)
    assert len(got) == 17 and server.calls == ['/s/tchantest'], 'the cap is met on page 1: no page 2'
    assert 84 in got and not {85, 86, 87} & set(got), 'album 84..87 is the single post 84'
    album = next(r for rows in pages for r in rows if r['postId'] == 84)
    assert album['mediaType'] == 'album' and len(album['media']) == 4
    assert stats.stopped_by == 'cap'


def test_cap_cuts_the_last_page_to_fit_exactly(naps):
    server = FakeTelegram(dict(TCHANTEST))
    pages, stats = run(walk(server, 'tchantest', cap=25))
    assert [len(rows) for rows in pages] == [17, 8]
    assert ids(pages)[-8:] == [73, 72, 71, 70, 69, 68, 67, 66], 'the newest 8 of the second page'
    assert server.calls == ['/s/tchantest', '/s/tchantest?before=74']
    assert (stats.posts, stats.stopped_by) == (25, 'cap')


def test_cap_of_one(naps):
    pages, _ = run(walk(FakeTelegram(dict(TCHANTEST)), 'tchantest', cap=1))
    assert ids(pages) == [94]


# 4. postedAfter ----------------------------------------------------------------------------
def test_cutoff_is_inclusive_and_stops_after_the_page_that_crosses_it(naps):
    second = parse_page(page('tchantest_before74')).posts          # ascending, 54..73
    cutoff_post = second[5]                                          # post 59
    cutoff = at(cutoff_post['date'])
    server = FakeTelegram(dict(TCHANTEST))
    pages, stats = run(walk(server, 'tchantest', cutoff=cutoff))
    got_dates = [at(r['date']) for rows in pages for r in rows]
    assert min(got_dates) == cutoff, 'a post exactly at the cutoff is kept (on or after)'
    assert all(d >= cutoff for d in got_dates)
    kept_second = [p['postId'] for p in second if at(p['date']) >= cutoff]
    assert ids(pages) == ids([parse_page(page('tchantest_head')).posts[::-1]]) + kept_second[::-1]
    assert server.calls == ['/s/tchantest', '/s/tchantest?before=74'], 'no page after the one that crossed it'
    assert stats.stopped_by == 'cutoff'


def test_cutoff_newer_than_every_post_reads_one_page_and_returns_nothing(naps):
    server = FakeTelegram(dict(TCHANTEST))
    pages, stats = run(walk(server, 'tchantest', cutoff=datetime(2030, 1, 1, tzinfo=timezone.utc)))
    assert pages == [] and server.calls == ['/s/tchantest'] and stats.stopped_by == 'cutoff'


def test_cutoff_older_than_every_post_reads_the_whole_channel(naps):
    pages, stats = run(walk(FakeTelegram(dict(TCHANTEST)), 'tchantest',
                            cutoff=datetime(2020, 1, 1, tzinfo=timezone.utc)))
    assert len(ids(pages)) == 88 and stats.stopped_by == 'start'


def test_cap_and_cutoff_whichever_comes_first(naps):
    early = datetime(2020, 1, 1, tzinfo=timezone.utc)
    pages, stats = run(walk(FakeTelegram(dict(TCHANTEST)), 'tchantest', cap=30, cutoff=early))
    assert len(ids(pages)) == 30 and stats.stopped_by == 'cap'


# 5. retries ---------------------------------------------------------------------------------
def test_retries_a_connection_reset_then_succeeds(naps):
    server = FakeTelegram({**TCHANTEST, '/s/tchantest': [
        httpx.ConnectError('[Errno 54] Connection reset by peer'), 'tchantest_head']})
    pages, _ = run(walk(server, 'tchantest', cap=5))
    assert server.calls == ['/s/tchantest', '/s/tchantest'] and len(ids(pages)) == 5
    assert len(naps) == 1 and 1.0 <= naps[0] <= 2.0, 'one backoff of about 1 s'


@pytest.mark.parametrize('error', [httpx.ReadTimeout('read timed out'), httpx.ConnectTimeout('connect timed out'),
                                   httpx.RemoteProtocolError('peer closed connection')])
def test_retries_timeouts_and_protocol_errors(naps, error):
    server = FakeTelegram({**TCHANTEST, '/s/tchantest': [error, 'tchantest_head']})
    pages, _ = run(walk(server, 'tchantest', cap=1))
    assert ids(pages) == [94] and len(server.calls) == 2


def test_honours_retry_after_on_429(naps):
    server = FakeTelegram({**TCHANTEST, '/s/tchantest': [
        httpx.Response(429, headers={'retry-after': '7'}), 'tchantest_head']})
    pages, _ = run(walk(server, 'tchantest', cap=1))
    assert naps == [7.0] and ids(pages) == [94]


def test_retry_after_is_capped(naps):
    server = FakeTelegram({**TCHANTEST, '/s/tchantest': [
        httpx.Response(429, headers={'retry-after': '3600'}), 'tchantest_head']})
    run(walk(server, 'tchantest', cap=1))
    assert naps == [telegram.RETRY_AFTER_CAP_S]


def test_retries_5xx_with_growing_backoff(naps):
    server = FakeTelegram({**TCHANTEST, '/s/tchantest': [
        httpx.Response(502), httpx.Response(503), httpx.Response(500), 'tchantest_head']})
    run(walk(server, 'tchantest', cap=1))
    assert len(naps) == 3
    assert 1 <= naps[0] <= 2 and 2 <= naps[1] <= 3 and 4 <= naps[2] <= 5


def test_a_page_that_keeps_failing_ends_the_channel_with_what_it_has(naps):
    server = FakeTelegram({**TCHANTEST, '/s/tchantest?before=74': httpx.ConnectError('[Errno 54] reset')})
    pages, stats = run(walk(server, 'tchantest'))
    assert [len(rows) for rows in pages] == [17], 'the first page is delivered'
    assert server.calls.count('/s/tchantest?before=74') == telegram.MAX_ATTEMPTS
    assert stats.stopped_by == 'error'
    assert 'did not answer after 5 tries' in stats.error and '#74' in stats.error


def test_check_that_keeps_failing_is_an_error_not_a_crash(naps):
    server = FakeTelegram({'/s/tchantest': httpx.ConnectError('[Errno 54] reset')})

    async def go():
        async with telegram.new_client(httpx.MockTransport(server)) as client:
            return await telegram.check_channel(client, 'tchantest')
    status, reason, first = run(go())
    assert status == 'error' and first is None
    assert 'Could not reach t.me for @tchantest' in reason and '5 tries' in reason


# 6. classification of everything that is not a readable channel -----------------------------
CLASSES = [
    # handle, landing fixture, embed fixture, status, words the reason must contain, requests
    ('qassambrigades', 'landing_restricted_qassambrigades', 'embed_restricted_qassambrigades', 'restricted',
     'Telegram says: "Unfortunately, this channel couldn\'t be displayed on your device."', 3),
    ('zlibrary_official', 'landing_banned_zlibrary_official', 'embed_banned_zlibrary_official', 'banned',
     'Telegram says: "This channel is unavailable due to copyright infringement."', 3),
    ('ru_python', 'landing_group_ru_python', None, 'is_group',
     '@ru_python is a group, not a channel. Telegram only shows channels publicly.', 2),
    ('zuck', 'landing_user_zuck', None, 'is_user', "@zuck is a person's account, not a channel.", 2),
    ('BotFather', 'landing_bot_BotFather', None, 'is_bot', '@BotFather is a bot, not a channel.', 2),
    ('thishandledoesnotexist_zz9q', 'landing_not_found_thishandledoesnotexist_zz9q', None, 'not_found',
     '@thishandledoesnotexist_zz9q does not exist.', 2),
    ('AbCdEf123', 'landing_private_link_AbCdEf123', None, 'private_link', 'private invite', 2),
]


async def check(server: FakeTelegram, handle: str):
    async with telegram.new_client(httpx.MockTransport(server)) as client:
        return await telegram.check_channel(client, handle)


@pytest.mark.parametrize('handle, landing, embed, status, says, requests', CLASSES)
def test_every_classification_outcome(naps, handle, landing, embed, status, says, requests):
    routes = {f'/s/{handle}': redirect(f'https://t.me/{handle}'), f'/{handle}': landing}
    if embed:
        routes[f'/{handle}/1?embed=1&mode=tme'] = embed
    server = FakeTelegram(routes)
    got_status, reason, first = run(check(server, handle))
    assert got_status == status and first is None
    assert says in reason, reason
    assert len(server.calls) == requests
    assert '\u2014' not in reason and '\u2013' not in reason


def test_a_readable_channel_costs_one_request(naps):
    server = FakeTelegram({'/s/tchantest': 'tchantest_head'})
    status, reason, first = run(check(server, 'tchantest'))
    assert (status, reason) == ('ok', None) and first.channel.handle == 'tchantest'
    assert first.channel.id == 1591537674 and server.calls == ['/s/tchantest']


def test_restricted_without_telegrams_own_reason_falls_back_to_ours(naps):
    server = FakeTelegram({'/s/qassambrigades': redirect('https://t.me/qassambrigades'),
                           '/qassambrigades': 'landing_restricted_qassambrigades',
                           '/qassambrigades/1?embed=1&mode=tme': httpx.ConnectError('reset')})
    status, reason, _ = run(check(server, 'qassambrigades'))
    assert status == 'restricted'
    assert reason == ('@qassambrigades has turned off its public web preview, so it can only be read inside the '
                      'Telegram app.')


def test_a_name_telegram_rejects_redirects_to_telegram_org(naps):
    server = FakeTelegram({'/s/duurov': redirect('https://t.me/duurov'), '/duurov': redirect('//telegram.org/')})
    status, reason, _ = run(check(server, 'duurov'))
    assert status == 'invalid' and 'does not accept "duurov"' in reason


def test_a_200_without_the_channel_header_is_classified_from_the_landing_page(naps):
    server = FakeTelegram({'/s/zuck': 'landing_user_zuck', '/zuck': 'landing_user_zuck'})
    status, _, _ = run(check(server, 'zuck'))
    assert status == 'is_user' and server.calls == ['/s/zuck', '/zuck']


# 7. posts the web preview hides ("Please open Telegram to view this post") ------------------
DUROV_RUSSIA = {'/s/durov_russia': 'durov_russia_head'}   # newest post #74 is such an orphan


def test_orphan_text_comes_from_og_description_and_is_flagged_truncated(naps):
    server = FakeTelegram({**DUROV_RUSSIA, '/durov_russia/74': 'durov_russia_74_single'})
    pages, _ = run(walk(server, 'durov_russia', cap=1))
    row = pages[0][0]
    assert row['postId'] == 74 and server.calls == ['/s/durov_russia', '/durov_russia/74']
    assert row['text'].startswith('Я не ходил в 4-й класс.')
    assert row['text'].endswith('…') and row['textTruncated'] is True


def test_orphan_og_that_is_just_the_channel_description_is_discarded(naps):
    # For a post with no text, og:description falls back to the channel's description
    # (seen on durov_russia/48). That must never become the post's text.
    server = FakeTelegram({**DUROV_RUSSIA, '/durov_russia/74': 'durov_russia_48_single'})
    pages, _ = run(walk(server, 'durov_russia', cap=1))
    row = pages[0][0]
    assert row['postId'] == 74 and row['text'] == '' and row['textTruncated'] is False
    assert row['unsupported'], 'the post is still emitted, with Telegram\'s label'


def test_orphan_whose_page_keeps_failing_is_kept_without_text(naps):
    server = FakeTelegram({**DUROV_RUSSIA, '/durov_russia/74': httpx.ConnectError('reset')})
    pages, _ = run(walk(server, 'durov_russia', cap=2))
    assert ids(pages) == [74, 73] and pages[0][0]['text'] == ''


def test_orphan_outside_the_date_filter_is_never_fetched(naps):
    server = FakeTelegram(dict(DUROV_RUSSIA))
    pages, _ = run(walk(server, 'durov_russia', cutoff=datetime(2030, 1, 1, tzinfo=timezone.utc)))
    assert pages == [] and server.calls == ['/s/durov_russia']


# 8. the whole actor, offline -----------------------------------------------------------------
HARNESS = r'''
import asyncio, gzip, json, sys
from pathlib import Path
import httpx
sys.path.insert(0, sys.argv[1])
from src import main as actor_main, telegram
routes = json.loads(sys.argv[2])
fixtures = Path(sys.argv[1]) / 'tests' / 'fixtures' / 'walk'

def handler(request):
    key = request.url.raw_path.decode()
    answer = routes.get(key)
    if answer is None:
        raise httpx.ConnectError(f'no route for {key}')
    if isinstance(answer, dict):
        return httpx.Response(answer['status'], headers={'location': answer['location']})
    return httpx.Response(200, text=gzip.decompress((fixtures / f'{answer}.html.gz').read_bytes()).decode())

async def no_sleep(seconds):
    return None

real_client = telegram.new_client
actor_main.new_client = lambda: real_client(httpx.MockTransport(handler))
telegram.sleep = no_sleep
exec(sys.argv[3])                      # a test's extra patch, usually empty
asyncio.run(actor_main.main())
'''

E2E_ROUTES = {
    **TCHANTEST,
    '/s/ru_python': {'status': 302, 'location': 'https://t.me/ru_python'},
    '/ru_python': 'landing_group_ru_python',
}


def run_actor(tmp_path: Path, actor_input: dict, env: dict | None = None, routes: dict | None = None,
              patch: str = ''):
    """python -m src.main against the fake t.me, local storage in tmp_path -> (exit code, rows, OUTPUT, log)."""
    store = tmp_path / 'key_value_stores' / 'default'
    store.mkdir(parents=True)
    (store / 'INPUT.json').write_text(json.dumps(actor_input))
    full_env = {k: v for k, v in os.environ.items() if not k.startswith(('APIFY_', 'ACTOR_', 'CRAWLEE_'))}
    full_env.update({'CRAWLEE_STORAGE_DIR': str(tmp_path), **(env or {})})
    proc = subprocess.run([sys.executable, '-c', HARNESS, str(REPO), json.dumps(routes or E2E_ROUTES), patch],
                          env=full_env, capture_output=True, text=True, timeout=120)
    rows = [json.loads(p.read_text()) for p in sorted((tmp_path / 'datasets' / 'default').glob('0*.json'))]
    out_file = store / 'OUTPUT'                  # local storage keeps the record without an extension
    output = json.loads(out_file.read_text()) if out_file.exists() else None
    return proc.returncode, rows, output, proc.stdout + proc.stderr


def test_actor_end_to_end_delivers_rows_and_explains_every_channel(tmp_path):
    code, rows, output, log = run_actor(tmp_path, {
        'channels': ['tchantest', 'https://t.me/ru_python', 't.me/+AbCdEf123', 'TCHANTEST'], 'maxPosts': 25})
    assert code == 0, log
    assert len(rows) == 25 and [r['postId'] for r in rows][:3] == [94, 92, 91]
    assert list(rows[0]) == list(parse_page(page('tchantest_head')).posts[0]), 'every SPEC key, in order'
    assert 'recordType' not in rows[0], 'no summary row mixed into the posts'
    assert output['status'] == 'done' and output['totalPosts'] == 25
    by_input = {c['input']: c for c in output['channels']}
    assert list(by_input) == ['tchantest', 'https://t.me/ru_python', 't.me/+AbCdEf123'], 'repeat dropped'
    tch = by_input['tchantest']
    assert (tch['status'], tch['channel'], tch['channelId'], tch['posts']) == ('ok', 'tchantest', 1591537674, 25)
    assert tch['newestDate'] == rows[0]['date'] and tch['oldestDate'] == rows[-1]['date']
    assert by_input['https://t.me/ru_python']['status'] == 'skipped'
    assert 'is a group' in by_input['https://t.me/ru_python']['reason']
    assert 'private invite link' in by_input['t.me/+AbCdEf123']['reason']
    assert set(output['channels'][0]) == {'input', 'channel', 'channelId', 'status', 'reason', 'posts',
                                          'newestDate', 'oldestDate'}
    assert output['message'].startswith('Got 25 posts from 1 of 3 channels: the newest 25 posts per channel.')
    assert '\u2014' not in output['message'] and '\u2013' not in output['message']


def test_actor_default_is_the_newest_100_posts(tmp_path):
    code, rows, output, log = run_actor(tmp_path, {'channels': ['tchantest']})
    assert code == 0, log
    # tchantest has 88 posts in total, fewer than the default 100: all of them, then a note why.
    assert len(rows) == 88 and output['channels'][0]['status'] == 'ok'
    assert output['channels'][0]['reason'] == 'Reached the oldest post the web preview shows, after 88 posts.'
    assert 'the newest 100 posts per channel, the default' in output['message']


def test_actor_stops_everything_at_the_spending_limit(tmp_path):
    ppe = {
        'ACTOR_TEST_PAY_PER_EVENT': '1',
        'APIFY_ACTOR_PRICING_INFO': json.dumps({'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {
            'actorChargeEvents': {'post': {'eventTitle': 'Post', 'eventPriceUsd': 0.001},
                                  'apify-default-dataset-item': {'eventTitle': 'free', 'eventPriceUsd': 0}}}}),
        'APIFY_CHARGED_ACTOR_EVENT_COUNTS': json.dumps({'post': 0}),
        'ACTOR_MAX_TOTAL_CHARGE_USD': '0.02',
    }
    code, rows, output, log = run_actor(tmp_path, {'channels': ['tchantest'], 'maxPosts': 60}, env=ppe)
    assert code == 0, log
    assert len(rows) == 20, 'a $0.02 limit at $0.001 a post delivers 20 rows, not 60'
    ch = output['channels'][0]
    assert (ch['status'], ch['posts']) == ('partial', 20)
    assert 'spending limit' in ch['reason'] and 'spending limit' in output['message']
    assert output['totalPosts'] == 20


def test_actor_fails_cleanly_on_empty_or_invalid_input(tmp_path):
    code, rows, output, log = run_actor(tmp_path / 'a', {'channels': []})
    assert code == 1 and rows == [] and 'No channel given.' in log
    code, _, _, log = run_actor(tmp_path / 'b', {'channels': ['t.me/+secret', 'abc']})
    assert code == 1 and 'private invite link' in log
    code, _, _, log = run_actor(tmp_path / 'c', {'channels': ['tchantest'], 'postedAfter': 'last tuesday'})
    assert code == 1 and 'is not a date the post filter understands' in log


def test_actor_fails_when_every_channel_is_skipped(tmp_path):
    code, rows, output, log = run_actor(tmp_path, {'channels': ['ru_python']})
    assert code == 1 and rows == []
    assert output['status'] == 'done' and output['channels'][0]['status'] == 'skipped'
    assert 'is a group' in output['message']


def test_actor_marks_a_channel_partial_when_a_page_keeps_failing(tmp_path):
    # Only the newest page answers; the harness answers every other path with a connection error.
    code, rows, output, log = run_actor(tmp_path, {'channels': ['tchantest'], 'maxPosts': 40},
                                        routes={'/s/tchantest': 'tchantest_head'})
    assert code == 0, 'partial success is success'
    ch = output['channels'][0]
    assert (ch['status'], ch['posts'], len(rows)) == ('partial', 17, 17)
    assert ch['reason'].startswith('Stopped after 17 posts because t.me did not answer after 5 tries')
    assert 'Those posts are saved' in ch['reason']


def test_actor_reads_a_channel_reached_by_two_names_only_once(tmp_path):
    # stickerpacks and stickerpack are one channel on the live site; same numeric id -> read once.
    routes = {**TCHANTEST, '/s/tchantest_alias': 'tchantest_head'}
    code, rows, output, log = run_actor(tmp_path, {'channels': ['tchantest', 'tchantest_alias'], 'maxPosts': 5},
                                        routes=routes)
    assert code == 0 and len(rows) == 5
    statuses = sorted(c['status'] for c in output['channels'])
    assert statuses == ['ok', 'skipped']
    dup = next(c for c in output['channels'] if c['status'] == 'skipped')
    assert 'is the same channel as' in dup['reason']


def test_actor_date_filter_alone_reads_back_to_the_date(tmp_path):
    cutoff = parse_page(page('tchantest_before74')).posts[5]['date']      # post 59
    code, rows, output, log = run_actor(tmp_path, {'channels': ['tchantest'], 'postedAfter': cutoff})
    assert code == 0, log
    assert rows[-1]['postId'] == 59 and all(at(r['date']) >= at(cutoff) for r in rows)
    assert output['channels'][0]['oldestDate'] == cutoff
    assert f'every post since {at(cutoff).strftime("%Y-%m-%d %H:%M:%S")} UTC' in output['message']


def test_actor_with_no_time_left_reads_nothing_and_says_why(tmp_path):
    past = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    code, rows, output, log = run_actor(tmp_path, {'channels': ['tchantest']}, env={'ACTOR_TIMEOUT_AT': past})
    assert code == 1 and rows == []
    assert 'was not read because the run was about to reach its time limit' in output['channels'][0]['reason']


def test_actor_survives_an_unexpected_error_in_one_channel(tmp_path):
    crash = """
real_parse = telegram.parse_page
def parse_page(html):
    if 'tchantest?before=54' in html:        # the second tchantest page
        raise RuntimeError('simulated parser bug')
    return real_parse(html)
telegram.parse_page = parse_page
"""
    routes = {**TCHANTEST, '/s/telegram': 'telegram_no_posts'}
    code, rows, output, log = run_actor(tmp_path, {'channels': ['tchantest', 'telegram']}, routes=routes, patch=crash)
    assert code == 0, log
    tch, tg = output['channels']
    assert (tch['status'], tch['posts'], len(rows)) == ('partial', 17, 17), 'the first page stays delivered'
    assert 'unexpected error (simulated parser bug)' in tch['reason'] and 'please report it' in tch['reason']
    assert (tg['status'], tg['posts'], tg['reason']) == ('ok', 0, 'The web preview shows no posts for this channel.')
