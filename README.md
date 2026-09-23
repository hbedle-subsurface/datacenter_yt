# What people say about data centers in Oklahoma

This repository collects public YouTube comments posted under videos about data centers in
Oklahoma, and provides a web page for reading and coding them.

Live at **https://hbedle-subsurface.github.io/datacenter_yt/**

---

## What it collects

For each comment: the text, the date it was posted, its number of likes, whether it is a
reply, and a link to it on YouTube.

For each video: the title, channel, description, date, link, the Oklahoma places it
mentions, the reason it was counted as Oklahoma coverage, and which search found it.

## What it does not collect

No commenter names, usernames, profile pictures, channel IDs or other account details.
YouTube offers these through its API and the collector does not ask for them. So the data
cannot show whether two comments came from the same person, or where a commenter lives.

No video transcripts or video files, and nothing from platforms other than YouTube.
Comments that YouTube has held for review or removed are not available to the collector.

---

## How videos are found

Several times a week the collector searches YouTube through the YouTube Data API, the official
access route YouTube provides. Each run makes 85 searches.

Sixteen are statewide and run every time:

- Oklahoma data center
- Oklahoma data center moratorium
- Oklahoma data center opposition residents
- Oklahoma City data center moratorium council
- Oklahoma data center water use
- Oklahoma data center electricity rates ratepayers
- Oklahoma data center rezoning hearing
- Oklahoma data center tax incentives
- Oklahoma data center jobs economic development
- Oklahoma AI data center county commissioners
- Norman Oklahoma data center city council
- Stillwater Oklahoma data center
- Tulsa Oklahoma data center residents
- Cherokee Nation data center
- Project Mustang Claremore
- El Reno data center bitcoin

The rest take the form *[place] Oklahoma data center* and rotate through all 77 Oklahoma
counties and 53 towns, about 69 places per run, so every place is searched at least once
every two runs. Towns are included because in Oklahoma a city council, not the county,
usually decides where a data center goes, and local news titles the story by town.

Each search asks for the top 50 results from the past three years, in YouTube's own
relevance order. The videos found therefore depend partly on YouTube's ranking system, and
the collection is not a random sample of every video on the subject. Several of the search
phrases name a dispute (opposition, moratorium, rates, tax incentives), which makes
coverage of local conflict more likely to be found than other coverage. Two of them name
specific events, the Claremore and El Reno projects.

## Which videos are kept

A video is kept only if it passes both of these tests:

1. **It is about data centers.** Its title or description contains *data center*,
   *data centre*, *datacenter*, *hyperscale*, *server farm*, *AI campus*, *cloud campus*
   or *colocation*.
2. **It is about Oklahoma.** Oklahoma is named; or an Oklahoma county or town is named; or
   the channel is an Oklahoma TV station (KOCO, KFOR, KOKH, KWTV, KOTV, KJRH, KTUL, KOKI,
   KSWO, KSBI, KAUT, KXII, KTEN, FOX23, FOX 25, News 9, News On 6) or has Oklahoma, OKC,
   Tulsa, Oklahoman or Tulsa World in its name.

Twenty-four Oklahoma town names that are common elsewhere (Norman, Moore, Yukon, Edmond,
Mustang and others) do not count as Oklahoma on their own. National outlets are kept when
their video is about an Oklahoma event. Videos about other states are turned away, even
when an Oklahoma search returns them.

Places are recognized by matching names in text, so mistakes happen. A person who shares a
town's name can be read as the town; those names are listed under `not_towns` in
`collect/queries.json` once they are found. Every video records which signal placed it in
Oklahoma (`local_because`) and which search found it (`found_by_search`), so the basis for
each placement can be checked.

## Which comments are collected

For Oklahoma videos, up to 2,000 comment threads per video, in the order YouTube ranks
them, and every reply under each of them. On the largest videos this still reaches only
the threads YouTube ranks highest.

---

## Keeping the data current: the 30 day rule

YouTube's developer policies require stored comment text to be refreshed from YouTube or
deleted within 30 days.

Every run re-collects the comments on every tracked video, then asks YouTube for every
other stored comment by its ID. A comment keeps its text as long as it is still public on
YouTube, whether or not its video is still tracked.

When the author or the channel deletes or hides a comment, YouTube stops returning it.
Thirty days after it was last returned, its text is removed here. Its link and any codes
already given to it are kept. A person's choice to withdraw a comment therefore carries
through into the collection.

The permanent research record is the set of dated exports made from the web page
(*Your coding, as a table*), held under the project's data management plan. Counts
reported anywhere should come from a dated export, since the live collection changes every
week.

---

## Coding

The web page shows each comment with its video, and a coder assigns it to categories from
`codebook.json`: water use, electricity rates, noise, tax incentives, public notice and
process, jobs, and others. The codebook is kept identical to the one in the solar
collection so the two can be compared.

The page marks keywords that may point to a category. These are suggestions only. Every
code in an export was assigned by a person who read the comment.

Codes are saved in the web browser used for coding, not in this repository. Clearing that
browser's data or moving to another computer loses them unless there is a file from
*Back up your work* to restore from.

---

## What the data can and cannot show

People who comment on YouTube choose to do so, and they are not a sample of Oklahoma
residents. National coverage of an Oklahoma event draws commenters from anywhere, and a
few widely watched videos can contribute a large share of all comments. YouTube's ranking
affects which videos are found and, on large videos, which comments are collected.

The data shows what arguments people make and in what words. It does not measure how many
Oklahomans hold a view.

---

## Running it

It needs a free YouTube API key:

1. At **console.cloud.google.com**, create a project. Billing is not required.
2. **APIs & Services → Library → YouTube Data API v3 → Enable.**
3. **APIs & Services → Credentials → Create credentials → API key.** Restrict it to the
   YouTube Data API v3 and leave application restrictions at None.
4. In this repository: **Settings → Secrets and variables → Actions → New repository
   secret**, named `YOUTUBE_API_KEY`.
5. **Settings → Actions → General → Workflow permissions → Read and write.**
6. **Actions → Collect comments → Run workflow.**

It runs by itself five days a week: Tuesday, Thursday, Friday, Saturday and Sunday, at
8:17 a.m. Oklahoma time. The daily YouTube allowance belongs to the Google Cloud project
and is shared with elsa_yt (Mondays) and ses_ok_yt (Wednesdays), and a run uses most of a
day's searches, so those two days are left to them. The weekly schedule this replaced ran
on Tuesdays only; the cron line in `.github/workflows/collect.yml` switches between them. The allowance resets at
midnight Pacific time. A run started after the searches are used up skips them and still
refreshes the comments.

## Settings

All of them are in `collect/queries.json`.

| Setting | What it does |
|---|---|
| `home_state`, `home_only` | Keep only videos tied to this state |
| `statewide`, `county_template` | The search phrases |
| `extra_counties`, `cities` | The places the rotation works through |
| `not_towns` | Names of people that are not to be read as towns |
| `per_search` | Results asked for per search, up to 50 |
| `searches_per_run` | Searches per run |
| `max_tracked_home`, `max_tracked` | How many videos are re-collected each run |
| `max_comment_pages_home` | Pages of 100 comment threads per Oklahoma video |
| `max_reply_pages` | Pages of 100 replies per thread |
| `expire_days` | Days before text of an unreturned comment is removed |

## Files

```
.github/workflows/collect.yml   the schedule and the Run workflow button
collect/collect.py              the collector; Python standard library only
collect/queries.json            searches, places and settings
codebook.json                   the coding categories
index.html, app.js              the web page for reading, coding and exporting
data/comments.json              the comments, written by each run
data/videos.json                the videos
data/runs.json                  run history and the place rotation position
```

```
python3 collect/collect.py --dry-run       # list this run's searches without calling YouTube
python3 collect/collect.py --retag-only    # re-read video locations with the current rules
python3 -m http.server                     # view the page at localhost:8000
```

## Credit

Comment data comes from the YouTube Data API v3 and remains subject to the YouTube API
Services Terms of Service. Comments belong to the people who wrote them.

Built at the University of Oklahoma for research on public response to data center
development.

## License

Creative Commons Attribution-ShareAlike 4.0 International. See `LICENSE`. The license
covers the software, not the collected data.
