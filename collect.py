#!/usr/bin/env python3
"""Collects Sydney events into events.json. GitHub Actions runs this every night.

Sources: Ticketmaster (needs a free key in TICKETMASTER_KEY), Inner West Council
and Northern Beaches Council what's-on pages. Each source is separate, so one
breaking never stops the others.
"""
import datetime as dt
import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "personal-events-calendar/1.0 (hobby project, one run a night)"}
TODAY = dt.date.today()
HORIZON = TODAY + dt.timedelta(days=120)  # how far ahead to look
DELAY = 1.5  # seconds between page loads, to be polite to council servers

# A council listing is kept if it contains a KEEP word and no SKIP word. Edit freely.
KEEP = ["festival", "market", "fair", "fete", "carnival", "concert", "live music", "comedy",
        "expo", "parade", "fiesta", "festa", "gig", "cinema", "film", "show", "party",
        "regatta", "open day", "food", "wine"]
SKIP = ["library", "workshop", "class", "meeting", "webinar", "storytime", "mahjong",
        "seniors", "school holiday", "drawing", "session", "course", "talk"]

MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
DATE_RE = re.compile(r"(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?: (\d{4}))?")
TIME_RE = re.compile(r"(\d{1,2})\.(\d{2}) ([AP]M) - (\d{1,2})\.(\d{2}) ([AP]M)")
IW_SUBURBS = ["Annandale", "Ashbury", "Ashfield", "Balmain East", "Balmain", "Birchgrove", "Camperdown",
              "Croydon Park", "Croydon", "Dulwich Hill", "Enmore", "Haberfield", "Hurlstone Park",
              "Leichhardt", "Lewisham", "Lilyfield", "Marrickville", "Newtown", "Petersham", "Rozelle",
              "St Peters", "Stanmore", "Summer Hill", "Sydenham", "Tempe"]


def get(url):
    time.sleep(DELAY)
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def area_for(postcode):
    """Maps a postcode to one of the calendar's areas, or None to skip the event."""
    try:
        p = int(postcode)
    except (TypeError, ValueError):
        return None
    if 2084 <= p <= 2108:
        return "Northern Beaches"
    if 2153 <= p <= 2158 or p in (2120, 2125, 2126):
        return "Hills"
    if 2021 <= p <= 2036:
        return "East"
    if 2037 <= p <= 2050 or 2130 <= p <= 2133 or p in (2203, 2204):
        return "Inner West"
    if 2000 <= p <= 2020 or p == 2127:  # 2127 = Sydney Olympic Park (big arena shows)
        return "City"
    return None


def to_date(d, mo, y):
    if y:
        return dt.date(int(y), MONTHS[mo], int(d))
    x = dt.date(TODAY.year, MONTHS[mo], int(d))
    return x if x >= TODAY - dt.timedelta(days=60) else x.replace(year=TODAY.year + 1)


def parse_dates(text):
    hits = DATE_RE.findall(text)
    if not hits:
        return None
    first, last = hits[0], hits[-1]
    end = to_date(*last)
    start = to_date(first[0], first[1], first[2] or last[2])
    if start > end:  # a range that crosses New Year
        start = start.replace(year=start.year - 1)
    return start, end


def fmt_time(h, m, ap):
    return f"{int(h)}{'' if m == '00' else ':' + m}{ap.lower()}"


def guess_kind(text):
    t = text.lower()
    if "market" in t:
        return "market"
    if "comedy" in t:
        return "comedy"
    if any(w in t for w in ("concert", "live music", "gig")):
        return "music"
    if any(w in t for w in ("theatre", "musical", "play ")):
        return "theatre"
    if any(w in t for w in ("food", "wine", "festa", "taste")):
        return "food"
    return "fest"


def wanted(text):
    t = text.lower()
    return any(k in t for k in KEEP) and not any(s in t for s in SKIP)


def card_for(a, event_urls_ok):
    """Climbs from a title link to the card that holds exactly one event and a date."""
    node = a
    for _ in range(8):
        node = node.parent
        if node is None:
            return None
        links = {x["href"] for x in node.find_all("a", href=True) if event_urls_ok(x["href"])}
        if len(links) > 1:
            return None
        if DATE_RE.search(node.get_text(" ", strip=True)):
            return node
    return None


def scrape_listing(url, base, prefix, skip_slugs, area, default_suburb, src):
    soup = get(url)
    out, seen = [], set()
    ok = lambda h: urljoin(base, h).startswith(base + prefix) and urljoin(base, h).rstrip("/").split("/")[-1] not in skip_slugs
    for a in soup.find_all("a", href=True):
        link = urljoin(base, a["href"])
        title = a.get_text(" ", strip=True)
        if not title or link in seen or not ok(a["href"]):
            continue
        card = card_for(a, ok)
        if card is None:
            continue
        seen.add(link)
        text = card.get_text(" ", strip=True)
        before = text.split(title)[0]
        dates = parse_dates(before)
        if not dates or dates[1] < TODAY or dates[0] > HORIZON or not wanted(text):
            continue
        suburb = next((s for s in IW_SUBURBS if s in before), default_suburb)
        tm = TIME_RE.search(before)
        out.append({"k": guess_kind(text), "n": title, "a": area, "s": suburb, "v": "See listing",
                    "st": dates[0].isoformat(), "en": dates[1].isoformat() if dates[1] != dates[0] else 0,
                    "t": f"{fmt_time(*tm.group(1, 2, 3))}\u2013{fmt_time(*tm.group(4, 5, 6))}" if tm else "See listing",
                    "x": "", "u": link, "src": src})
    return out, len(seen)


def inner_west():
    base, out = "https://www.innerwest.nsw.gov.au", []
    for page in range(30):  # 25 pages existed in Oct 2026; stops when a page has no listings
        got, seen = scrape_listing(f"{base}/whats-on?query&page={page}", base, "/whats-on/",
                                   {"accessible-events"}, "Inner West", "Inner West", "inner_west")
        out += got
        if seen == 0:
            break
    return out


def northern_beaches():
    base, out = "https://www.northernbeaches.nsw.gov.au", []
    cal = base + "/things-to-do/whats/event-calendar"
    for page in range(8):
        url = cal if page == 0 else f"{cal}?end_date={TODAY.strftime('%d/%m/%Y')}&page={page}"
        got, seen = scrape_listing(url, base, "/things-to-do/whats-on/", set(),
                                   "Northern Beaches", "Northern Beaches", "northern_beaches")
        out += got
        if seen == 0:
            break
    return out


def ticketmaster():
    key = os.environ.get("TICKETMASTER_KEY")
    if not key:
        raise RuntimeError("TICKETMASTER_KEY is not set")
    out, page = [], 0
    while True:
        r = requests.get("https://app.ticketmaster.com/discovery/v2/events.json", headers=UA, timeout=30, params={
            "apikey": key, "countryCode": "AU", "stateCode": "NSW", "city": "Sydney",
            "startDateTime": TODAY.strftime("%Y-%m-%dT00:00:00Z"),
            "endDateTime": HORIZON.strftime("%Y-%m-%dT00:00:00Z"),
            "size": 200, "page": page, "sort": "date,asc"})
        r.raise_for_status()
        data = r.json()
        for e in data.get("_embedded", {}).get("events", []):
            c = (e.get("classifications") or [{}])[0]
            seg, genre = c.get("segment", {}).get("name", ""), c.get("genre", {}).get("name", "")
            venue = (e.get("_embedded", {}).get("venues") or [{}])[0]
            area = area_for(venue.get("postalCode"))
            if seg == "Sports" or not area:
                continue
            if seg == "Music":
                kind = "music"
            elif seg == "Arts & Theatre":
                kind = "comedy" if "comed" in genre.lower() else "theatre"
            else:
                kind = "fest"
            start = e.get("dates", {}).get("start", {})
            lt = start.get("localTime", "")[:5]
            tm = f"{int(lt[:2]) % 12 or 12}{'' if lt[3:] == '00' else ':' + lt[3:]}{'pm' if int(lt[:2]) >= 12 else 'am'}" if lt else "Times on listing"
            out.append({"k": kind, "n": e["name"], "a": area, "s": venue.get("city", {}).get("name", ""),
                        "v": venue.get("name", ""), "st": start.get("localDate"), "en": 0, "t": tm,
                        "x": "", "u": e.get("url"), "src": "ticketmaster"})
        page += 1
        if page >= data.get("page", {}).get("totalPages", 1) or page >= 5:  # API caps deep paging
            break
        time.sleep(DELAY)
    return [e for e in out if e["st"]]


def main():
    try:
        old = json.load(open("events.json"))
    except (OSError, ValueError):
        old = {}
    events, sources = [], {}
    for name, fn in [("ticketmaster", ticketmaster), ("inner_west", inner_west), ("northern_beaches", northern_beaches)]:
        try:
            got, err = fn(), None
        except Exception as ex:  # one broken source must not stop the rest
            got, err = [], str(ex)[:150]
        prev_count = old.get("sources", {}).get(name, {}).get("count", 0)
        warn = None
        if err:
            warn = "failed, showing older listings"
        elif not got:
            warn = "found nothing, showing older listings"
        elif prev_count and len(got) < prev_count * 0.4:
            warn = f"only {len(got)} events (was {prev_count})"
        if err or not got:  # keep the last good copy of events that haven't ended yet
            got = [e for e in old.get("events", []) if e.get("src") == name and (e.get("en") or e["st"]) >= TODAY.isoformat()]
        sources[name] = {"count": len(got), "warning": warn, "error": err}
        events += got
    seen, unique = set(), []
    for e in sorted(events, key=lambda e: e["st"]):
        key = re.sub(r"[^a-z0-9]", "", e["n"].lower()) + e["st"]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    with open("events.json", "w") as f:
        json.dump({"updated": dt.datetime.now(dt.timezone.utc).isoformat(), "sources": sources, "events": unique}, f, indent=1)
    print({k: (v["count"], v["warning"]) for k, v in sources.items()})


if __name__ == "__main__":
    main()
