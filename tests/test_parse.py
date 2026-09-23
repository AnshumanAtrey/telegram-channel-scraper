"""
Parser tests against real Telegram pages saved on 2026-09-23 (tests/fixtures/pages/*.html.gz).

Every expected value below was read off the raw HTML by hand (post ids, texts, URLs,
counters, sizes, durations), not copied from the parser's own output. Where a CDN link
carries a rotating ?token=, only the stable part is checked.

Run: python -m pytest -q tests/test_parse.py
"""
import functools
import gzip
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import parse  # noqa: E402

PAGES = ROOT / 'tests' / 'fixtures' / 'pages'
FEED_PAGES = sorted(p.name[:-8] for p in PAGES.glob('*.html.gz')
                    if not p.name.startswith(('landing_', 'single_')))
MEDIA_KEYS = ['type', 'url', 'thumbnailUrl', 'width', 'height', 'durationSeconds', 'fileName',
              'fileSize', 'fileId', 'postId', 'unavailable']


def html(name: str) -> str:
    return gzip.decompress((PAGES / f'{name}.html.gz').read_bytes()).decode('utf-8')


@functools.cache
def page(name: str) -> parse.Page:
    return parse.parse_page(html(name))


def post(name: str, post_id: int) -> dict:
    return next(r for r in page(name).posts if r['postId'] == post_id)


def strict_guard(fn, default=None):
    return fn()   # no swallowing: any extractor that trips fails the test


# --------------------------------------------------------------- whole-page shape --
def test_every_row_has_every_key_in_order():
    for name in FEED_PAGES:
        for row in page(name).posts:
            assert list(row) == list(parse.ROW_KEYS), (name, row['postId'])
            assert isinstance(row['text'], str) and isinstance(row['textTruncated'], bool)
            assert row['textTruncated'] is False   # only the runtime's og fallback sets it
            for item in row['media']:
                assert list(item)[:len(MEDIA_KEYS)] == MEDIA_KEYS, (name, row['postId'])


def test_no_field_extractor_fails_on_any_fixture(monkeypatch):
    monkeypatch.setattr(parse, '_guard', strict_guard)
    for name in FEED_PAGES:
        parse.parse_page(html(name))
    for p in PAGES.glob('landing_*.html.gz'):
        text = gzip.decompress(p.read_bytes()).decode('utf-8')
        parse.parse_page(text), parse.classify_landing(text), parse.parse_embed_error(text)


def test_posts_are_ascending_and_unique():
    for name in FEED_PAGES:
        ids = [r['postId'] for r in page(name).posts]
        assert ids == sorted(set(ids)), name


# ---------------------------------------------------------------- channel header --
def test_channel_header_and_cursor_on_head_page():
    p = page('durov__head')
    assert p.channel == parse.Channel(handle='durov', id=1006503122, title='Pavel Durov',
                                      subscribers=10_700_000, description='Founder of Telegram.')
    assert p.has_channel_info is True
    assert (p.before, p.after) == (528, None)
    # 20 posts, 528..548 with 530 deleted
    assert [r['postId'] for r in p.posts] == [528, 529] + list(range(531, 549))


def test_cursors_come_from_the_page():
    assert (page('BotNews__before30').before, page('BotNews__before30').after) == (10, 29)
    assert (page('tchantest__before21').before, page('tchantest__before21').after) == (None, 20)
    assert (page('tginfo__before4413').before, page('tginfo__before4413').after) == (4392, 4412)
    assert (page('magisk_update__head').before, page('magisk_update__head').after) == (None, None)


def test_channel_without_description_and_small_counts():
    assert page('magisk_update__head').channel.description is None
    assert page('magisk_update__head').channel.subscribers == 5
    assert page('concall_pulse__head').channel.subscribers == 1920   # "1.92K"
    assert page('concall_pulse__head').channel.description is None
    assert page('tchantest__head').channel.title == "tchan's test channel 👍"   # emoji inside the title


def test_data_post_slug_can_differ_from_the_handle():
    p = page('stickerpacks__head')
    assert p.channel.handle == 'stickerpacks' and p.channel.title == 'STEACKER PACK'
    assert p.channel.subscribers == 47 and p.channel.description is None
    row = post('stickerpacks__head', 7)
    assert row['channel'] == 'stickerpack'
    assert row['postUrl'] == 'https://t.me/stickerpack/7'
    assert {r['channel'] for r in p.posts} == {'stickerpack'}


def test_no_posts_found_page_still_has_the_channel():
    p = page('telegram__before1')
    assert p.posts == [] and p.orphans == []
    assert p.has_channel_info is True
    assert (p.channel.handle, p.channel.title, p.channel.subscribers) == ('telegram', 'Telegram News', 9_490_000)
    assert (p.before, p.after) == (None, None)   # rel=next is "?after=" with no value
    assert p.channel.id is None                  # no data-view on an empty page


def test_data_view_is_unpadded_base64url_json():
    raw = 'eyJjIjotMTAwNjUwMzEyMiwicCI6NTQ4LCJ0IjoxNzkwMTc5NzU2LCJoIjoiMmY3ZjE0MTc5MWU0MTlhNzYwIn0'
    assert len(raw) % 4 != 0
    assert parse._decode_view(raw) == {'c': -1006503122, 'p': 548, 't': 1790179756, 'h': '2f7f141791e419a760'}
    assert parse._decode_view('!!!not base64') == {}
    row = post('durov__head', 548)
    assert row['channelId'] == 1006503122
    assert row['scrapedAt'] == '2026-09-23T16:09:16+00:00'   # t=1790179756


# ------------------------------------------------------------------- text + meta --
def test_custom_emoji_post_reads_the_supported_copy():
    row = post('durov__head', 548)
    assert row['text'].startswith(
        '🤝 Telegram has become the sponsor of Codeforces — the largest competitive programming '
        'platform in the world. \n\n ⚡CodeForces organizes over 100 coding contests')
    assert row['text'].endswith("we'll be happy to help him do that 🤝")
    assert 'Please open Telegram' not in row['text']
    assert row['textHtml'].startswith('<tg-emoji emoji-id="5463249828450424568">')
    assert row['unsupported'] is None
    assert row['date'] == '2026-09-11T16:04:02+00:00'
    assert (row['author'], row['views'], row['isEdited']) == ('Pavel Durov', 1_480_000, False)
    assert row['links'] == ['https://codeforces.com/blog/entry/156620']
    assert row['postUrl'] == 'https://t.me/durov/548'
    assert (row['channelTitle'], row['channelSubscribers']) == ('Pavel Durov', 10_700_000)


def test_reactions_emoji_custom_and_paid():
    assert post('durov__head', 548)['reactions'] == [
        {'type': 'paid', 'emoji': None, 'customEmojiId': None, 'count': 11800},
        {'type': 'custom', 'emoji': None, 'customEmojiId': '5399847211989246390', 'count': 26600},
        {'type': 'custom', 'emoji': None, 'customEmojiId': '5936157098181135162', 'count': 7640},
        {'type': 'custom', 'emoji': None, 'customEmojiId': '5373223594484587136', 'count': 7190},
    ]
    assert post('durov_russia__before51', 33)['reactions'] == [
        {'type': 'emoji', 'emoji': '💩', 'customEmojiId': None, 'count': 3030},
        {'type': 'emoji', 'emoji': '👍', 'customEmojiId': None, 'count': 962},
        {'type': 'custom', 'emoji': None, 'customEmojiId': '5935912783261470019', 'count': 427},
        {'type': 'emoji', 'emoji': '❤', 'customEmojiId': None, 'count': 262},
        {'type': 'emoji', 'emoji': '👎', 'customEmojiId': None, 'count': 238},
    ]
    assert post('stickerpacks__head', 5)['reactions'] == [
        {'type': 'emoji', 'emoji': '🔥', 'customEmojiId': None, 'count': 2}]


def test_signature_and_edited_marker():
    row = post('tchantest__before21', 5)
    assert (row['text'], row['author'], row['isEdited']) == ('Hello! Signed and edited message', 'Álvaro Justen', True)
    row = post('tchantest__before21', 6)
    assert (row['text'], row['author'], row['isEdited']) == ('Unsigned message', None, False)
    row = post('durov__head', 535)
    assert (row['author'], row['isEdited'], row['date']) == ('Pavel Durov', True, '2026-07-30T16:52:37+00:00')
    row = post('tginfo__before4413', 4405)
    assert (row['author'], row['isEdited']) == ('Sominemo', True)


def test_br_becomes_newline_and_pre_keeps_its_lines():
    assert post('magisk_update__head', 6)['text'] == 'Magisk: official\nVer: 27.0'
    text = post('pythonl__head', 5716)['text']
    assert text.startswith('🐍 В CPython одно чтение `__dict__` может навсегда замедлить доступ к '
                           'атрибутам конкретного объекта.\n\nНачиная с Python 3.11')
    assert '\n\n\n`obj.__dict__`\n\n\n' in text


def test_links_fix_double_escaping_drop_hashtags_and_dedupe():
    # a[onclick] text link: raw href has &amp;amp;
    assert ('https://ct.com/news/raiffeisen-crypto-11-european-markets-bitpanda'
            '?utm_campaign=rss_partner_inbound&utm_medium=rss&utm_source=rss_feed') in post('cointelegraph__head', 72279)['links']
    # relative hashtag links (?q=%23...) are dropped; mentions and tg:// kept; @tginfo appears 3 times
    assert post('tginfo__before4413', 4405)['links'] == [
        'https://t.me/tginfo', 'https://t.me/tginfo/4397', 'https://t.me/d_code/25732',
        'https://t.me/bruhcollective/874', 'https://www.rbc.ru/rbcfreenews/6998bacd9a7947720c215815',
        'https://telegram.org/apps', 'tg://settings/devices',
    ]
    assert post('tginfo__before4413', 4405)['text'].endswith('#iOS #безопасность #telega')
    row = post('concall_pulse__head', 1722)
    assert row['text'] == '🔔 #SHIPROCKET concall in 30 min · 9:00 AM IST'
    assert row['links'] == []
    for name in FEED_PAGES:
        for r in page(name).posts:
            assert not any(link.startswith(('?', '/')) or '&amp;' in link for link in r['links']), (name, r['postId'])


def test_via_bot():
    assert post('BotNews__before30', 10)['viaBot'] == '@sticker'
    assert post('BotNews__before30', 16)['viaBot'] == '@like'
    assert post('durov__head', 548)['viaBot'] is None


# -------------------------------------------------------------------- reply trap --
def test_reply_variants_and_reply_text_never_leaks():
    row = post('durov_russia__head', 61)   # quote reply
    assert row['replyTo'] == {'postId': 57, 'url': 'https://t.me/durov_russia/57', 'channel': 'durov_russia'}
    assert row['text'] == '100% 😺' and row['isEdited'] is True
    row = post('tchantest__before21', 20)  # parent is a captionless video ("Video" metatext)
    assert row['replyTo'] == {'postId': 19, 'url': 'https://t.me/tchantest/19', 'channel': 'tchantest'}
    assert row['text'] == 'Reply to a video (not recorded in telegram)'
    row = post('tgbeta__head', 4774)       # other channel + malformed data-bg-emoji="…"" attribute
    assert row['replyTo'] == {'postId': 236, 'url': 'https://t.me/durov/236', 'channel': 'durov'}
    assert row['text'].startswith('🆕 Giveaways in Channels and Free Premium\n\nChannel admins can now launch Giveaways')
    row = post('tginfo__before4413', 4405)  # reply with thumb, then a photo
    assert row['replyTo']['postId'] == 4397
    assert row['text'].startswith('Опасный неофициальный клиент «Telega» удалён из App Store')
    assert 'Telegram сможет предупреждать' not in row['text']   # that is the quoted parent


# ------------------------------------------------------------------------- media --
def test_single_photo_size_and_stable_id():
    row = post('durov__head', 536)
    assert (row['mediaType'], row['text']) == ('photo', '🇦🇫😈')
    item = row['media'][0]
    assert item['url'].startswith('https://cdn4.telesco.pe/file/u5RcmXiAC3DQZx')
    assert row['mediaUrl'] == item['url']
    assert (item['width'], item['height'], item['fileId'], item['postId']) == (800, 524, '5429460311875460150', 536)
    item = post('concall_pulse__head', 1722)['media'][0]
    assert (item['width'], item['height'], item['fileId']) == (600, 800, '5838303424217288680')
    item = post('tchantest__before21', 16)['media'][0]   # narrow photo: inner div also has width:75%
    assert (item['width'], item['height'], item['fileId']) == (450, 600, '5172795381250108682')


def test_album_is_one_row_with_every_item_and_single_caption():
    row = post('cointelegraph__head', 72264)
    assert row['mediaType'] == 'album'
    assert [(m['type'], m['postId']) for m in row['media']] == [('photo', 72264), ('photo', 72265)]
    assert row['media'][0]['url'].startswith('https://cdn4.telesco.pe/file/buii_U-khakD9P')
    assert row['media'][1]['url'].startswith('https://cdn4.telesco.pe/file/XrQw-s8a_w5JlV')
    assert all(m['width'] is None and m['fileId'] is None for m in row['media'])   # layout px only
    assert row['text'] == ('🚨 NEW: OpenAI CEO Sam Altman and Anthropic CEO Dario Amodei are expected to address '
                           'the UN Security Council this week during a meeting on AI safety and global security.'
                           '\n\nNews | Markets | YouTube')
    assert not row['textHtml'].startswith('<div')   # innermost of the two wrapped caption divs
    assert row['links'] == ['https://cointelegraph.com/', 'https://cointelegraph.com/category/markets',
                            'https://www.youtube.com/@cointelegraph']


def test_mixed_album_items_keep_their_own_ids():
    row = post('tchantest__head', 84)
    assert row['text'] == 'Multiple videos and pictures'
    assert [(m['type'], m['postId'], m['fileId'], m['durationSeconds']) for m in row['media']] == [
        ('video', 84, 'bd7ceb0b41', 27), ('photo', 85, None, None),
        ('video', 86, 'e996ca12fa', 8), ('photo', 87, None, None)]
    assert row['media'][0]['thumbnailUrl'].startswith('https://cdn1.telesco.pe/file/aftC9pYPhQ9TUZ')
    assert row['media'][1]['url'].startswith('https://cdn1.telesco.pe/file/RIcS3_irHO-4Mo')
    assert row['mediaUrl'].startswith('https://cdn1.telesco.pe/file/bd7ceb0b41.mp4?token=')


def test_album_nested_inside_supported_cont():
    row = post('durov_russia__head', 55)
    assert [(m['type'], m['postId']) for m in row['media']] == [('photo', 55), ('photo', 56)]
    assert row['text'].startswith('✌️ Наши конкуренты отчаялись')
    assert row['text'].endswith('растёт 😎')
    assert row['unsupported'] is None and 55 not in page('durov_russia__head').orphans


def test_video_with_file():
    row = post('durov__head', 546)
    item = row['media'][0]
    assert row['mediaType'] == 'video' and row['unsupported'] is None   # browser fallback label ignored
    assert item['url'].startswith('https://cdn4.telesco.pe/file/e0b2769474.mp4?token=')
    assert (item['width'], item['height'], item['durationSeconds'], item['fileId'], item['unavailable']) == (
        1920, 1920, 20, 'e0b2769474', None)
    assert (row['views'], row['date']) == (2_880_000, '2026-08-31T16:30:15+00:00')
    item = post('tchantest__before21', 18)['media'][0]
    assert (item['type'], item['width'], item['height'], item['durationSeconds']) == ('video', 720, 960, 6)


def test_video_too_big_has_only_a_thumbnail():
    row = post('V_Zelenskiy_official__head', 21001)
    item = row['media'][0]
    assert (item['type'], item['url'], item['unavailable']) == ('video', None, 'too_big')
    assert item['thumbnailUrl'].startswith('https://cdn4.telesco.pe/file/QHY6JRUpA8vv5k')
    assert (item['width'], item['height'], item['durationSeconds'], item['fileId']) == (1920, 1080, 11, None)
    assert row['mediaUrl'] is None
    assert row['unsupported'] == 'Media is too big'


def test_gif_has_no_play_button_and_no_duration():
    item = post('durov__head', 531)['media'][0]
    assert item['type'] == 'gif'
    assert item['url'].startswith('https://cdn4.telesco.pe/file/b231b9b161.mp4?token=')
    assert (item['width'], item['height'], item['durationSeconds'], item['fileId']) == (1920, 1920, None, 'b231b9b161')
    assert item['thumbnailUrl'].startswith('https://cdn4.telesco.pe/file/QAf9ipuWR_1f3N')
    # an older GIF renders a second, blurred <video> outside the wrap
    item = post('telegram__before100', 80)['media'][0]
    assert (item['type'], item['width'], item['height'], item['fileId']) == ('gif', 360, 480, '688ccbc236')
    assert item['thumbnailUrl'].startswith('https://cdn1.telesco.pe/file/VtD01HRSwRfTno')
    assert len(post('telegram__before100', 80)['media']) == 1


def test_round_video_voice_audio_documents():
    item = post('tchantest__before21', 17)['media'][0]
    assert (item['type'], item['durationSeconds'], item['fileId']) == ('round_video', 5, '68145c80a1')
    assert item['url'].startswith('https://cdn1.telesco.pe/file/68145c80a1.mp4?token=')
    assert item['thumbnailUrl'].startswith('https://cdn1.telesco.pe/file/j1BEzRC9_g7tiU')

    item = post('tchantest__before21', 13)['media'][0]
    assert (item['type'], item['durationSeconds'], item['fileId']) == ('voice', 1, '29881a3f30')
    assert item['url'].startswith('https://cdn1.telesco.pe/file/29881a3f30.ogg?token=')

    row = post('durov_russia__head', 67)
    item = row['media'][0]
    assert (row['mediaType'], row['mediaUrl']) == ('audio', None)
    assert (item['title'], item['performer'], item['url'], item['unavailable']) == (
        'Свой Живой Интернет', 'durikovich', None, 'no_public_link')
    row = post('tchantest__before21', 12)
    assert row['text'] == 'Povo hebreu'
    assert (row['media'][0]['title'], row['media'][0]['performer']) == ('AUD-20130329-WA0000', 'portadosfundos')
    assert (post('grey_zone__head', 23644)['media'][0]['title'], post('grey_zone__head', 23644)['media'][0]['performer']) == ('Die MF Die', 'Dope')

    item = post('magisk_update__head', 6)['media'][0]
    assert (item['type'], item['fileName'], item['fileSize'], item['url'], item['unavailable']) == (
        'document', 'MagiskStable-27.0_(27000) (1).zip', '11.9 MB', None, 'no_public_link')


def test_document_group_items_carry_their_own_ids():
    row = post('designers__head', 243)
    assert row['mediaType'] == 'album' and row['mediaUrl'] is None
    assert [(m['type'], m['postId'], m['fileName'], m['fileSize']) for m in row['media']] == [
        ('document', 243, 'Features 1.png', '3.9 MB'),
        ('document', 244, 'Features 2.png', '3.7 MB'),
        ('document', 245, 'Features 3.png', '3.8 MB')]
    assert row['replyTo'] == {'postId': 242, 'url': 'https://t.me/designers/242', 'channel': 'designers'}


def test_stickers_webp_tgs_webm():
    item = post('tchantest__before21', 9)['media'][0]
    assert (item['type'], item['fileId']) == ('sticker', '5b5c6e1325')
    assert item['url'].startswith('https://cdn1.telesco.pe/file/5b5c6e1325.webp?token=')   # data-webp, not the SVG background
    assert post('BotNews__before30', 10)['media'][0]['url'].startswith('https://cdn4.telesco.pe/file/e31be46ce9.webp?token=')
    item = post('stickerpacks__head', 7)['media'][0]
    assert item['url'].startswith('https://cdn4.telesco.pe/file/sticker.tgs?token=')
    assert item['fileId'] is None   # "sticker" is not a file key
    item = post('stickerpacks__head', 8)['media'][0]
    assert (item['type'], item['fileId']) == ('sticker', '53d11fee02')
    assert item['url'].startswith('https://cdn4.telesco.pe/file/53d11fee02.webm?token=')
    assert item['thumbnailUrl'].startswith('https://cdn4.telesco.pe/file/dMwn0WB5B0tTYD')


def test_location():
    item = post('tchantest__head', 88)['media'][0]
    assert item['type'] == 'location'
    assert (item['latitude'], item['longitude']) == (-23.531972140424, -46.689076113848)
    assert item['url'] == ('https://maps.google.com/maps?q=-23.531972140424,-46.689076113848'
                           '&ll=-23.531972140424,-46.689076113848&z=16')
    assert item['thumbnailUrl'].startswith('https://static-maps.yandex.ru/1.x/?l=map&ll=-46.689076113848,-23.531972140424')
    item = post('tchantest__before21', 10)['media'][0]
    assert (item['latitude'], item['longitude']) == (-23.930587176407, -44.086305367818)


# --------------------------------------------------------- forwards, previews, polls --
def test_forwarded_four_variants():
    assert post('designers__head', 250)['forwardedFrom'] == {
        'name': 'Telegram Contests', 'url': 'https://t.me/contest/430', 'author': None}
    assert post('grey_zone__head', 23647)['forwardedFrom'] == {
        'name': 'ВЕТЕР', 'url': 'https://t.me/Veter7n39/627', 'author': 'veter'}
    assert post('tchantest__head', 89)['forwardedFrom'] == {'name': 'Troian', 'url': 'https://t.me/rtroian', 'author': None}
    assert post('tchantest__head', 89)['text'] == ';)'
    assert post('telegram__before100', 99)['forwardedFrom'] == {'name': 'Pavel Durov', 'url': None, 'author': None}
    item = post('grey_zone__head', 23647)['media'][0]
    assert (item['width'], item['height'], item['fileId']) == (800, 400, '5229021098769765874')


def test_link_preview_variants():
    lp = post('durov__head', 548)['linkPreview']   # small right image
    assert lp['url'] == 'https://codeforces.com/blog/entry/156620'
    assert (lp['siteName'], lp['title'], lp['description']) == (
        'Codeforces', 'Telegram Returns as Title Sponsor of Codeforces!', 'Hi, Codeforces!')
    assert lp['imageUrl'].startswith('https://cdn4.telesco.pe/file/hEt3cq1km4JQlW') and lp['videoUrl'] is None
    lp = post('durov__head', 538)['linkPreview']   # large image
    assert (lp['url'], lp['siteName'], lp['title']) == ('https://telegram.org/safety', 'Telegram', 'Telegram Safety Overview')
    assert lp['imageUrl'].startswith('https://cdn4.telesco.pe/file/udOVyUWkI3DIjU')
    lp = post('durov__head', 547)['linkPreview']   # no image
    assert (lp['url'], lp['siteName'], lp['title'], lp['imageUrl'], lp['videoUrl']) == (
        'https://t.me/contest/460', 'Telegram', 'Telegram Contests', None, None)
    assert lp['description'].startswith('🏆 Design Contest 2026, Round 2: Results\n\n')
    assert lp['description'].endswith('Thank you all for your efforts \u2013 this steady pace and\u2026')
    row = post('grey_zone__head', 23630)           # video preview of a t.me post
    lp = row['linkPreview']
    assert (lp['url'], lp['siteName'], lp['title']) == ('https://t.me/bayraktar1070/2430', 'Telegram', 'Свидетели Байрактара')
    assert lp['videoUrl'].startswith('https://cdn4.telesco.pe/file/f637e3109d.mp4?token=')
    assert lp['imageUrl'].startswith('https://cdn4.telesco.pe/file/ATv3jtZV5S9dvF')
    assert lp['description'].startswith('Африканский Корпус Министерства обороны') and '\n' in lp['description']
    assert row['media'] == [] and row['links'] == ['https://t.me/bayraktar1070/2430']
    lp = post('tginfo__before4413', 4396)['linkPreview']   # image only
    assert lp['url'] == 'https://img2.teletype.in/files/9b/ed/9bedba58-19e9-434a-965c-7bc1b4612a43.png'
    assert (lp['siteName'], lp['title'], lp['description']) == (None, None, None)
    assert lp['imageUrl'].startswith('https://cdn4.telesco.pe/file/J68M7Nd9ydpWQr')


def test_polls_and_quiz():
    row = post('durov_russia__before51', 33)
    assert row['poll'] == {
        'question': 'Что лучше? 🤔', 'type': 'Anonymous Poll',
        'options': [{'text': 'Полное прекращение работы Telegram в России', 'percent': 20},
                    {'text': 'Приостановка работы агитационных ботов на два дня голосования', 'percent': 80}],
        'voters': 834_000,
    }
    assert row['views'] == 4_920_000 and row['mediaType'] is None
    assert post('tchantest__before21', 11)['poll'] == {
        'question': "Let's do a quick poll quizz mode", 'type': 'Anonymous Quiz',
        'options': [{'text': 'Opt1', 'percent': 100}, {'text': 'Opt2', 'percent': 0}, {'text': 'Opt3', 'percent': 0}],
        'voters': 1,
    }


# ----------------------------------------------------------------------- buttons --
def test_url_buttons_two_by_two_outside_the_bubble():
    assert post('concall_pulse__head', 1722)['buttons'] == [
        {'text': '📞 Join Call', 'row': 1, 'column': 1, 'type': 'url',
         'url': 'https://services.choruscall.in/DiamondPassRegistration/register?confirmationNumber=0613269&linkSecurityString=5555a6408'},
        {'text': '📄 Invite PDF', 'row': 1, 'column': 2, 'type': 'url',
         'url': 'https://nsearchives.nseindia.com/corporate/SHIPROCKET2011_07092026125606_Investor_meet_intimation_revised.pdf'},
        {'text': '📊 Stock Page', 'row': 2, 'column': 1, 'type': 'url', 'url': 'https://earningspulse.ai/stock/SHIPROCKET'},
        {'text': '📊 Last PPT', 'row': 2, 'column': 2, 'type': 'url',
         'url': 'https://nsearchives.nseindia.com/corporate/SHIPROCKET2011_07092026235857_Investor_Presentation.pdf'},
    ]


def test_callback_and_tg_buttons():
    row = post('BotNews__before30', 16)
    assert row['buttons'] == [
        {'text': '👍 4.8K', 'url': None, 'row': 1, 'column': 1, 'type': 'callback'},
        {'text': '😱 1.3K', 'url': None, 'row': 1, 'column': 2, 'type': 'callback'},
        {'text': '🤔 1.4K', 'url': None, 'row': 1, 'column': 3, 'type': 'callback'},
    ]
    assert row['links'] == ['https://t.me/vote', 'https://t.me/like',
                            'https://core.telegram.org/bots#inline-keyboards-and-on-the-fly-updating']
    assert row['reactions'] == [{'type': 'custom', 'emoji': None, 'customEmojiId': '5309832892262654231', 'count': 2}]
    assert (row['linkPreview']['siteName'], row['linkPreview']['title']) == ('core.telegram.org', 'Bots: An introduction for developers')
    row = post('tgbeta__head', 4765)
    assert row['buttons'] == [{'text': 'Subscribe to Telegram Premium', 'url': 'tg://premium_offer?ref=premium',
                               'row': 1, 'column': 1, 'type': 'url'}]
    assert row['forwardedFrom'] == {'name': 'Telegram Premium', 'url': 'https://t.me/premium/144', 'author': None}


# ------------------------------------------------------- service and unsupported --
def test_service_messages():
    expected = {
        ('tchantest__before21', 1): 'Channel created',
        ('tchantest__before21', 2): "Channel name was changed to «tchan's test channel»",
        ('tchantest__before21', 3): 'Channel photo updated',
        ('tchantest__head', 92): "tchan's test channel 👍 pinned «Going to pin this message»",
        ('V_Zelenskiy_official__head', 21013): 'Live stream started',
        ('V_Zelenskiy_official__head', 21014): 'Live stream finished (12 minutes)',
    }
    for (name, pid), text in expected.items():
        row = post(name, pid)
        assert (row['text'], row['isService'], row['views']) == (text, True, None), (name, pid)
    assert post('grey_zone__head', 23649)['text'].startswith('The owner of this channel has been inactive for the last 11 months.')
    assert post('grey_zone__head', 23649)['isService'] is True
    item = post('tchantest__before21', 3)['media'][0]   # the new avatar
    assert (item['type'], item['postId']) == ('photo', 3)
    assert item['url'].startswith('https://cdn1.telesco.pe/file/dgkOLxvxEzo9TP')
    assert post('durov__head', 548)['isService'] is False


def test_textless_service_posts_are_kept():
    for name, pid in (('durov_russia__head', 51), ('durov_russia__before51', 48)):
        row = post(name, pid)
        assert (row['isService'], row['text'], row['textHtml'], row['media'], row['unsupported']) == (True, '', None, [], None)
        assert pid not in page(name).orphans
    assert post('durov_russia__head', 51)['date'] == '2023-11-14T14:04:23+00:00'


def test_unsupported_widget_label():
    row = post('durov_russia__before51', 49)   # giveaway: no text, no media, only the label
    assert row['unsupported'] == 'This media is not supported in the widget'
    assert (row['text'], row['media']) == ('', [])
    assert row['reactions'][0] == {'type': 'emoji', 'emoji': '🤝', 'customEmojiId': None, 'count': 63500}
    assert 49 not in page('durov_russia__before51').orphans


def test_orphan_needs_the_og_fallback():
    p = page('durov_russia__head')
    assert p.orphans == [74]
    row = post('durov_russia__head', 74)
    assert row['unsupported'] == 'Please open Telegram to view this post'
    assert (row['text'], row['textHtml'], row['media'], row['isEdited'], row['views']) == ('', None, [], True, 692_000)
    for name in FEED_PAGES:
        if name != 'durov_russia__head':
            assert page(name).orphans == [], name


# ----------------------------------------------------------------- other pages --
LANDING = {
    'landing_durov': 'restricted',            # a working channel's t.me/<h> page: only /s/ tells them apart
    'landing_qassambrigades': 'restricted',
    'landing_Prigozhin_hat': 'restricted',
    'landing_s_zlibrary_official': 'banned',
    'landing_ru_python': 'is_group',
    'landing_zuck': 'is_user',                 # also says "you can contact" in hidden text
    'landing_BotFather': 'is_bot',
    'landing_thishandledoesnotexist_zz9q': 'not_found',
    'landing_AbCdEf123': 'private_link',
    'landing_du_rov': 'unknown',               # telegram.org home page
    'landing_s_vbuterin': 'ok',
}


@pytest.mark.parametrize('name,expected', sorted(LANDING.items()))
def test_classify_landing(name, expected):
    assert parse.classify_landing(html(name)) == expected


def test_landing_pages_parse_to_zero_posts():
    for name, kind in LANDING.items():
        p = parse.parse_page(html(name))
        assert p.has_channel_info is (kind == 'ok'), name
        if kind != 'ok':
            assert p.posts == [], name


def test_embed_error_reasons():
    assert parse.parse_embed_error(html('landing_embed_qassambrigades_1000')) == \
        "Unfortunately, this channel couldn't be displayed on your device."
    assert parse.parse_embed_error(html('landing_embed_zlibrary_official_10')) == \
        'This channel is unavailable due to copyright infringement.'
    assert parse.parse_embed_error(html('landing_embed_thishandledoesnotexist_zz9q_1')) == \
        'Channel with username @thishandledoesnotexist_zz9q not found'
    assert parse.parse_embed_error(html('landing_embed_durov_99999')) == 'Post not found'
    assert parse.parse_embed_error(html('durov__head')) is None


def test_og_description_and_the_channel_description_guard():
    og = parse.parse_og_description(html('single_contest__462'))
    assert og.startswith('🎆 Congratulations to all the winners!\n')
    assert og.endswith('We expect the payouts to be…')
    assert '$6,250' in og and '&#' not in og
    # textless post: og:description falls back to the channel description, which must match exactly
    assert parse.parse_og_description(html('single_durov_russia__48')) == page('durov_russia__head').channel.description
    for name in FEED_PAGES:
        if page(name).has_channel_info:
            assert parse.parse_og_description(html(name)) == page(name).channel.description, name


@pytest.mark.parametrize('text,expected', [
    ('1.48M', 1_480_000), ('44.1K', 44_100), ('757', 757), ('10 518', 10_518), ('10.7M', 10_700_000),
    ('3.03K', 3_030), ('834K', 834_000), ('9.99M', 9_990_000), ('1.92K', 1_920), ('204 518 subscribers', 204_518),
    ('15 848 members, 3 465 online', 15_848), ('83.1K subscribers', 83_100), ('5 monthly users', 5),
    ('1.2B', 1_200_000_000), ('10\u00a0518', 10_518), ('0', 0), ('', None), (None, None), ('abc', None),
])
def test_parse_count(text, expected):
    assert parse.parse_count(text) == expected


# ------------------------------------------------------------- never crash --
SYNTHETIC = """<html><body><section class="tgme_channel_history">
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="demo/7" data-view="!!!">
 <div class="tgme_widget_message_bubble">
  <a class="tgme_widget_message_reply" href="https://t.me/demo/5" "><div class="tgme_widget_message_text js-message_reply_text">parent</div></a>
  <div class="tgme_widget_message_game"><div class="tgme_widget_message_game_title">Some game</div></div>
  <a class="tgme_widget_message_photo_wrap 123 4_5" style="width:abcpx;background-image:url(" href="https://t.me/demo/7"></a>
  <a class="tgme_widget_message_location_wrap" href="https://maps.google.com/maps?q=12.5,77.25">
   <div class="tgme_widget_message_location_info"><div class="tgme_widget_message_location_title">Cafe</div><div class="tgme_widget_message_location_address">MG Road</div></div></a>
  <div class="tgme_widget_message_contact_wrap"><div class="tgme_widget_message_contact"><div class="tgme_widget_message_contact_name">Ann</div><div class="tgme_widget_message_contact_phone">+1 555</div></div></div>
  <div class="tgme_widget_message_text js-message_text">hello<!-- a comment --> world</div>
  <div class="tgme_widget_message_footer"><span class="tgme_widget_message_meta"><a class="tgme_widget_message_date" href="https://t.me/demo/7"><time datetime="not a date">x</time></a></span></div>
 </div></div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="demo/7"></div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="demo/notanumber"></div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message_centered"><div class="tme_no_messages_found">No posts found</div></div></div>
</section></body></html>"""


def test_unknown_and_broken_markup_degrades_to_a_row():
    p = parse.parse_page(SYNTHETIC)
    assert [r['postId'] for r in p.posts] == [7]   # duplicate dropped, id-less post skipped
    row = p.posts[0]
    assert (row['channel'], row['postUrl'], row['channelId'], row['date']) == ('demo', 'https://t.me/demo/7', None, None)
    assert row['text'] == 'hello world'
    assert row['replyTo'] == {'postId': 5, 'url': 'https://t.me/demo/5', 'channel': 'demo'}
    photo, venue, contact = row['media']
    assert (photo['type'], photo['url'], photo['width'], photo['fileId']) == ('photo', None, None, '123')
    assert (venue['type'], venue['title'], venue['address'], venue['latitude'], venue['longitude']) == (
        'venue', 'Cafe', 'MG Road', 12.5, 77.25)
    assert (contact['type'], contact['name'], contact['phone']) == ('contact', 'Ann', '+1 555')
    assert row['mediaType'] == 'album'
    assert row['scrapedAt'].endswith('+00:00')


def test_a_failing_field_costs_only_that_field(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError('markup changed')
    monkeypatch.setattr(parse, '_reactions', boom)
    row = next(r for r in parse.parse_page(html('durov__head')).posts if r['postId'] == 548)
    assert row['reactions'] == []
    assert row['text'].startswith('🤝 Telegram has become the sponsor') and row['views'] == 1_480_000


def test_a_failing_post_still_becomes_a_minimal_row(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError('markup changed')
    monkeypatch.setattr(parse, '_Index', boom)
    p = parse.parse_page(html('durov__head'))
    assert [r['postId'] for r in p.posts] == [528, 529] + list(range(531, 549))
    row = p.posts[-1]
    assert (row['channel'], row['postUrl'], row['channelId'], row['text']) == ('durov', 'https://t.me/durov/548', 1006503122, '')
    assert list(row) == list(parse.ROW_KEYS)


def test_empty_or_garbage_input():
    for junk in ('', '   ', None, '<?xml version="1.0" encoding="utf-8"?><html><body>x</body></html>'):
        p = parse.parse_page(junk)
        assert (p.posts, p.has_channel_info, p.before, p.after, p.orphans) == ([], False, None, None, []), junk
    assert parse.classify_landing('') == 'unknown'
    assert parse.parse_og_description('') is None and parse.parse_embed_error('') is None


def test_player_without_a_file_reports_its_label():
    # Defensive: a round video with no src. Its "not supported in your browser" label then matters.
    html_text = SYNTHETIC.replace(
        '<div class="tgme_widget_message_text js-message_text">hello<!-- a comment --> world</div>',
        '<div class="tgme_widget_message_roundvideo_player"><div class="message_media_not_supported_wrap">'
        '<div class="message_media_not_supported_label">This media is not supported in your browser</div></div></div>')
    row = parse.parse_page(html_text).posts[0]
    item = row['media'][-1]
    assert (item['type'], item['url'], item['unavailable']) == ('round_video', None, 'no_public_link')
    assert row['unsupported'] == 'This media is not supported in your browser'
    assert row['text'] == '' and parse.parse_page(html_text).orphans == []
