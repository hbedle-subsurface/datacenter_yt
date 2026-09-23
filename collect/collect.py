#!/usr/bin/env python3
"""
collect.py — gather what people say in YouTube comments about solar
projects proposed near them.

Rebuilt after the first version returned mostly the wrong thing. What
changed and why:

  Searching by county, not by state. YouTube ignores a state name when
  it has a stronger title match, so "solar farm Oklahoma" returned
  whatever was popular about solar farms. Local coverage is titled by
  county, so that is what gets searched. The county list comes from the
  news crawler, which extracts counties from headlines.

  Two tests instead of one score. A video must carry a topic phrase AND
  a local signal: a named county, a named US state, broadcast call
  letters in the channel name, or a county or city government channel.
  The old version added these into one score with the bar at 3, which a
  title match alone reached, so a floating solar plant in India got in.

  No guessed geography. A video's state comes from what the video says.
  The old version fell back to whichever state's search found it, which
  labelled Georgia as Louisiana and Indiana as Missouri. Which search
  found a video is kept as provenance and never as a location.

Needs one secret, YOUTUBE_API_KEY. Standard library only.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VIDEOS = os.path.join(ROOT, 'data', 'videos.json')
COMMENTS = os.path.join(ROOT, 'data', 'comments.json')
RUNS = os.path.join(ROOT, 'data', 'runs.json')
CODEBOOK = os.path.join(ROOT, 'codebook.json')

API = 'https://www.googleapis.com/youtube/v3/'
KEY = os.environ.get('YOUTUBE_API_KEY', '')

US_STATES = [
    'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
    'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho',
    'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine',
    'Maryland', 'Massachusetts', 'Michigan', 'Minnesota', 'Mississippi',
    'Missouri', 'Montana', 'Nebraska', 'Nevada', 'New Hampshire', 'New Jersey',
    'New Mexico', 'New York', 'North Carolina', 'North Dakota', 'Ohio',
    'Oklahoma', 'Oregon', 'Pennsylvania', 'Rhode Island', 'South Carolina',
    'South Dakota', 'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia',
    'Washington', 'West Virginia', 'Wisconsin', 'Wyoming']

# US broadcast stations are called KFOR, WJCL, KOCO, WCPO — four letters
# starting with K west of the Mississippi or W east of it. A very reliable
# sign that a channel is a local station rather than a global explainer.
CALL_LETTERS = re.compile(r'\b[KW][A-Z]{3}\b')

# A county name is the capitalized word before "County". Two words only
# when the first is a real county-name prefix — El Paso, St. Charles,
# Le Flore, Palm Beach. The previous version took any two capitalized
# words and so produced "These Jackson County" and "Later Madison
# County"; a stopword list never covers every word a sentence can start
# with, but the set of prefixes real counties use is small and closed.
COUNTY_PREFIX = {
    'st', 'ste', 'mt', 'san', 'santa', 'los', 'las', 'el', 'la', 'le', 'du',
    'de', 'del', 'van', 'new', 'red', 'big', 'black', 'white', 'green',
    'grand', 'fort', 'port', 'lake', 'deer', 'box', 'cedar', 'clear', 'cross',
    'palm', 'prince', 'king', 'queen', 'long', 'hot', 'iron', 'rio', 'sierra',
    'twin', 'west', 'east', 'north', 'south', 'little', 'silver', 'golden',
    'bay', 'blue', 'cape', 'castle', 'elk', 'falls', 'glacier', 'grays',
    'jack', 'jim', 'mineral', 'oak', 'pine', 'rock', 'sand', 'spring', 'star',
    'stone', 'sweet', 'thunder', 'union', 'valley', 'wind', 'wolf', 'yellow',
    'roger', 'dona', 'doña', 'val', 'palo', 'tom', 'jeff', 'deaf', 'live', 'ben',
}
COUNTY_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z'\-]*\.?\s+)?[A-Z][A-Za-z'\-]+)\s+(County|Parish|Borough)\b")


# --------------------------------------------------------------- helpers

def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def days_since(stamp):
    if not stamp:
        return 9999
    try:
        d = datetime.strptime(stamp[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return 9999
    return (datetime.now(timezone.utc) - d).days


class QuotaExhausted(Exception):
    pass


def api(endpoint, **params):
    """One API call. A 429 rateLimitExceeded is a short-term burst limit,
    not the daily quota, so it is waited out and retried twice before
    being reported. Every search in the 2026-09-14 and 2026-09-15 runs
    after the first few failed this way."""
    for wait in (8, 30, None):
        data, err = _api_once(endpoint, **params)
        if err and 'rateLimitExceeded' in err and wait:
            time.sleep(wait)
            continue
        return data, err


def _api_once(endpoint, **params):
    params['key'] = KEY
    url = API + endpoint + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode('utf-8', 'replace')), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', 'replace')
        reason = ''
        try:
            errs = json.loads(body)['error'].get('errors', [])
            reason = errs[0].get('reason', '') if errs else ''
        except Exception:
            pass
        if reason in ('quotaExceeded', 'dailyLimitExceeded'):
            raise QuotaExhausted(reason)
        return None, '%s %s' % (exc.code, reason or body[:120])
    except Exception as exc:
        return None, '%s: %s' % (type(exc).__name__, exc)


def find_counties(text, known=()):
    """Counties named in a piece of text.

    The configured county list is checked first, by exact name. Guessing
    from capitalization alone truncates the real two-word ones — Roger
    Mills, Dona Ana, Val Verde, Palo Pinto all lose their first word —
    and no prefix list covers every county in the country. When the name
    is already known, no guessing is needed.

    The pattern is still used for counties outside the list, so coverage
    from a neighboring state is recognized rather than dropped."""
    text = text or ''
    low = text.lower()
    out = []
    for name in known:
        if name.lower() in low and name not in out:
            out.append(name)

    for m in COUNTY_RE.finditer(text):
        parts = m.group(1).split()
        if len(parts) == 2 and parts[0].rstrip('.').lower() not in COUNTY_PREFIX:
            parts = parts[1:]          # the first word belongs to the sentence
        full = ' '.join(parts) + ' ' + m.group(2)
        if full not in out and not any(full in k for k in out):
            out.append(full)
    return out


# --------------------------------------------------- where to search

def counties_from_news(cfg, problems):
    """The news crawler already pulls counties out of headlines. Reusing
    its list means the two tools point at the same places."""
    src = cfg.get('county_source') or {}
    url = src.get('url')
    if not url:
        return []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'solar-research'})
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace'))
    except Exception as exc:
        problems.append('county feed unreachable (%s) — using the '
                        'hand-written list only' % type(exc).__name__)
        return []

    tally = {}
    for a in data.get('articles', []):
        if a.get('weak'):
            continue
        state = (a.get('states') or [''])[0] or (a.get('via_states') or [''])[0]
        for c in a.get('counties', []):
            key = c if not state else '%s, %s' % (c, state)
            tally[key] = tally.get(key, 0) + 1

    floor = src.get('min_articles', 1)
    ranked = [k for k, n in sorted(tally.items(), key=lambda kv: -kv[1]) if n >= floor]
    return ranked[:src.get('max_from_feed', 120)]


def build_searches(cfg, places, cursor):
    """Two tiers.

    Statewide searches run every week for every technology, so anything
    that makes the Oklahoma news is caught within a week.

    County searches are the slow sweep. Seventy-seven counties across
    seven technologies is 539 combinations and YouTube allows about a
    hundred searches a day, so each run takes the next block and picks up
    where the last one stopped. A full cycle takes a few months and runs
    in the background."""
    statewide = []
    for topic in cfg['topics']:
        for phrase in topic.get('statewide', []):
            statewide.append({'topic': topic['id'], 'county': '', 'q': phrase})

    pairs = []
    for place in places:
        bare = place.split(',')[0].strip()
        for topic in cfg['topics']:
            tmpl = topic.get('county_template')
            if tmpl:
                pairs.append({'topic': topic['id'], 'county': bare,
                              'q': tmpl.replace('{county}', bare)
                                       .replace('{place}', bare)})

    block = min(cfg.get('county_block', 45),
                max(0, cfg.get('searches_per_run', 85) - len(statewide)))
    if not pairs or block <= 0:
        return statewide, cursor
    start = cursor % len(pairs)
    taken = [pairs[(start + i) % len(pairs)] for i in range(min(block, len(pairs)))]
    return statewide + taken, cursor + len(taken)


def search_videos(q, days, per_search):
    after = (datetime.now(timezone.utc) - timedelta(days=days)
             ).strftime('%Y-%m-%dT%H:%M:%SZ')
    data, err = api('search', part='snippet', q=q, type='video',
                    maxResults=min(50, per_search), order='relevance',
                    regionCode='US', relevanceLanguage='en', publishedAfter=after)
    if err:
        return [], err
    out = []
    for item in data.get('items', []):
        sn = item['snippet']
        # The API returns titles HTML-escaped (&#39;, &amp;). Unescape once
        # here so the stored title reads correctly and place names inside
        # it match.
        out.append({'id': item['id']['videoId'],
                    'title': html.unescape(sn.get('title', '')),
                    'channel': html.unescape(sn.get('channelTitle', '')),
                    'description': html.unescape(sn.get('description', '')),
                    'published': (sn.get('publishedAt') or '')[:10]})
    return out, None


# -------------------------------------------------------------- judging

def phrases(terms):
    return [t.strip('"').lower() for t in terms if t.strip('"')]


def best_topic(text, cfg, asked):
    matches = [t for t in cfg['topics']
               if any(p in text for p in phrases(t['recognize']))]
    if not matches:
        return None
    for t in matches:
        if t['id'] == asked:
            return t
    return matches[0]


def cities_named(text, cities):
    """Towns mentioned. Matched on word boundaries because several
    Oklahoma town names sit inside ordinary words — Ada in Canada,
    Moore in a surname, Miami in the Florida one.

    Two uses of a town name are not the town. "Project Mustang" is a data
    center code name in Claremore, not Mustang, and "Coweta County" is a
    county (in Georgia as well as Oklahoma), which find_counties handles."""
    out = []
    for city in cities:
        pat = re.compile(r'\b' + re.escape(city.lower()) + r'\b')
        for m in pat.finditer(text):
            if text[max(0, m.start() - 8):m.start()].endswith('project '):
                continue
            if re.match(r'\s+(county|parish|borough)\b', text[m.end():m.end() + 9]):
                continue
            out.append(city)
            break
    return out


def states_named(text):
    """US states mentioned, not counting the ones that are really county
    names. Oklahoma has a Delaware County, a Texas County, an Oklahoma
    County and a Washington County, so a bare substring match reads four
    of its own counties as other states."""
    out = []
    for st in US_STATES:
        for m in re.finditer(re.escape(st.lower()), text):
            tail = text[m.end():m.end() + 9]
            if re.match(r'\s+(county|parish|borough)\b', tail):
                continue
            out.append(st)
            break
    return out


def place_of(video, cfg, known_counties=(), cities=()):
    """Where a video is, from what it says.

    Counties and towns are read from the title and description only. The
    channel name used to be included, so every clip from "FOX23 News
    Tulsa" was tagged Tulsa, including stories about Okmulgee and
    Muskogee County.

    States are read from the title, description and channel, since a
    channel such as "Oklahoma's Own" does say where it is.

    When no state is named at all, the home state is filled in from a
    signal that belongs to it: a station on the home_stations list, a
    channel name containing one of home_channel_words, a county from the
    configured list, or a town from the configured list that is not also
    a common place name elsewhere (ambiguous_towns). Without this, a KOCO
    story about Luther or a Stitt interview never said "Oklahoma" and fell
    out of any Oklahoma filter. An inferred state is marked in
    local_because so it can be told apart from a named one. Nothing is
    inferred when another state is named."""
    title = html.unescape(video.get('title', ''))
    desc = html.unescape(video.get('description', ''))
    chan = html.unescape(video.get('channel', ''))
    place_blob = title + ' ' + desc
    place_text = place_blob.lower()
    text = (place_blob + ' ' + chan).lower()

    counties = find_counties(place_blob, known_counties)
    states = states_named(text)
    call = bool(CALL_LETTERS.search(chan))
    gov = any(h in chan.lower() for h in cfg.get('gov_channel_hints', []))

    # "County" is not only a US word. Kenya uses it for its administrative
    # units, and a Kenyan agrivoltaics video passed the local test on
    # "Nyeri County" alone. A county from the configured list is proof on
    # its own; any other county name needs a US state or a US broadcaster
    # alongside it.
    known_set = set(known_counties)
    known_hit = [c for c in counties if c in known_set
                 or c.rsplit(' ', 1)[0] in known_set]
    # A person who shares a town's name is not the town. Darren Blanchard,
    # arrested at the Claremore meeting, was read as the town of Blanchard.
    town_text = place_text
    for phrase in cfg.get('not_towns', []):
        if not phrase.startswith('_comment'):
            town_text = town_text.replace(phrase.lower(), ' ')
    towns = cities_named(town_text, cities)

    why = []
    if known_hit:
        why.append('county named')
    elif counties:
        why.append('county named elsewhere')
    if towns:
        why.append('town named')
    if states:
        why.append('state named')
    if call:
        why.append('broadcast call letters')
    if gov:
        why.append('government channel')

    home = cfg.get('home_state')
    if home and not states:
        chan_low = chan.lower()
        stations = [x for x in cfg.get('home_stations', []) if not x.startswith('_comment')]
        words = [x for x in cfg.get('home_channel_words', []) if not x.startswith('_comment')]
        vague = {x.lower() for x in cfg.get('ambiguous_towns', []) if not x.startswith('_comment')}
        if any(re.search(r'\b' + re.escape(x.lower()) + r'\b', chan_low) for x in stations):
            states = [home]
            why.append('state inferred from station')
        elif any(re.search(r'\b' + re.escape(x.lower()) + r'\b', chan_low) for x in words):
            states = [home]
            why.append('state inferred from channel name')
        elif known_hit:
            states = [home]
            why.append('state inferred from county')
        elif [t for t in towns if t.lower() not in vague]:
            states = [home]
            why.append('state inferred from town')

    local = bool(known_hit or towns or states or call or gov)
    elsewhere = bool(home and states and home not in states)
    return {'counties': counties, 'states': states, 'towns': towns,
            'local_because': why, 'elsewhere': elsewhere, 'local': local,
            'call': call, 'gov': gov}


def judge(video, asked_topic, cfg, known_counties=(), cities=()):
    """Two tests, then points.

    Subject: does it mention a solar phrase at all.
    Local:   is it tied to a place in the United States.

    Both must pass. The points that follow only order what survives —
    they cannot let anything in on their own, which is the mistake that
    filled the first version with global explainer videos."""
    text = ' '.join([video['title'], video['channel'], video['description']]).lower()
    spec = best_topic(text, cfg, asked_topic)

    where = place_of(video, cfg, known_counties, cities)
    counties, states, towns = where['counties'], where['states'], where['towns']
    why, elsewhere, local = where['local_because'], where['elsewhere'], where['local']
    call, gov = where['call'], where['gov']

    if spec is None or not local:
        return {'keep': False, 'score': 0, 'topic': asked_topic,
                'counties': counties, 'states': states, 'towns': towns,
                'local_because': why, 'elsewhere': elsewhere,
                'missing': 'no matching phrase' if spec is None else 'nothing local'}

    # home_only keeps the collection to one state. An Oklahoma search still
    # returns Georgia and Utah coverage, and before this those videos passed
    # the local test and took tracking slots from Oklahoma ones.
    home = cfg.get('home_state')
    if cfg.get('home_only') and home and home not in states:
        return {'keep': False, 'score': 0, 'topic': spec['id'],
                'counties': counties, 'states': states, 'towns': towns,
                'local_because': why, 'elsewhere': elsewhere,
                'missing': 'outside %s' % home}

    score = 3 if any(p in video['title'].lower() for p in phrases(spec['recognize'])) else 2
    score += min(sum(1 for a in cfg['angles'] if a in text), 2)
    if counties or towns:
        score += 2
    if call or gov:
        score += 2
    if [s for s in cfg['states'] if s.lower() in text]:
        score += 1          # one of the states actually under study

    return {'keep': True, 'score': score, 'topic': spec['id'], 'counties': counties,
            'states': states, 'towns': towns, 'local_because': why,
            'elsewhere': elsewhere, 'missing': ''}


_CUE_PATTERNS = {}


def _cue_pattern(cat):
    """Whole words only, with an optional common ending (plural, past
    tense, -ing, -ion, -ive, -ly), so 'flood' still finds 'flooding' and
    'evaporat' finds 'evaporation'. Plain substring matching let 'hum' fire
    on 'human', 'nda' on 'agenda' and 'nation' on 'imagination'."""
    key = (cat['id'], tuple(cat['cues']))
    if key not in _CUE_PATTERNS:
        alts = '|'.join(re.escape(q.lower()) for q in cat['cues'])
        _CUE_PATTERNS[key] = re.compile(
            r'(?<![a-z0-9])(?:' + alts + r')(?:e|s|es|d|ed|ing|ion|ions|ive|ly)?(?![a-z0-9])')
    return _CUE_PATTERNS[key]


def cues_in(text, codebook):
    low = (text or '').lower()
    return [c['id'] for c in codebook['categories']
            if c['cues'] and _cue_pattern(c).search(low)]


# ------------------------------------------------------------- comments

def fetch_comments(video_id, max_pages, reply_pages=0):
    """Comments and replies, with nothing identifying who wrote them.
    Author names are never requested into storage.

    commentThreads shows at most five replies under each comment. When a
    thread has more, and reply_pages is above zero, the full set is asked
    for separately, at one quota unit per 100 replies. Long arguments in
    the replies were otherwise cut off after the fifth."""
    out, token = [], None
    for _ in range(max_pages):
        params = dict(part='snippet,replies', videoId=video_id, maxResults=100,
                      order='relevance', textFormat='plainText')
        if token:
            params['pageToken'] = token
        data, err = api('commentThreads', **params)
        if err:
            return out, err
        for item in data.get('items', []):
            top = item['snippet']['topLevelComment']
            out.append(one_comment(top, video_id, None))
            shown = (item.get('replies') or {}).get('comments', [])
            total = item['snippet'].get('totalReplyCount', len(shown))
            replies = None
            if reply_pages and total > len(shown):
                replies = fetch_replies(top['id'], video_id, reply_pages)
            if replies is None:
                replies = [one_comment(r, video_id, top['id']) for r in shown]
            out.extend(replies)
        token = data.get('nextPageToken')
        if not token:
            break
    return out, None


def fetch_replies(parent_id, video_id, max_pages):
    """Every reply under one comment, or None if the request failed, in
    which case the caller keeps the five it already has."""
    out, token = [], None
    for _ in range(max_pages):
        params = dict(part='snippet', parentId=parent_id, maxResults=100,
                      textFormat='plainText')
        if token:
            params['pageToken'] = token
        data, err = api('comments', **params)
        if err:
            return None
        out.extend(one_comment(r, video_id, parent_id) for r in data.get('items', []))
        token = data.get('nextPageToken')
        if not token:
            break
    return out


def one_comment(raw, video_id, parent):
    sn = raw['snippet']
    return {'id': raw['id'], 'video': video_id,
            'text': sn.get('textOriginal') or sn.get('textDisplay') or '',
            'published': (sn.get('publishedAt') or '')[:10],
            'likes': sn.get('likeCount', 0), 'reply_to': parent,
            'url': 'https://www.youtube.com/watch?v=%s&lc=%s' % (video_id, raw['id'])}


def refresh_by_id(stale, codebook):
    """Refresh stored comments directly by id, 50 to a call at one quota
    unit a call.

    commentThreads only returns the first few hundred threads of a video,
    in relevance order, and only for tracked videos. Anything outside that
    window used to go unrefreshed and lose its text. Asking for the stored
    ids reaches every comment still on YouTube, on any video, tracked or
    not. It also brings back the text of comments that were blanked
    earlier, since the id and link were kept.

    A comment YouTube does not return has been deleted or hidden by its
    author or the channel. It is left alone here and ages out once its
    last refresh is expire_days old."""
    renewed = restored = 0
    stamp = today()
    for i in range(0, len(stale), 50):
        batch = {c['id']: c for c in stale[i:i + 50]}
        data, err = api('comments', part='snippet', id=','.join(batch),
                        textFormat='plainText')
        if err:
            return renewed, restored, 'comment refresh by id — %s' % err
        for item in data.get('items', []):
            c = batch.get(item.get('id'))
            if c is None:
                continue
            sn = item.get('snippet', {})
            text = sn.get('textOriginal') or sn.get('textDisplay') or ''
            if not text:
                continue
            if c.get('expired'):
                restored += 1
            else:
                renewed += 1
            c['text'] = text
            c['likes'] = sn.get('likeCount', c.get('likes', 0))
            c['cues'] = cues_in(text, codebook)
            c['refreshed'] = stamp
            c['expired'] = False
    return renewed, restored, None


# ----------------------------------------------------------------- main

def fill_descriptions(videos, problems):
    """Descriptions for stored videos that were saved without one. The
    videos endpoint takes 50 ids a call at one quota unit a call, so this
    costs a few units, against 100 for a single search."""
    missing = [v for v in videos if 'description' not in v]
    filled = 0
    for i in range(0, len(missing), 50):
        batch = {v['id']: v for v in missing[i:i + 50]}
        data, err = api('videos', part='snippet', id=','.join(batch), maxResults=50)
        if err:
            problems.append('video descriptions — %s' % err)
            break
        for item in data.get('items', []):
            rec = batch.get(item.get('id'))
            if rec is not None:
                rec['description'] = html.unescape(item['snippet'].get('description', ''))
                filled += 1
    return filled


def retag(videos, cfg, county_names, cities):
    """Re-read the location of every stored video with the current rules
    and place lists. Videos are found once and kept, so without this a
    fix to the rules or a town added to the list would only reach videos
    found afterward. Costs no API quota.

    A video stored before descriptions were kept can only be re-read from
    its title, which would throw away places that were found in the
    description (the El Reno leak coverage names Oklahoma only there). So
    for those, what was found before is kept alongside the new reading,
    minus towns that came from the channel name or a "Project" code name.
    Once fill_descriptions or a search supplies the description, the
    reading is made from scratch."""
    changed = 0
    for rec in videos:
        rec['title'] = html.unescape(rec.get('title', ''))
        rec['channel'] = html.unescape(rec.get('channel', ''))
        where = place_of(rec, cfg, county_names, cities)
        new = {k: where[k] for k in ('counties', 'states', 'towns',
                                     'local_because', 'elsewhere')}
        if 'description' not in rec:
            title_low, chan_low = rec['title'].lower(), rec['channel'].lower()
            keep_towns = [t for t in rec.get('towns', [])
                          if not (re.search(r'\b' + re.escape(t.lower()) + r'\b', chan_low)
                                  and t.lower() not in title_low)
                          and ('project ' + t.lower()) not in title_low]
            new['towns'] = new['towns'] + [t for t in keep_towns if t not in new['towns']]
            new['counties'] = new['counties'] + [c for c in rec.get('counties', [])
                                                 if c not in new['counties']]
            old_states = [x for x in rec.get('states', [])]
            if old_states:
                inferred = [w for w in new['local_because'] if w.startswith('state inferred')]
                named = [x for x in new['states'] if x not in old_states] if not inferred else []
                new['states'] = old_states + named
                new['local_because'] = [w for w in new['local_because']
                                        if not w.startswith('state inferred')]
                if 'state named' not in new['local_because']:
                    new['local_because'].append('state named')
            home = cfg.get('home_state')
            new['elsewhere'] = bool(home and new['states'] and home not in new['states'])
        if any(rec.get(k) != v for k, v in new.items()):
            changed += 1
        rec.update(new)
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--counties', default='', help='comma separated; skips the feed')
    ap.add_argument('--days', type=int, default=0)
    ap.add_argument('--topics', default='')
    ap.add_argument('--per-search', type=int, default=0,
                    help='results per search, up to 50; blank uses per_search in queries.json')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--retag-only', action='store_true',
                    help='re-read the location of stored videos and exit; no API calls')
    args = ap.parse_args()

    cfg = load_json(os.path.join(HERE, 'queries.json'))
    codebook = load_json(CODEBOOK)
    if not cfg or not codebook:
        sys.exit('queries.json or codebook.json is missing')
    if args.topics.strip():
        want = {t.strip() for t in args.topics.split(',')}
        cfg['topics'] = [t for t in cfg['topics'] if t['id'] in want]

    problems = []
    if args.counties.strip():
        counties = [c.strip() for c in args.counties.split(',') if c.strip()]
    else:
        counties = [c for c in cfg.get('extra_counties', [])
                    if not c.startswith('_comment')]
        counties += counties_from_news(cfg, problems)
    # One search per county name. The feed labels counties with a state
    # that is sometimes a guess, so two spellings of the same county would
    # otherwise cost two searches for the same thing.
    skip = {c.lower() for c in cfg.get('skip_counties', [])
            if not c.startswith('_comment')}
    seen_names, unique = set(), []
    for c in counties:
        bare = c.split(',')[0].strip()
        if bare.lower() in skip or bare.lower() in seen_names:
            continue
        seen_names.add(bare.lower())
        unique.append(c)
    counties = unique

    cities = cfg.get('cities', [])
    places = counties + [c for c in cities if c not in counties]
    county_names = [c.split(',')[0].strip() for c in counties]

    if args.retag_only:
        store_v = load_json(VIDEOS, {'videos': []})
        n = retag(store_v['videos'], cfg, county_names, cities)
        save_json(VIDEOS, store_v)
        print('%d of %d stored videos had their location updated'
              % (n, len(store_v['videos'])))
        return

    runs = load_json(RUNS, {'runs': [], 'cursor': 0})
    cursor = runs.get('cursor', 0)
    searches, next_cursor = build_searches(cfg, places, cursor)

    pair_total = len(places) * sum(1 for t in cfg['topics'] if t.get('county_template'))
    done = (cursor % pair_total) if pair_total else 0
    print('%d counties and %d towns, %d technologies, %d searches this run'
          % (len(counties), len(cities), len(cfg['topics']), len(searches)))
    if pair_total:
        print('sweep at %d of %d place-technology pairs (%d%% of a cycle)'
              % (done, pair_total, round(100 * done / pair_total)))
    if not counties:
        print('No counties yet. Run the news crawler first, or list some under '
              'extra_counties in queries.json. Only the national topics will run.')
    if args.dry_run:
        for s in searches:
            print('  [%s/%s] %s' % (s['topic'], s['county'] or 'national', s['q']))
        for p in problems:
            print('  note: ' + p)
        return
    if not KEY:
        sys.exit('YOUTUBE_API_KEY is not set.')

    days = args.days or cfg.get('search_days', 1095)
    # A search costs the same quota whether it returns 25 results or 50,
    # so asking for the full 50 looks twice as deep for nothing extra.
    per_search = args.per_search or cfg.get('per_search', 50)
    store_v = load_json(VIDEOS, {'videos': []})
    store_c = load_json(COMMENTS, {'comments': []})
    known = {v['id']: v for v in store_v['videos']}
    new_videos = 0
    seen = rejected = 0

    # The sweep position only moves past places that were actually searched.
    # It used to advance by the whole block even when every search failed,
    # so places were skipped for a full cycle.
    first_failed_pair = None
    pair_index = -1
    limited = 0
    try:
        for i, s in enumerate(searches, 1):
            if s['county']:
                pair_index += 1
            vids, err = search_videos(s['q'], days, per_search)
            if err:
                problems.append('search "%s" — %s' % (s['q'], err))
                print('  %2d/%d  %-44s failed' % (i, len(searches), s['q'][:44]))
                if s['county'] and first_failed_pair is None:
                    first_failed_pair = pair_index
                limited = limited + 1 if 'rateLimitExceeded' in err else 0
                if limited >= 3:
                    if first_failed_pair is None:
                        first_failed_pair = pair_index + 1
                    raise QuotaExhausted('rateLimitExceeded')
                continue
            limited = 0
            kept = 0
            for v in vids:
                seen += 1
                m = judge(v, s['topic'], cfg, county_names, cities)
                if not m['keep']:
                    rejected += 1
                    continue
                kept += 1
                if v['id'] in known:
                    rec = known[v['id']]
                    rec['title'] = v['title']
                    if v.get('description'):
                        rec['description'] = v['description']
                    rec['topics'] = sorted(set(rec.get('topics', []) + [m['topic']]))
                    rec['found_by'] = sorted(set(rec.get('found_by', [])
                                                 + [s['county'] or 'national']))
                    rec['score'] = max(rec.get('score', 0), m['score'])
                    continue
                known[v['id']] = {
                    'id': v['id'], 'title': v['title'], 'channel': v['channel'],
                    'description': v.get('description', ''),
                    'published': v['published'],
                    'url': 'https://www.youtube.com/watch?v=' + v['id'],
                    'topics': [m['topic']], 'counties': m['counties'],
                    'states': m['states'], 'towns': m['towns'],
                    'local_because': m['local_because'],
                    'elsewhere': m['elsewhere'],
                    'found_by': [s['county'] or 'national'],
                    'score': m['score'], 'first_seen': today(), 'comments': 0,
                }
                new_videos += 1
            print('  %2d/%d  %-44s %2d of %2d kept'
                  % (i, len(searches), s['q'][:44], kept, len(vids)))
    except QuotaExhausted:
        problems.append('search quota or rate limit reached; remaining searches '
                        'skipped. The daily quota resets at midnight Pacific.')
        print('  search quota exhausted, moving on to comments')
        if first_failed_pair is None:
            first_failed_pair = max(pair_index, 0)
    if first_failed_pair is not None:
        next_cursor = cursor + first_failed_pair

    try:
        fill_descriptions(list(known.values()), problems)
    except QuotaExhausted:
        problems.append('quota exhausted before stored video descriptions were '
                        'filled; locations were re-read from titles')
    retag(known.values(), cfg, county_names, cities)

    videos = sorted(known.values(),
                    key=lambda v: (v.get('score', 0), v.get('published', '')),
                    reverse=True)
    # Home-state videos are tracked first and are not bound by max_tracked,
    # so a busy week of new videos can no longer push Oklahoma coverage out.
    # Other videos fill whatever room is left under max_tracked.
    home = cfg.get('home_state')
    def is_home(v):
        return bool(home) and home in (v.get('states') or [])
    cap = cfg.get('max_tracked', 250)
    home_cap = cfg.get('max_tracked_home', 1000)
    home_v = [v for v in videos if is_home(v)]
    other_v = [v for v in videos if not is_home(v)]
    tracked = home_v[:home_cap] + other_v[:max(0, cap - min(len(home_v), home_cap))]
    tracked_ids = {v['id'] for v in tracked}
    dropped = [v for v in videos if v['id'] not in tracked_ids]
    if len(home_v) > home_cap:
        problems.append('%d %s videos are over max_tracked_home and were not '
                        'fetched this run; their stored comments are still '
                        'refreshed by id' % (len(home_v) - home_cap, home))

    by_id = {c['id']: c for c in store_c['comments']}
    pages = cfg.get('max_comment_pages', 5)
    pages_home = cfg.get('max_comment_pages_home', pages)
    reply_pages = cfg.get('max_reply_pages', 0)
    fetched = refreshed = 0

    try:
        for n, v in enumerate(tracked, 1):
            deep = is_home(v)
            got, err = fetch_comments(v['id'], pages_home if deep else pages,
                                      reply_pages if deep else 0)
            if err:
                if 'commentsDisabled' not in err:
                    problems.append('comments %s — %s' % (v['id'], err))
                v['comments_off'] = True
                continue
            v.pop('comments_off', None)
            v['comments'] = len(got)
            for c in got:
                c['cues'] = cues_in(c['text'], codebook)
                c['refreshed'] = today()
                c['expired'] = False
                if c['id'] in by_id:
                    c['first_seen'] = by_id[c['id']].get('first_seen', today())
                    refreshed += 1
                else:
                    c['first_seen'] = today()
                    fetched += 1
                by_id[c['id']] = c
            if n % 25 == 0 or n == len(tracked):
                print('  comments: %d of %d videos' % (n, len(tracked)))
    except QuotaExhausted:
        problems.append('comment quota exhausted partway through; the rest will '
                        'be refreshed next run.')
        print('  comment quota exhausted')

    # Everything the thread fetch did not reach today, including comments
    # whose text was blanked before, is asked for by id. Oldest refresh
    # first, so if the quota runs short the ones closest to the limit go
    # first.
    stale = [c for c in by_id.values() if c.get('refreshed') != today()]
    stale.sort(key=lambda c: (bool(c.get('expired')), c.get('refreshed') or ''))
    by_id_renewed = restored = 0
    try:
        by_id_renewed, restored, err = refresh_by_id(stale, codebook)
        if err:
            problems.append(err)
    except QuotaExhausted:
        problems.append('quota exhausted during the refresh by id; the rest '
                        'will be refreshed next run')
    refreshed += by_id_renewed

    # Text is removed only when YouTube's 30 day limit is actually reached.
    # It used to be removed the moment a video left the tracked set, which
    # blanked 6,574 comments on 2026-09-22 that were six to eight days old.
    expire_after = cfg.get('expire_days', 30)
    expired = 0
    for c in by_id.values():
        if days_since(c.get('refreshed')) >= expire_after:
            if not c.get('expired'):
                c['text'] = ''
                c['cues'] = []
                c['expired'] = True
                expired += 1

    comments = sorted(by_id.values(),
                      key=lambda c: (c.get('published', ''), c.get('likes', 0)),
                      reverse=True)

    save_json(VIDEOS, {'updated': now_iso(), 'tracked': len(tracked),
                       'videos': tracked + dropped})
    save_json(COMMENTS, {'updated': now_iso(), 'expire_days': expire_after,
                         'comments': comments})
    runs['runs'].insert(0, {
        'when': now_iso(), 'searches': len(searches), 'counties': len(counties),
        'sweep_position': next_cursor % pair_total if pair_total else 0,
        'sweep_total': pair_total,
        'videos_seen': seen, 'videos_rejected': rejected, 'new_videos': new_videos,
        'tracked_videos': len(tracked), 'new_comments': fetched,
        'refreshed_comments': refreshed, 'restored_comments': restored,
        'expired_comments': expired,
        'total_comments': len(comments), 'problems': problems[:25],
    })
    runs['runs'] = runs['runs'][:60]
    runs['cursor'] = next_cursor
    save_json(RUNS, runs)

    away = sum(1 for v in tracked if v.get('elsewhere'))
    live = sum(1 for c in comments if not c.get('expired'))
    if cfg.get('home_state') and away:
        print('\n%d of the tracked videos name a state other than %s. They are kept '
              'and flagged, not dropped \u2014 %s county names repeat in other states.'
              % (away, cfg['home_state'], cfg['home_state']))
    print('%d tracked videos are in %s' % (sum(1 for v in tracked if is_home(v)),
                                           cfg.get('home_state') or 'the home state'))
    print('\n%d results looked at, %d turned away for having no matching phrase '
          'or nothing local' % (seen, rejected))
    print('%d new videos, %d tracked' % (new_videos, len(tracked)))
    print('%d new comments, %d refreshed, %d restored, %d expired'
          % (fetched, refreshed, restored, expired))
    print('%d comments on file, %d with text' % (len(comments), live))
    if problems:
        print('\n%d problems:' % len(problems))
        for p in problems[:10]:
            print('  ' + p)


if __name__ == '__main__':
    main()
