# Telegram Channel Scraper - Posts, Media & Buttons | No Login

Telegram channel scraper for public channels, with no login, no phone number and no API key. Paste a channel and get every post as a row: text, date, views, reactions, Telegram's own photo and video links, every album item, file names and sizes, the links in the text and the buttons under each post.

Available as an [Apify Actor](https://apify.com/anshumanatrey/telegram-channel-scraper). $0.30 per 1,000 posts, no start fee. One required field.

---

## What does it do?

You type one or more public Telegram channels and press Start. Each post comes back as one dataset row, newest first, with the same 27 columns every time, so it drops straight into a spreadsheet, a database or an AI pipeline.

- **The post itself.** Plain text with emoji and line breaks kept, the original HTML (bold, links, spoilers), date in UTC, views, the author signature, whether it was edited, and "via @bot" when a bot posted it.
- **Media, as Telegram's own links.** Photos, videos, GIFs, round videos, voice notes and stickers each come with Telegram's link, plus width, height, duration and thumbnail where Telegram shows them. An album is one row with every item listed, and each item keeps its own post id.
- **Files and music.** Documents come with their file name and size (`app-release.apk`, `12.3 MB`), music with its title and performer.
- **Inline buttons.** The buttons under a post, with their text, link, row and column. On alert, signal and deal channels this is where the real link lives (the exchange filing, the checkout, the chart).
- **Links, previews, forwards and replies.** Every link in the text, the preview card Telegram shows for a shared link, who a post was forwarded from, and which post it replies to, even in another channel.
- **Reactions and polls.** Every reaction with its count, including custom emoji and paid star reactions. Polls with the question, each option's share and the voter count.
- **Channel details on every row.** Channel name, title, numeric id (it survives a rename) and subscriber count, so each row stands on its own.

## How is it different from other Telegram scrapers?

We read the output fields that the 10 most-used Apify Store scrapers reading Telegram's public web preview (the same source as this actor) declare in their dataset schemas and READMEs, on 2026-09-23. How many of the 10 list each field:

| Field | Other web-preview scrapers (of 10) | This actor |
|---|---|---|
| Inline buttons under a post | 0 | Yes, with text, link, row and column |
| Document file name and size | 0 | Yes |
| Every item of an album | 4 | Yes, each with its own post id |
| Reactions (custom emoji and paid stars too) | 5 | Yes |
| Author signature | 4 | Yes |
| Edited flag | 3 | Yes |
| Original HTML of the text | 2 | Yes |
| Poll question, options and voters | 1 | Yes |
| Video duration and thumbnail | 1 | Yes |
| Start fee per run | 4 of 10 charge one | None |
| Price per 1,000 posts | $2.00, the median of the 15 most-used Telegram channel scrapers | $0.30 |

Two more things you only notice when something goes wrong:

- **Every skipped channel says why.** Groups, personal accounts, bots, names that do not exist, private invite links and channels Telegram has blocked all look like an ordinary page to a scraper. This actor recognises each one and writes a plain reason ("@ru_python is a group, not a channel") into the run summary instead of returning an empty result. When Telegram blocks a channel it passes on Telegram's own words, for example "This channel can't be displayed because it violated local laws."
- **Posts the web preview hides still get their text.** A few posts show only "Please open Telegram to view this post" on the web. The actor fetches the post's own page and recovers the text (up to about 1,000 characters, flagged with `textTruncated` when Telegram cut it).

## When should I use it?

- Monitoring channels every day or every hour for news, announcements, deals or signals, and feeding the posts into a sheet, a database or an alerting bot.
- Collecting the links behind inline buttons, such as the exchange filing on an earnings-alert channel or the product link on a deals channel.
- Building a dataset of posts for research, sentiment analysis or training, with views and reactions as engagement numbers.
- Tracking what a brand, a public figure or a competitor posts, with the photos and videos alongside.
- Archiving a channel's history back to its first post.
- OSINT and journalism: who forwards whom, which channels quote each other, and a dated record of what a channel posted in case it later edits or deletes it.

## What does it cost?

Pay per post: **$0.30 per 1,000 posts** ($0.0003 each). No start fee, no monthly fee, platform compute included.

| Run | Posts | Price |
|---|---|---|
| The default input (the 100 newest posts of 2 channels) | 200 | $0.06 |
| One channel, last 1,000 posts | 1,000 | $0.30 |
| 5 channels, 600 posts each | 3,000 | $0.90 |
| 10,000 posts | 10,000 | $3.00 |
| A run where no channel could be read | 0 | $0.00 |

An album counts as one post. Apify's free plan includes $5 of monthly credit, which covers about 16,000 posts. Set a spending limit on a run for a hard cap: the run stops cleanly when it is reached and every post you paid for is saved.

## How does the price compare with other Telegram scrapers?

The same job with the most-used Telegram scrapers on the Apify Store, worked out from each actor's live pricing on 2026-09-24. Apify free plan, one channel, each actor's default memory (some start fees are charged per GB of memory):

| Scraper | Per post | Start fee | 100 posts | 1,000 posts | 10,000 posts | Notes |
|---|---|---|---|---|---|---|
| **This actor** | **$0.0003** | **None** | **$0.030** | **$0.30** | **$3.00** | Same price on every plan |
| [Telegram Keyword Search Scraper](https://apify.com/lofomachines/telegram-keyword-search-scraper) by lofomachines | $0.0025 | $0.0002 | $0.25 | $2.50 | $25.00 | Also searches keywords across Telegram |
| [Telegram Scraper](https://apify.com/tri_angle/telegram-scraper) by tri_angle | $0.005 | $0.0001 | $0.50 | $5.00 | $50.00 | - |
| [Telegram Channel Message](https://apify.com/truefetch/telegram-channel-message) by truefetch | $0.00035 | $0.01 | $0.045 | $0.36 | $3.51 | Free-plan runs capped at 10 results |
| [Telegram Channel & Profile Scraper](https://apify.com/automation-lab/telegram-scraper) by automation-lab | $0.001 | $0.005 | $0.11 | $1.01 | $10.01 | Totals include $0.002 channel info per channel |
| [Telegram Channels Scraper: Messages, Contacts & Leads](https://apify.com/khadinakbar/telegram-channel-scraper) by khadinakbar | $0.002 | $0.003 | $0.20 | $2.00 | $20.00 | Totals include $0.001 channel info per channel |
| [Telegram Channel Content & Media Scraper](https://apify.com/webfinity/telegram-channel-content-media-scraper-v2) by webfinity | $0.01 | None | $1.00 | max 200 per channel | max 200 per channel | Last 30 days only |
| [Telegram Channel Posts Scraper - Comments & Media](https://apify.com/fetch_cat/telegram-channel-posts-scraper) by fetch_cat | $0.00045 | $0.005 | $0.050 | $0.45 | $4.48 | Also reads post comments |
| [Telegram Chat Scraper](https://apify.com/agentx/telegram-chat-scraper) by agentx | $0.00054 | $0.01 | $0.064 | $0.55 | $5.41 | Free-plan runs capped at 10 results |
| [Telegram Channel Scraper - Posts, Views & Engagement](https://apify.com/viralanalyzer/telegram-channel-scraper) by viralanalyzer | $0.03 | None | $3.00 | max 500 per channel | max 500 per channel | - |
| [Telegram Channel Scraper - No Bot Token, No Phone Login](https://apify.com/tugelbay/telegram-posts-scraper) by tugelbay | $0.002 | $0.00005 | $0.20 | $2.00 | $20.00 | - |
| [Telegram Channel Scraper - Messages & Members](https://apify.com/george.the.developer/telegram-channel-scraper) by george.the.developer | $0.005 | None | $0.50 | $5.00 | $50.00 | - |
| [Telegram Channel Scraper](https://apify.com/thescrapelab/Apify-Telegram-Scraper) by thescrapelab | $0.00069 | $0.00005 | $0.069 | $0.69 | $6.90 | - |

Several of these actors discount their prices on paid Apify plans. On the top paid tiers, fetch_cat, automation-lab and truefetch cost less per post than this actor's flat $0.0003, so compare at your own plan if you run millions of posts. What each one returns also differs: see the field comparison above.

## Which inputs does it take?

| Field | Required | What it does |
|---|---|---|
| `channels` | Yes | Public channels, one per line. A name (`durov`), `@durov`, `t.me/durov`, `https://t.me/s/durov`, a post link like `t.me/durov/123`, a Telegram Web link like `web.telegram.org/k/#@durov`, `telegram.me` and `telegram.dog` links all work. Quotes and trailing punctuation from a pasted sentence are cleaned off |
| `maxPosts` | No | The most posts to take from each channel, newest first. The form starts at 100. Through the API you can leave it out: then you get the 100 newest, or every post since the date when you send one |
| `postedAfter` | No | Only posts on or after this date (UTC). Pick a date, or type a span like `7 days`, `2 weeks`, `3 months` |

When both limits are set, each channel stops at whichever it reaches first. Through the API:

```json
{
  "channels": ["durov", "https://t.me/telegram"],
  "maxPosts": 500,
  "postedAfter": "30 days"
}
```

## What does the output look like?

One row per post. A real row from an earnings-alert channel, run on Apify on 2026-09-23 (the photo link is shortened here):

```json
{
  "channel": "earnings_pulse",
  "channelId": 2457353254,
  "channelTitle": "Earnings Pulse",
  "channelSubscribers": 16700,
  "postId": 14397,
  "postUrl": "https://t.me/earnings_pulse/14397",
  "date": "2026-09-23T14:30:09+00:00",
  "text": "📅 Tomorrow's Calendar - 24 Sep, 2026\nKey companies reporting results: #ESDS #PERNIASPOP\n\n @earnings_pulse",
  "textHtml": "<i class=\"emoji\" ...",
  "textTruncated": false,
  "author": null,
  "isEdited": false,
  "isService": false,
  "viaBot": null,
  "views": 1600,
  "reactions": [],
  "forwardedFrom": null,
  "replyTo": null,
  "links": ["https://t.me/earnings_pulse"],
  "buttons": [
    {
      "text": "📥 TV Watchlist",
      "url": "https://earningspulse.ai/watchlist?source=calendar&market=IN&date=2026-09-24&exchange=nse&illiquid=false",
      "row": 1,
      "column": 1,
      "type": "url"
    }
  ],
  "linkPreview": null,
  "mediaType": "photo",
  "mediaUrl": "https://cdn5.telesco.pe/file/J_2-EXw1QZwMA0WAXI6R4bti3qSdjie6...jpg",
  "media": [
    {
      "type": "photo",
      "url": "https://cdn5.telesco.pe/file/J_2-EXw1QZwMA0WAXI6R4bti3qSdjie6...jpg",
      "thumbnailUrl": null,
      "width": 800,
      "height": 551,
      "durationSeconds": null,
      "fileName": null,
      "fileSize": null,
      "fileId": "6170246302470443471",
      "postId": 14397,
      "unavailable": null
    }
  ],
  "poll": null,
  "unsupported": null,
  "scrapedAt": "2026-09-23T17:55:18+00:00"
}
```

A file post from the same run, media part only. Telegram shows the name and size but gives no public download link, so `url` is empty and `unavailable` says why:

```json
{
  "postUrl": "https://t.me/magisk_update/8",
  "isEdited": true,
  "mediaType": "document",
  "media": [
    { "type": "document", "url": null, "fileName": "app-release (1).apk", "fileSize": "12.3 MB", "postId": 8, "unavailable": "no_public_link" }
  ]
}
```

A poll:

```json
{
  "postUrl": "https://t.me/polls/161",
  "poll": {
    "question": "By 2030, what will be NATO's biggest challenge to its unity and effectiveness?",
    "type": "Anonymous Poll",
    "options": [
      { "text": "Rising US isolationism pulling back from commitments", "percent": 0 },
      { "text": "Internal member state political divisions", "percent": 67 },
      { "text": "An increasingly aggressive Russia or China", "percent": 33 }
    ],
    "voters": 3
  }
}
```

The run summary is the `OUTPUT` record (Console: the Output tab; API: the default key-value store, key `OUTPUT`). It lists every channel you typed with its status (`ok`, `partial` or `skipped`), the reason in plain English, the number of posts and the newest and oldest post dates. There is no summary row in the dataset, so CSV and Excel exports hold posts only.

## How fast is it?

Measured on Apify at the default 256 MB on 2026-09-23:

- The default input, 200 posts from 2 channels: **6.5 seconds**.
- 2,400 posts from 5 channels read at the same time: **64 seconds**.
- 2,000 posts from one channel: **142 seconds**, about 14 posts a second per channel. Five channels are read at once.
- The parser was checked against 1,165 real posts from 89 pages of more than 60 channels (news, crypto, files, music, polls, stickers, albums): 0 errors, and the text matched a second, independent HTML parser on 1,162 of 1,165 posts (the other 3 differ by one doubled space).

More memory does not make it faster: at 512 MB the same 2,000 posts took 125 seconds instead of 142. The time goes into waiting for Telegram, not into computing.

## Common questions

**Q: Is this an alternative to Telegram Scraper by tri_angle, Telegram Keyword Search Scraper by lofomachines or Telegram Channel Message by truefetch?** For reading public channels, yes, at $0.30 per 1,000 posts with no start fee and no 10-result cap on the free plan (see the price table above). Two things it does not do: keyword search across all of Telegram (lofomachines' actor does that) and the comments under posts (fetch_cat's actor reads them).

**Q: Do I need a Telegram account, a phone number or an API key?** No. The actor reads Telegram's public web preview of each channel (`t.me/s/<channel>`), the same page anyone can open in a browser.

**Q: Can it read groups, private channels or personal accounts?** No. Telegram only shows public channels on the web. Groups, private channels, invite links, bots and personal accounts are skipped, and the run summary says which kind each one was.

**Q: Why was a channel skipped?** The summary gives the reason. The common ones: the name is a group or a person, the channel does not exist, its owner turned off the web preview, or Telegram blocks it. Some channels are blocked only in some countries; from Apify's servers we saw one channel refused with "This channel can't be displayed because it violated local laws."

**Q: Does it download the photos, videos and files?** No. It returns Telegram's own links, which keeps the price low and your data yours. Photos are Telegram's web images, up to 800 pixels on the long side. Videos come as the full original file when Telegram shows them on the web, which was about 7 in 10 of the 169 videos we checked; larger ones come with a thumbnail and `"unavailable": "too_big"`. Documents and music have no public link on Telegram's web at all, so you get the name and size, and `postUrl` opens the post in Telegram.

**Q: How long do the media links work?** Telegram signs them and they expire, anywhere from minutes to a few hours. Download what you need soon after the run, or run again for fresh links. `fileId` stays the same across runs for photos and videos, so you can tell whether a file is one you already have.

**Q: How do I get a channel's whole history?** Set `maxPosts` to a big number, such as 100000, and leave the date empty. The actor walks back to the first post the web preview shows, at about 14 posts a second per channel.

**Q: How do I check a channel every day for new posts?** Save the input as an Apify task with `postedAfter` set to `1 day` and schedule it daily. Each run then returns only the last day's posts.

**Q: Are comments included?** No. The discussion under a post is a separate group, which Telegram does not show in the channel's web preview.

**Q: What is `unsupported`?** Telegram's own label for something its web preview cannot show, such as a giveaway, a paid post or a very large video. The post is still returned with everything else Telegram shows, never dropped.

**Q: What is `isService`?** Channel events such as "Channel created", "Channel photo updated", a pinned message or a live stream. They are returned as rows flagged `isService: true`, and cost the same as a post.

**Q: Can I call it from code or an AI agent?** Yes. Start it through the Apify API or the Apify MCP server with the JSON input above and read the dataset. Every field is always present, empty (`null`) when a post has no such part.

## Limitations

- Public channels only, as Telegram shows them on the web.
- Photos are Telegram's web size (800 pixels on the long side). Files, music and the largest videos have no public link.
- Media links expire after minutes to a few hours.
- No comments and no forward counts, because the web preview does not show them.
- A channel's history is read one page of about 20 posts after another, so one very large channel takes time: about 800 posts a minute.

---

## About the maintainer (priority response within 1-2 hours)

Built and maintained by **Anshuman Atrey** ([@AnshumanAtrey](https://github.com/AnshumanAtrey)).

- Purple-team security researcher, 5x hackathon winner
- Co-founder of **Walrus Securitas** (AI cybersecurity SaaS) and **The Drone Syndicate** (autonomous defence drones)
- Author of the OSINT and data actor portfolio on Apify Store: 16 shipped actors covering email, phone, username, IP and domain, network, secret, social, LinkedIn, domain history, Telegram and Indian fintech data

### Custom feature requests shipped within 1-2 hours (priority)

If you need a field, a filter or an output format this actor does not have, the maintainer ships it directly into this actor, typically within 1-2 hours for priority requests during active hours and within 24 hours overnight. This is direct one-to-one service from the maintainer, not a contractor queue.

**Fastest contact channels (ranked by response speed):**
1. **LinkedIn DM** -> [linkedin.com/in/anshumanatrey](https://linkedin.com/in/anshumanatrey), typically under 1 hour during active hours
2. **GitHub issue** on this actor's repo
3. **Apify Console** DM to `@anshumanatrey`
4. **Email** via [atrey.dev](https://atrey.dev)

---

## Sibling actors by the same maintainer

| Actor | Use case |
|---|---|
| [social-analyzer](https://apify.com/anshumanatrey/social-analyzer) | Username -> profiles across 900+ social sites with confidence scoring |
| [instagram-profile-intel-no-login](https://apify.com/anshumanatrey/instagram-profile-intel-no-login) | Instagram username -> bio emails + phones + 25 fields (no login) |
| [yt-dlp-video-link-extractor](https://apify.com/anshumanatrey/yt-dlp-video-link-extractor) | Any video URL -> direct stream and download links + metadata, 1000+ sites |
| [holehe-email-osint](https://apify.com/anshumanatrey/holehe-email-osint) | Email -> registered accounts across 120+ platforms |
| [linkedin-harvester](https://apify.com/anshumanatrey/linkedin-harvester) | Email -> best-match public LinkedIn profile URL + confidence score |
| [domain-history-contact-osint](https://apify.com/anshumanatrey/domain-history-contact-osint) | Domain -> past owners, WHOIS history and contacts with a source for each |
| [theharvester-osint](https://apify.com/anshumanatrey/theharvester-osint) | Domain -> emails + subdomains + IPs from 54+ public sources |
| [phoneinfoga-phone-osint](https://apify.com/anshumanatrey/phoneinfoga-phone-osint) | International phone -> country, footprint URLs, OSINT trail |
| [netintel](https://apify.com/anshumanatrey/netintel) | IP or domain -> unified WHOIS + DNS + GeoIP + ASN + ports |
| [nmap-scanner](https://apify.com/anshumanatrey/nmap-scanner) | Network -> port + service + version detection, NSE scripts |
| [bug-bounty-finder](https://apify.com/anshumanatrey/bug-bounty-finder) | Domain -> active HackerOne + Bugcrowd + security.txt programs |
| [gitleaks-github-secret-scanner](https://apify.com/anshumanatrey/gitleaks-github-secret-scanner) | GitHub -> leaked API keys across 30+ services |
| [betterleaks-cloud](https://apify.com/anshumanatrey/betterleaks-cloud) | GitHub + S3 -> leaked secrets with live vendor-API validation |
| [upi-id-osint](https://apify.com/anshumanatrey/upi-id-osint) | Indian phone or VPA -> active UPI IDs + bank-registered name from NPCI |

---

## Documentation

- Apify Store: https://apify.com/anshumanatrey/telegram-channel-scraper
- GitHub repo: https://github.com/AnshumanAtrey/telegram-channel-scraper
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Issues / feature requests: open an issue on the GitHub repo or DM LinkedIn for the fastest response
- License: MIT

## Last updated

2026-09-23 (version 1.0)
