"""
Parser for Telegram's public channel preview pages (https://t.me/s/<channel>).

Pure functions: HTML in, rows out. No network, no Apify SDK. Built on lxml, not bs4:
the platform's default 256 MB gives the actor 1/16 of a CPU core, so parse time is
throughput. Each post is walked once into a class-token index; running one XPath per
field instead cost 12 ms a page, the index brings it to a few ms.

Every rule below comes from real pages saved on 2026-09-23 (recon/html-catalog.md),
not from guesses. The traps it handles, in the order they bite:

- A post is div.tgme_widget_message[data-post]. The "No posts found" block is also a
  message wrap, but it has no data-post, so it never becomes a row.
- data-post can name a different slug than the one requested (t.me/s/stickerpacks
  renders data-post="stickerpack/7"). The row's channel is the data-post slug.
- data-view is base64url JSON without padding: {"c": -<channel id>, "p", "t", "h"}.
  It gives a numeric channel id that survives username changes, and "t" is the
  server's render time, used as scrapedAt.
- Posts with custom emoji carry their content twice: div.media_supported_cont (shown)
  and div.media_not_supported_cont ("Please open Telegram to view this post", hidden
  by Telegram's CSS). The walk never enters the fallback, so its placeholder cannot
  leak into the text or the unsupported field.
- The reply quote is a tgme_widget_message_text too (js-message_reply_text), and the
  link preview holds its own image and video. Both are read on their own and kept out
  of the post's index, so the body text and media never pick them up.
- Album captions are wrapped twice; the innermost js-message_text wins.
- The inline keyboard sits outside the bubble, so the whole message is indexed.
- Text links (the ones with onclick="return confirm(...)") are HTML-escaped twice
  (&amp;amp;). Inline buttons and every other link are escaped once.
- Class names are matched as whole tokens ("js-message_video_player" is not
  "message_video_play").
- A post whose only content is the bare "Please open Telegram to view this post"
  placeholder is an orphan: its text exists only in the og:description of
  t.me/<channel>/<id>. The runtime fetches that; this module only reports the ids.

Nothing on a page may crash the run: every field is read under a guard, and a post
whose markup is unrecognisable still becomes a row with whatever could be read.
"""
import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import lxml.html
from lxml import etree
from lxml.cssselect import CSSSelector

PLACEHOLDER_ORPHAN = 'open telegram to view this post'   # lowercased stem of the orphan label
BROWSER_FALLBACK = 'not supported in your browser'        # shown inside every player, even with a file
UNAVAILABLE_TOO_BIG = 'too_big'                           # video with no file anywhere, only a thumbnail
UNAVAILABLE_NO_LINK = 'no_public_link'                    # documents and audio never expose a file link

# Subtrees that are never the post's own content. The index records them and does not enter.
FALLBACK = 'media_not_supported_cont'
REPLY = 'tgme_widget_message_reply'
LINK_PREVIEW = 'tgme_widget_message_link_preview'
SKIP_SUBTREES = {FALLBACK, REPLY, LINK_PREVIEW}

# Order of a row's keys = the dataset's column order. Every key is always present.
ROW_KEYS = (
    'channel', 'channelId', 'channelTitle', 'channelSubscribers', 'postId', 'postUrl', 'date',
    'text', 'textHtml', 'textTruncated', 'author', 'isEdited', 'isService', 'viaBot', 'views',
    'reactions', 'forwardedFrom', 'replyTo', 'links', 'buttons', 'linkPreview', 'mediaType',
    'mediaUrl', 'media', 'poll', 'unsupported', 'scrapedAt',
)


# --------------------------------------------------------------------- selectors --
# Page-level and small-subtree lookups. Compiled once; CSSSelector matches class tokens
# exactly, and (careful) also matches the element it runs on.
S_MESSAGE = CSSSelector('div.tgme_widget_message')
S_CHANNEL_INFO = CSSSelector('div.tgme_channel_info')
S_CH_TITLE = CSSSelector('.tgme_channel_info_header_title')
S_CH_USERNAME = CSSSelector('.tgme_channel_info_header_username')
S_CH_COUNTER = CSSSelector('.tgme_channel_info_counter')
S_CH_COUNTER_VALUE = CSSSelector('.counter_value')
S_CH_COUNTER_TYPE = CSSSelector('.counter_type')
S_CH_DESCRIPTION = CSSSelector('.tgme_channel_info_description')
S_HEADER_COUNTER = CSSSelector('.tgme_header_counter')
S_MORE = CSSSelector('a.tme_messages_more')

S_FORWARDED_NAME = CSSSelector('.tgme_widget_message_forwarded_from_name')
S_FORWARDED_AUTHOR = CSSSelector('.tgme_widget_message_forwarded_from_author')
S_BUTTON = CSSSelector('.tgme_widget_message_inline_button')
S_BUTTON_TEXT = CSSSelector('.tgme_widget_message_inline_button_text')
S_LABEL = CSSSelector('.message_media_not_supported_label')
S_POLL_QUESTION = CSSSelector('.tgme_widget_message_poll_question')
S_POLL_TYPE = CSSSelector('.tgme_widget_message_poll_type')
S_POLL_OPTION = CSSSelector('.tgme_widget_message_poll_option')
S_POLL_OPTION_TEXT = CSSSelector('.tgme_widget_message_poll_option_text')
S_POLL_OPTION_PERCENT = CSSSelector('.tgme_widget_message_poll_option_percent')
S_LP_SITE = CSSSelector('.link_preview_site_name')
S_LP_TITLE = CSSSelector('.link_preview_title')
S_LP_DESCRIPTION = CSSSelector('.link_preview_description')
S_LP_IMAGE = CSSSelector('i.link_preview_image, i.link_preview_right_image, .link_preview_video_thumb')
S_LP_VIDEO = CSSSelector('video.link_preview_video, .link_preview_video_player video')

S_PHOTO_INNER = CSSSelector('.tgme_widget_message_photo')
S_VIDEO_WRAP = CSSSelector('.tgme_widget_message_video_wrap')
S_VIDEO_IN_WRAP = CSSSelector('.tgme_widget_message_video_wrap video')
S_VIDEO_ANY = CSSSelector('video')
S_VIDEO_THUMB = CSSSelector('.tgme_widget_message_video_thumb')
S_VIDEO_PLAY = CSSSelector('.message_video_play')
S_VIDEO_DURATION = CSSSelector('.message_video_duration')
S_ROUND_THUMB = CSSSelector('.tgme_widget_message_roundvideo_thumb')
S_ROUND_DURATION = CSSSelector('.tgme_widget_message_roundvideo_duration')
S_VOICE_DURATION = CSSSelector('.tgme_widget_message_voice_duration')
S_DOC_ICON = CSSSelector('.tgme_widget_message_document_icon')
S_DOC_TITLE = CSSSelector('.tgme_widget_message_document_title')
S_DOC_EXTRA = CSSSelector('.tgme_widget_message_document_extra')
S_STICKER_WEBP = CSSSelector('.tgme_widget_message_sticker')
S_STICKER_TGS = CSSSelector('.tgme_widget_message_tgsticker source')
S_LOCATION_MAP = CSSSelector('.tgme_widget_message_location')
S_LOCATION_TITLE = CSSSelector('.tgme_widget_message_location_title')
S_LOCATION_ADDRESS = CSSSelector('.tgme_widget_message_location_address')
S_CONTACT_NAME = CSSSelector('.tgme_widget_message_contact_name')
S_CONTACT_PHONE = CSSSelector('.tgme_widget_message_contact_phone')

# Landing page (t.me/<handle>) and embed (t.me/<handle>/<id>?embed=1&mode=tme).
S_PAGE_EXTRA = CSSSelector('.tgme_page_extra')
S_PAGE_DESCRIPTION = CSSSelector('.tgme_page_description')
S_PAGE_TITLE = CSSSelector('div.tgme_page_title')
S_PAGE_BUTTON = CSSSelector('a.tgme_action_button_new, .tgme_page_action a')
S_EMBED_ERROR = CSSSelector('div.tgme_widget_message_error')

WIDTH_RE = re.compile(r'(?<![-\w])width\s*:\s*([\d.]+)px')
PADDING_TOP_RE = re.compile(r'padding-top\s*:\s*([\d.]+)%')
BG_IMAGE_RE = re.compile(r'background-image\s*:\s*url\(\s*([\'"]?)(.*?)\1\s*\)')
FILE_KEY_RE = re.compile(r'/file/([0-9a-f]{8,})\.[A-Za-z0-9]+(?:[?#]|$)')
POST_LINK_RE = re.compile(r'^https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(?:s/)?([A-Za-z0-9_]+)/(\d+)')
# Uppercase suffix only, and not the start of a word: '15 848 members' is not 15848 million.
COUNT_RE = re.compile(r'^(\d+(?:[.,]\d+)?)(?:\s*([KMB])(?![A-Za-z]))?')
SPACE_IN_NUMBER_RE = re.compile(r'(?<=\d)[\s\u00a0\u202f\u2009]+(?=\d)')
DURATION_RE = re.compile(r'^(?:(\d+):)?(\d{1,2}):(\d{2})$')


@dataclass
class Channel:
    handle: str | None
    id: int | None
    title: str | None
    subscribers: int | None
    description: str | None   # plain text; the runtime compares og:description against it


@dataclass
class Page:
    channel: Channel
    posts: list[dict]
    before: int | None        # next cursor for walking back in time, straight from the page
    after: int | None
    orphans: list[int]        # post ids that need the og:description fallback
    has_channel_info: bool


# ----------------------------------------------------------------------- helpers --
def _guard(fn, default=None):
    """Run one field extractor; a surprise in the markup costs that field, not the post."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _classes(el) -> list[str]:
    return (el.get('class') or '').split()


def _first(selector, el):
    found = selector(el)
    return found[0] if found else None


def _document_root(html):
    """Parse a page leniently. Empty input, or text that declares its own encoding, must not raise."""
    for source in (html, html.encode('utf-8') if isinstance(html, str) else None):
        if source:
            try:
                return lxml.html.fromstring(source)
            except (ValueError, etree.ParserError):
                continue
    return lxml.html.fromstring('<html></html>')


class _Index:
    """Class token -> elements of one post, in page order, from a single walk.

    The walk does not enter the hidden fallback, the reply quote or the link preview;
    their roots are kept in `skipped` so they can be read on their own.
    """
    __slots__ = ('by_class', 'order', 'skipped')

    def __init__(self, root):
        self.by_class: dict[str, list] = {}
        self.order: list[tuple] = []        # (element, class tokens) in page order
        self.skipped: dict[str, list] = {}
        stack = [root]
        while stack:
            el = stack.pop()
            tokens = (el.get('class') or '').split()
            if el is not root:
                hit = SKIP_SUBTREES.intersection(tokens)
                if hit:
                    self.skipped.setdefault(hit.pop(), []).append(el)
                    continue
            if tokens:
                self.order.append((el, tokens))
                for t in tokens:
                    self.by_class.setdefault(t, []).append(el)
            stack.extend(child for child in reversed(el) if isinstance(child.tag, str))

    def all(self, token: str) -> list:
        return self.by_class.get(token, [])

    def first(self, token: str):
        found = self.by_class.get(token)
        return found[0] if found else None


def _text(el) -> str:
    """Plain text the way a reader sees it: emoji kept, <br> as a newline, &nbsp; as a space."""
    if el is None:
        return ''
    parts: list[str] = []

    def walk(node):
        if node.tag == 'br':
            parts.append('\n')
            return
        if node.text and isinstance(node.tag, str):
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail:
                parts.append(child.tail)   # comments have no text of their own, but keep what follows them

    walk(el)
    return ''.join(parts).replace('\xa0', ' ').strip()


def _one_line(el) -> str | None:
    """Short labels (names, titles): whitespace collapsed, None when empty."""
    if el is None:
        return None
    value = ' '.join(_text(el).split())
    return value or None


def _inner_html(el) -> str:
    outer = lxml.html.tostring(el, encoding='unicode', with_tail=False)
    start, end = outer.find('>') + 1, outer.rfind('</')
    return outer[start:end].strip() if 0 < start <= end else ''


def _abs_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith('//'):
        url = 'https:' + url
    if url.startswith('data:'):
        return None   # generated placeholders (SVG avatars, sticker shimmer), never a file
    return url or None


def _style_bg(el) -> str | None:
    if el is None:
        return None
    m = BG_IMAGE_RE.search(el.get('style') or '')
    return _abs_url(m.group(2)) if m else None


def _style_width(el) -> int | None:
    m = WIDTH_RE.search(el.get('style') or '') if el is not None else None
    return round(float(m.group(1))) if m else None


def _style_padding(el) -> float | None:
    m = PADDING_TOP_RE.search(el.get('style') or '') if el is not None else None
    return float(m.group(1)) if m else None


def _height(width: int | None, padding_pct: float | None) -> int | None:
    """Telegram sizes the box with padding-top as a percentage of the width."""
    if width is None or padding_pct is None:
        return None
    return round(width * padding_pct / 100)


def _file_key(url: str | None) -> str | None:
    """The hex name of a video/voice/sticker file: stable while the ?token= rotates."""
    m = FILE_KEY_RE.search(url or '')
    return m.group(1) if m else None


def _duration(el) -> int | None:
    m = DURATION_RE.match(_text(el)) if el is not None else None
    if not m:
        return None
    hours, minutes, seconds = m.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def _item_id(el, post_id: int) -> int:
    """Album and document-group items link to https://t.me/<ch>/<id>?single: that is their own id."""
    href = el.get('href') or ''
    m = POST_LINK_RE.match(href) if '?single' in href else None
    return int(m.group(2)) if m else post_id


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds')


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_count(text: str | None) -> int | None:
    """Telegram's abbreviated counters: '1.48M' -> 1480000, '44.1K' -> 44100, '10 518' -> 10518."""
    if text is None:
        return None
    s = SPACE_IN_NUMBER_RE.sub('', str(text).strip())   # '10 518' -> '10518'
    m = COUNT_RE.match(s)
    if not m:
        return None
    number, suffix = m.group(1), m.group(2) or ''
    value = float(number.replace(',', '.')) if suffix else float(number.replace(',', ''))
    return round(value * {'': 1, 'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}[suffix])


def _decode_view(data_view: str | None) -> dict:
    """data-view is base64url JSON with the padding stripped."""
    if not data_view:
        return {}
    try:
        raw = base64.urlsafe_b64decode(data_view + '=' * (-len(data_view) % 4))
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _link_target(href: str | None) -> tuple[str | None, int | None]:
    """(channel, post id) from a t.me post link. Private-channel links (t.me/c/<id>/<n>) have no handle."""
    m = POST_LINK_RE.match(href or '')
    if not m:
        return None, None
    handle = m.group(1)
    return (None if handle == 'c' else handle), int(m.group(2))


# ----------------------------------------------------------------------- channel --
def _parse_channel(doc) -> Channel:
    info = _first(S_CHANNEL_INFO, doc)
    title = handle = description = subscribers = None
    if info is not None:
        title = _one_line(_first(S_CH_TITLE, info))
        handle_text = _one_line(_first(S_CH_USERNAME, info))
        handle = handle_text.lstrip('@') if handle_text else None
        for counter in S_CH_COUNTER(info):
            kind = _one_line(_first(S_CH_COUNTER_TYPE, counter)) or ''
            if kind.lower().startswith('subscriber'):
                subscribers = parse_count(_text(_first(S_CH_COUNTER_VALUE, counter)))
        desc_el = _first(S_CH_DESCRIPTION, info)
        description = (_text(desc_el) or None) if desc_el is not None else None
    if subscribers is None:
        header = _one_line(_first(S_HEADER_COUNTER, doc)) or ''
        if 'subscriber' in header.lower():
            subscribers = parse_count(header)
    if title is None:
        og = doc.xpath('//meta[@property="og:title"]/@content')
        title = (og[0].strip() or None) if og else None
    return Channel(handle=handle, id=None, title=title, subscribers=subscribers, description=description)


def _cursor(doc, more_links: list, attr: str, rel: str) -> int | None:
    """Telegram's own cursor. Never computed from post ids: before=0 silently returns the newest page."""
    for a in more_links:
        value = _int(a.get(attr))
        if value is not None:
            return value
    for href in doc.xpath(f'//link[@rel="{rel}"]/@href'):
        values = parse_qs(urlparse(href).query).get(attr.replace('data-', ''))
        value = _int(values[0]) if values else None
        if value is not None:
            return value
    return None


# ------------------------------------------------------------------------- media --
def _media_item(kind: str, post_id: int, **fields) -> dict:
    item = {
        'type': kind, 'url': None, 'thumbnailUrl': None, 'width': None, 'height': None,
        'durationSeconds': None, 'fileName': None, 'fileSize': None, 'fileId': None,
        'postId': post_id, 'unavailable': None,
    }
    item.update(fields)
    return item


def _photo(el, post_id: int) -> dict:
    classes = _classes(el)
    if 'grouped_media_wrap' in classes:
        # Album item: its style holds layout pixels, not the photo's size, and it has no id classes.
        return _media_item('photo', _item_id(el, post_id), url=_style_bg(el))
    width = _style_width(el)
    ids = [c for c in classes if c.isdigit()]
    return _media_item(
        'photo', post_id, url=_style_bg(el), width=width,
        height=_height(width, _style_padding(_first(S_PHOTO_INNER, el))),
        fileId=ids[0] if ids else None,
    )


def _video(el, post_id: int) -> dict:
    grouped = 'grouped_media_wrap' in _classes(el)
    video = _first(S_VIDEO_IN_WRAP, el)
    if video is None:
        video = next((v for v in S_VIDEO_ANY(el) if 'js-message_video_blured' not in _classes(v)), None)
    url = _abs_url(video.get('src')) if video is not None else None
    # A GIF is the same player with no play button and no duration.
    kind = 'video' if S_VIDEO_PLAY(el) or S_VIDEO_DURATION(el) else 'gif'
    wrap = _first(S_VIDEO_WRAP, el)
    width = None if grouped else _style_width(wrap)
    item = _media_item(
        kind, _item_id(el, post_id), url=url, thumbnailUrl=_style_bg(_first(S_VIDEO_THUMB, el)),
        width=width, height=_height(width, _style_padding(wrap)),
        durationSeconds=_duration(_first(S_VIDEO_DURATION, el)), fileId=_file_key(url),
    )
    if url is None:
        label = (_one_line(_first(S_LABEL, el)) or '').lower()
        too_big = 'too big' in label or 'not_supported' in _classes(el)
        item['unavailable'] = UNAVAILABLE_TOO_BIG if too_big else UNAVAILABLE_NO_LINK
    return item


def _round_video(el, post_id: int) -> dict:
    video = el.find('.//video')
    url = _abs_url(video.get('src')) if video is not None else None
    return _media_item(
        'round_video', post_id, url=url, thumbnailUrl=_style_bg(_first(S_ROUND_THUMB, el)),
        durationSeconds=_duration(_first(S_ROUND_DURATION, el)), fileId=_file_key(url),
        unavailable=None if url else UNAVAILABLE_NO_LINK,
    )


def _voice(el, post_id: int) -> dict:
    audio = el.find('.//audio')
    url = _abs_url(audio.get('src')) if audio is not None else None
    return _media_item(
        'voice', post_id, url=url, durationSeconds=_duration(_first(S_VOICE_DURATION, el)),
        fileId=_file_key(url), unavailable=None if url else UNAVAILABLE_NO_LINK,
    )


def _document(el, post_id: int) -> dict:
    icon = _first(S_DOC_ICON, el)
    title = _one_line(_first(S_DOC_TITLE, el))
    extra = _one_line(_first(S_DOC_EXTRA, el))
    if icon is not None and 'audio' in _classes(icon):
        # Music file: title and performer only. No link, size or duration in any no-login view.
        return _media_item('audio', _item_id(el, post_id), unavailable=UNAVAILABLE_NO_LINK,
                           title=title, performer=extra)
    return _media_item('document', _item_id(el, post_id), fileName=title, fileSize=extra,
                       unavailable=UNAVAILABLE_NO_LINK)


def _sticker(el, post_id: int) -> dict:
    url = thumb = None
    webp = _first(S_STICKER_WEBP, el)
    if webp is not None:
        url = _abs_url(webp.get('data-webp'))   # the background-image is only an SVG shimmer
    if url is None:
        for source in S_STICKER_TGS(el):
            if 'tgsticker' in (source.get('type') or ''):
                url = _abs_url((source.get('srcset') or '').split(' ')[0])
                break
    if url is None:
        video = el.find('.//video')
        if video is not None:
            url = _abs_url(video.get('src'))
            poster = video.find('.//img')
            thumb = _abs_url(poster.get('src')) if poster is not None else None
    return _media_item('sticker', post_id, url=url, thumbnailUrl=thumb, fileId=_file_key(url),
                       unavailable=None if url else UNAVAILABLE_NO_LINK)


def _location(el, post_id: int) -> dict:
    href = _abs_url(el.get('href'))
    lat = lon = None
    q = parse_qs(urlparse(href or '').query).get('q')
    parts = q[0].split(',') if q else []
    if len(parts) == 2:
        lat, lon = _guard(lambda: float(parts[0])), _guard(lambda: float(parts[1]))
    fields = dict(url=href, thumbnailUrl=_style_bg(_first(S_LOCATION_MAP, el)), latitude=lat, longitude=lon)
    title = _one_line(_first(S_LOCATION_TITLE, el))
    if title is not None:
        # Venue: no live example exists; the shape comes from Telegram's widget CSS.
        return _media_item('venue', post_id, title=title,
                           address=_one_line(_first(S_LOCATION_ADDRESS, el)), **fields)
    return _media_item('location', post_id, **fields)


def _contact(el, post_id: int) -> dict:
    # No live example exists; the shape comes from Telegram's widget CSS.
    return _media_item('contact', post_id, name=_one_line(_first(S_CONTACT_NAME, el)),
                       phone=_one_line(_first(S_CONTACT_PHONE, el)))


def _service_photo(el, post_id: int) -> dict:
    img = el.find('.//img')   # "Channel photo updated": the new avatar
    return _media_item('photo', post_id, url=_abs_url(img.get('src')) if img is not None else None)


MEDIA_READERS = {
    'tgme_widget_message_photo_wrap': _photo,
    'tgme_widget_message_video_player': _video,
    'tgme_widget_message_roundvideo_player': _round_video,
    'tgme_widget_message_voice_player': _voice,
    'tgme_widget_message_document_wrap': _document,
    'tgme_widget_message_sticker_wrap': _sticker,
    'tgme_widget_message_location_wrap': _location,
    'tgme_widget_message_contact_wrap': _contact,
    'tgme_widget_message_service_photo': _service_photo,
}


def _media(ix: _Index, post_id: int) -> tuple[list[dict], dict]:
    """All media items in page order, plus a map from each block element to its item."""
    items, by_element = [], {}
    for el, tokens in ix.order:
        reader = next((MEDIA_READERS[t] for t in tokens if t in MEDIA_READERS), None)
        if reader is None:
            continue
        item = _guard(lambda: reader(el, post_id))
        if item is not None:
            items.append(item)
            by_element[el] = item
    return items, by_element


# ---------------------------------------------------------------------- sections --
def _reply(el) -> dict:
    href = _abs_url(el.get('href'))
    channel, post_id = _link_target(href)
    return {'postId': post_id, 'url': href, 'channel': channel}


def _forwarded(el) -> dict:
    name_el = _first(S_FORWARDED_NAME, el)
    if name_el is not None:
        name = _one_line(name_el)
        url = _abs_url(name_el.get('href')) if name_el.tag == 'a' else None   # hidden senders have no link
    else:
        name = (_one_line(el) or '').replace('Forwarded from', '').strip() or None
        url = None
    return {'name': name, 'url': url, 'author': _one_line(_first(S_FORWARDED_AUTHOR, el))}


def _link_preview(el) -> dict:
    video = _first(S_LP_VIDEO, el)
    return {
        'url': _abs_url(el.get('href')),
        'siteName': _one_line(_first(S_LP_SITE, el)),
        'title': _one_line(_first(S_LP_TITLE, el)),
        'description': _text(_first(S_LP_DESCRIPTION, el)) or None,
        'imageUrl': next((u for u in (_style_bg(i) for i in S_LP_IMAGE(el)) if u), None),
        'videoUrl': _abs_url(video.get('src')) if video is not None else None,
    }


def _body(ix: _Index):
    """The post's own text div: innermost js-message_text (albums wrap the caption twice)."""
    candidates = [el for el in ix.all('js-message_text') if 'tgme_widget_message_text' in _classes(el)]
    inner = [c for c in candidates if not any(o is not c and c in o.iterancestors() for o in candidates)]
    return inner[0] if inner else None


def _links(body) -> list[str]:
    out: list[str] = []
    for a in body.iter('a'):
        href = (a.get('href') or '').strip()
        if a.get('onclick') is not None:
            href = href.replace('&amp;', '&')   # text links are escaped twice by Telegram
        if href.startswith('//'):
            href = 'https:' + href
        scheme = urlparse(href).scheme.lower()
        if not scheme or scheme in ('javascript', 'data'):
            continue   # relative hashtag links (?q=%23tag) and anything that is not a real target
        if href not in out:
            out.append(href)
    return out


def _reactions(ix: _Index) -> list[dict]:
    out = []
    for span in ix.all('tgme_reaction'):
        count = parse_count(((span.text or '') + ''.join(child.tail or '' for child in span)).strip())
        if 'tgme_reaction_paid' in _classes(span):
            out.append({'type': 'paid', 'emoji': None, 'customEmojiId': None, 'count': count})
            continue
        custom = span.find('.//tg-emoji')
        if custom is not None:
            # Custom emoji reactions carry only an id, no fallback character.
            out.append({'type': 'custom', 'emoji': _text(custom) or None,
                        'customEmojiId': custom.get('emoji-id'), 'count': count})
            continue
        emoji = span.find('.//b')
        out.append({'type': 'emoji', 'emoji': (_text(emoji) or None) if emoji is not None else None,
                    'customEmojiId': None, 'count': count})
    return out


def _buttons(ix: _Index) -> list[dict]:
    out = []
    for r, row in enumerate(ix.all('tgme_widget_message_inline_row'), start=1):
        for c, button in enumerate(S_BUTTON(row), start=1):
            is_url = 'url_button' in _classes(button)
            label = _first(S_BUTTON_TEXT, button)
            out.append({
                'text': _text(label if label is not None else button),
                # Callback buttons link to the post itself; only url buttons have a real target.
                'url': _abs_url(button.get('href')) if is_url else None,
                'row': r, 'column': c,
                'type': 'url' if is_url else 'callback',
            })
    return out


def _poll(el, ix: _Index) -> dict:
    options = []
    for opt in S_POLL_OPTION(el):
        percent = _first(S_POLL_OPTION_PERCENT, opt)
        options.append({
            'text': _text(_first(S_POLL_OPTION_TEXT, opt)),
            'percent': parse_count(_text(percent).rstrip('%')) if percent is not None else None,
        })
    voters = ix.first('tgme_widget_message_voters')
    return {
        'question': _text(_first(S_POLL_QUESTION, el)),
        'type': _one_line(_first(S_POLL_TYPE, el)),
        'options': options,
        'voters': parse_count(_text(voters)) if voters is not None else None,
    }


def _is_edited(ix: _Index) -> bool:
    meta = ix.first('tgme_widget_message_meta')
    if meta is None:
        return False
    # Only the meta's own text nodes: a signature that reads "edited" must not count.
    own = (meta.text or '') + ''.join(child.tail or '' for child in meta)
    return 'edited' in own.lower()


def _unsupported(ix: _Index, media_by_element: dict) -> str | None:
    """Telegram's own label for content the web preview cannot show."""
    for label in ix.all('message_media_not_supported_label'):
        text = _one_line(label)
        if not text:
            continue
        holder = next((a for a in label.iterancestors() if a in media_by_element), None)
        if holder is not None and media_by_element[holder].get('url'):
            continue   # "not supported in your browser" inside a player that has its file
        if holder is None and BROWSER_FALLBACK in text.lower():
            continue
        return text
    return None


# ----------------------------------------------------------------------- message --
def _empty_row() -> dict:
    row = dict.fromkeys(ROW_KEYS)
    row.update(text='', textTruncated=False, isEdited=False, isService=False,
               reactions=[], links=[], buttons=[], media=[])
    return row


def _post_id(msg, channel: Channel) -> tuple[str | None, int | None]:
    slug, _, raw_id = (msg.get('data-post') or '').partition('/')
    return slug or channel.handle, _int(raw_id)


def _parse_message(msg, channel: Channel) -> tuple[dict, bool] | None:
    """One post -> (row, is_orphan). None only when the post has no id at all."""
    slug, post_id = _post_id(msg, channel)
    if post_id is None:
        return None
    view = _decode_view(msg.get('data-view'))
    ix = _Index(msg)

    row = _empty_row()
    row['channel'] = slug
    row['channelId'] = abs(view['c']) if isinstance(view.get('c'), int) else channel.id
    row['channelTitle'] = channel.title or _guard(lambda: _one_line(ix.first('tgme_widget_message_owner_name')))
    row['channelSubscribers'] = channel.subscribers
    row['postId'] = post_id
    row['postUrl'] = f'https://t.me/{slug}/{post_id}' if slug else None
    row['date'] = _guard(lambda: _iso(ix.first('tgme_widget_message_date').find('.//time').get('datetime')))
    row['isService'] = 'service_message' in _classes(msg)
    row['author'] = _guard(lambda: _one_line(ix.first('tgme_widget_message_from_author')))
    row['isEdited'] = _guard(lambda: _is_edited(ix), False)
    row['viaBot'] = _guard(lambda: _one_line(ix.first('tgme_widget_message_via_bot')))
    views = ix.first('tgme_widget_message_views')
    row['views'] = _guard(lambda: parse_count(_text(views))) if views is not None else None

    forwarded = ix.first('tgme_widget_message_forwarded_from')
    if forwarded is not None:
        row['forwardedFrom'] = _guard(lambda: _forwarded(forwarded))
    replies = ix.skipped.get(REPLY)
    if replies:
        row['replyTo'] = _guard(lambda: _reply(replies[0]))

    body = _guard(lambda: _body(ix))
    if body is not None:
        etree.strip_tags(body, 'mark')   # search-result highlighting, not part of the post
        row['text'] = _guard(lambda: _text(body), '')
        row['textHtml'] = _guard(lambda: _inner_html(body))
        row['links'] = _guard(lambda: _links(body), [])

    row['reactions'] = _guard(lambda: _reactions(ix), [])
    row['buttons'] = _guard(lambda: _buttons(ix), [])
    previews = ix.skipped.get(LINK_PREVIEW)
    if previews:
        row['linkPreview'] = _guard(lambda: _link_preview(previews[0]))

    media, media_by_element = _guard(lambda: _media(ix, post_id), ([], {}))
    row['media'] = media
    if media:
        row['mediaType'] = 'album' if len(media) > 1 else media[0]['type']
        row['mediaUrl'] = media[0]['url']
    poll = ix.first('tgme_widget_message_poll')
    if poll is not None:
        row['poll'] = _guard(lambda: _poll(poll, ix))
    row['unsupported'] = _guard(lambda: _unsupported(ix, media_by_element))
    row['scrapedAt'] = (_guard(lambda: datetime.fromtimestamp(view['t'], timezone.utc).isoformat(timespec='seconds'))
                        if isinstance(view.get('t'), int) else None) or _now_iso()

    is_orphan = bool(
        row['unsupported'] and PLACEHOLDER_ORPHAN in row['unsupported'].lower()
        and not row['text'] and not media and row['poll'] is None
    )
    return row, is_orphan


def _fallback_row(msg, channel: Channel) -> tuple[dict, bool] | None:
    """Last resort when a post's markup broke the normal path: id and link, nothing else."""
    slug, post_id = _post_id(msg, channel)
    if post_id is None:
        return None
    row = _empty_row()
    row.update(
        channel=slug, channelId=channel.id, channelTitle=channel.title,
        channelSubscribers=channel.subscribers, postId=post_id,
        postUrl=f'https://t.me/{slug}/{post_id}' if slug else None,
        isService='service_message' in _classes(msg), scrapedAt=_now_iso(),
    )
    return row, False


# -------------------------------------------------------------------------- page --
def parse_page(html: str) -> Page:
    """One t.me/s/<channel> page -> channel header, rows (ascending by post id), cursors, orphans."""
    doc = _document_root(html)
    channel = _guard(lambda: _parse_channel(doc), Channel(None, None, None, None, None))
    messages = [m for m in S_MESSAGE(doc) if m.get('data-post')]
    for m in messages:
        c = _decode_view(m.get('data-view')).get('c')
        if isinstance(c, int):
            channel.id = abs(c)
            break

    rows: dict[int, dict] = {}
    orphans: set[int] = set()
    for msg in messages:
        try:
            parsed = _parse_message(msg, channel)
        except Exception:  # noqa: BLE001
            parsed = _guard(lambda: _fallback_row(msg, channel))
        if parsed is None:
            continue
        row, is_orphan = parsed
        if row['postId'] in rows:
            continue
        rows[row['postId']] = row
        if is_orphan:
            orphans.add(row['postId'])

    more_links = S_MORE(doc)
    return Page(
        channel=channel,
        posts=[rows[k] for k in sorted(rows)],
        before=_guard(lambda: _cursor(doc, more_links, 'data-before', 'prev')),
        after=_guard(lambda: _cursor(doc, more_links, 'data-after', 'next')),
        orphans=sorted(orphans),
        has_channel_info=bool(S_CHANNEL_INFO(doc)),
    )


# ------------------------------------------------------------- other page kinds --
def parse_og_description(html: str) -> str | None:
    """og:description of a single-post page (t.me/<ch>/<id>). The HTML parser already unescapes it."""
    doc = _document_root(html)
    values = doc.xpath('//meta[@property="og:description"]/@content')
    return (values[0].strip() or None) if values else None


def classify_landing(html: str) -> str:
    """What t.me/<handle> is, once t.me/s/<handle> redirected. Rule order matters (access-edges.md B)."""
    doc = _document_root(html)
    if S_CHANNEL_INFO(doc):
        return 'ok'
    description = ' '.join(' '.join(_text(el) for el in S_PAGE_DESCRIPTION(doc)).split()).lower()
    extras = [' '.join(_text(el).split()).lower() for el in S_PAGE_EXTRA(doc)]
    buttons = [' '.join(_text(el).split()).lower() for el in S_PAGE_BUTTON(doc)]
    noindex = 'noindex' in ' '.join(doc.xpath('//meta[@name="robots"]/@content')).lower()

    if 'invited to a group chat' in description:
        return 'private_link'
    if any(re.search(r'subscribers?$', e) for e in extras):
        return 'restricted'   # a channel whose web preview is off (or a working channel's landing page)
    if any('member' in e for e in extras):
        return 'is_group'
    if any('start bot' in b for b in buttons) or any('monthly user' in e for e in extras):
        return 'is_bot'
    if noindex and 'view posts' in description:
        return 'banned'
    # User pages also say "you can contact" in hidden text, so not_found needs noindex and runs first.
    if noindex and 'you can contact' in description:
        return 'not_found'
    if any('send message' in b for b in buttons) and S_PAGE_TITLE(doc):
        return 'is_user'
    return 'unknown'


def parse_embed_error(html: str) -> str | None:
    """Telegram's own reason string on t.me/<h>/<id>?embed=1&mode=tme, e.g. a copyright block."""
    doc = _document_root(html)
    return _one_line(_first(S_EMBED_ERROR, doc))
