"""Read Airbnb's public search and listing pages.

Both pages embed their data as JSON in <script id="data-deferred-state-0">. Nothing here logs in or
calls a private API; it reads the same HTML a browser gets. If Airbnb changes that JSON, these
functions raise instead of guessing.
"""
import base64
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept-Language": "en",
}
STATE_RE = re.compile(r'<script id="data-deferred-state-0"[^>]*>(.*?)</script>', re.S)


class AirbnbFormatError(RuntimeError):
    """Airbnb's page did not have the shape this code expects."""


def fetch_html(url):
    """Download a page as text. HTTP errors propagate with the URL in the message."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Airbnb answered HTTP {e.code} for {url}") from e


def state_from_html(body, url):
    """The JSON embedded in <script id="data-deferred-state-0">."""
    m = STATE_RE.search(body)
    if not m:
        raise AirbnbFormatError(f"no data-deferred-state-0 in {url} (blocked, or Airbnb changed its page)")
    return json.loads(m.group(1))


def fetch_state(url):
    """Download a page and return its embedded JSON state."""
    return state_from_html(fetch_html(url), url)


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


# ---------- search ----------

def search_url(cfg, sw, ne, cursor=None):
    p = [
        ("checkin", cfg["checkin"]), ("checkout", cfg["checkout"]), ("adults", str(cfg["adults"])),
        ("ne_lat", ne[0]), ("ne_lng", ne[1]), ("sw_lat", sw[0]), ("sw_lng", sw[1]),
        ("search_by_map", "true"), ("zoom_level", "14"), ("search_type", "user_map_move"),
        ("currency", cfg["currency"]), ("locale", "en"),
        ("price_max", cfg["max_total"]), ("price_filter_input_type", "2"),  # 2 = price_max is the stay total
    ]
    if cfg["entire_place"]:
        p.append(("room_types[]", "Entire home/apt"))
    for a in cfg["amenity_ids"]:
        p.append(("amenities[]", str(a)))
    if cursor:
        p.append(("cursor", cursor))
    return "https://www.airbnb.com/s/homes?" + urllib.parse.urlencode(p)


def _money(txt):
    digits = re.sub(r"[^\d.]", "", txt.replace(",", ""))
    if not digits:
        raise AirbnbFormatError(f"cannot read price {txt!r}")
    return float(digits)


def parse_price(spd, nights):
    """(stay total, total before discount or None) from a search result's structuredDisplayPrice.
    Raises unless Airbnb labels the price as the total for exactly `nights` nights."""
    line = (spd or {}).get("primaryLine") or {}
    txt = line.get("discountedPrice") or line.get("price")
    if not txt:
        raise AirbnbFormatError(f"no total price in {json.dumps(spd)[:300]}")
    q = line.get("qualifier") or ""
    m = re.search(r"(\d+)\s+nights?\b", q)
    if not m or int(m.group(1)) != nights:
        raise AirbnbFormatError(f"price {txt!r} is labelled {q!r}, expected a total 'for {nights} nights'")
    orig = line.get("originalPrice")
    return _money(txt), (_money(orig) if orig and line.get("discountedPrice") else None)


def listing_id(global_id):
    """Airbnb's search ids are base64 of 'DemandStayListing:<numeric id>'."""
    raw = base64.b64decode(global_id).decode()
    kind, _, num = raw.partition(":")
    if kind != "DemandStayListing" or not num.isdigit():
        raise AirbnbFormatError(f"unexpected listing id {raw!r}")
    return num


def parse_search_page(state, nights):
    """One search page -> {"items": [...], "count": int, "cursors": [...]}.

    count is Airbnb's own number of matches for the whole map box; cursors are the page cursors
    Airbnb offers for it (at most 15 pages, so at most about 270 results per box)."""
    ss = _find_key(state, "staysSearch")
    if ss is None:
        raise AirbnbFormatError("no staysSearch in search page")
    res = ss.get("results")
    if not res or "searchResults" not in res:
        raise AirbnbFormatError("staysSearch has no results.searchResults")
    panel = _find_key(res, "filterPanel")
    if not panel or not isinstance(panel.get("resultCount"), int):
        raise AirbnbFormatError("search page has no filterPanel.resultCount")
    out = []
    for r in res["searchResults"]:
        l = r.get("demandStayListing")
        if not l or not l.get("id"):
            continue  # ads and other non-listing tiles
        c = l["location"]["coordinate"]
        name = (r.get("nameLocalized") or {}).get("localizedStringWithTranslationPreference") \
            or (((l.get("description") or {}).get("name")) or {}).get("localizedStringWithTranslationPreference")
        if not name:
            raise AirbnbFormatError(f"search result {l['id']} has no name")
        total, before = parse_price(r.get("structuredDisplayPrice"), nights)
        out.append({
            "id": listing_id(l["id"]), "name": name,
            "lat": c["latitude"], "lng": c["longitude"],
            "total": total, "total_before_discount": before,
        })
    cursors = (res.get("paginationInfo") or {}).get("pageCursors") or []
    return {"items": out, "count": panel["resultCount"], "cursors": cursors}


def _quarters(sw, ne):
    ml, mg = (sw[0] + ne[0]) / 2, (sw[1] + ne[1]) / 2
    return [((sw[0], sw[1]), (ml, mg)), ((sw[0], mg), (ml, ne[1])),
            ((ml, sw[1]), (ne[0], mg)), ((ml, mg), (ne[0], ne[1]))]


def search_tile(cfg, nights, sw, ne, found, fetch, log=print, depth=0, max_depth=8, short=None):
    """Search one map box and add its results to `found`.

    Airbnb pages through at most about 15 × 18 results, and its pages overlap (the same listing can
    come back on two pages while another never shows). So a box is split into 4 and each quarter
    searched again when it has more matches than its pages hold, or when paging returned fewer
    distinct listings than Airbnb counted. A box that is still short at max_depth adds its shortfall
    to short[0], which the page reports."""
    short = short if short is not None else [0]
    first = parse_search_page(fetch(search_url(cfg, sw, ne)), nights)
    count, cursors = first["count"], first["cursors"]
    if count == 0:
        return short[0]
    per_page = len(first["items"])
    if per_page == 0 or not cursors:
        raise AirbnbFormatError(f"Airbnb reports {count} matches for box {sw}–{ne} but returned "
                                f"{per_page} results and {len(cursors)} page cursors")
    box = {it["id"]: it for it in first["items"]}
    # One cursor means Airbnb itself puts the whole box on one page. Otherwise the first page is full,
    # so its length is the page size, and more matches than the pages hold means the box is too big.
    single_page = len(cursors) == 1
    too_big = not single_page and count > len(cursors) * per_page
    if not too_big:
        for cur in cursors[1:-(-count // per_page)]:
            items = parse_search_page(fetch(search_url(cfg, sw, ne, cur)), nights)["items"]
            if not items:
                break
            box.update((it["id"], it) for it in items)
    found.update(box)
    if len(box) >= count:
        log(f"  box {sw[0]:.4f},{sw[1]:.4f}–{ne[0]:.4f},{ne[1]:.4f}: {count} matches, {len(found)} found so far")
        return short[0]
    if too_big and depth >= max_depth:
        raise RuntimeError(f"box {sw}–{ne} still has {count} matches after {max_depth} splits; "
                           f"lower max_total or pick smaller areas")
    if single_page or depth >= max_depth:  # splitting cannot recover what Airbnb counted but did not return
        short[0] += count - len(box)
        log(f"  box {sw[0]:.4f},{sw[1]:.4f}–{ne[0]:.4f},{ne[1]:.4f}: Airbnb counted {count}, returned {len(box)}")
        return short[0]
    for a, b in _quarters(sw, ne):
        search_tile(cfg, nights, a, b, found, fetch, log, depth + 1, max_depth, short)
    return short[0]


def search(cfg, nights, bbox, fetch, log=print):
    """bbox = [south, west, north, east]. Returns ({listing id: search result}, shortfall), where
    shortfall is how many listings Airbnb counted but never returned."""
    s, w, n, e = bbox
    found = {}
    short = search_tile(cfg, nights, (s, w), (n, e), found, fetch, log)
    return found, short


# ---------- listing page ----------

def listing_url(cfg, lid):
    q = urllib.parse.urlencode({"check_in": cfg["checkin"], "check_out": cfg["checkout"],
                                "adults": cfg["adults"], "currency": cfg["currency"], "locale": "en"})
    return f"https://www.airbnb.com/rooms/{lid}?{q}"


def _ugc(x):
    """(translated, original) text of an Airbnb user-written text object."""
    if not x:
        return "", ""
    t = x.get("localizedStringWithTranslationPreference") or x.get("localizedString") or ""
    return t, x.get("source") or ""


def _strip_html(h):
    return html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>|</p>", "\n", h or ""))).strip()


def parse_listing(state, lid):
    """Pull what the page shows about one listing out of its state JSON."""
    try:
        n = state["niobeClientData"][0][1]["data"]["node"]["pdpPresentation"]
    except (KeyError, IndexError, TypeError) as e:
        raise AirbnbFormatError(f"listing {lid}: no pdpPresentation ({e!r})") from e
    if not n:
        raise AirbnbFormatError(f"listing {lid}: pdpPresentation is empty (listing removed or page blocked)")
    try:
        return _parse_pdp(n, lid)
    except (KeyError, IndexError, TypeError) as e:
        raise AirbnbFormatError(f"listing {lid}: page data is missing {e}; Airbnb may have changed its format") from e


def _parse_pdp(n, lid):
    q = n["quality"]
    st = q["listingRatingStats"]["overallRatingStats"]
    count = int(st["ratingCount"] or 0)

    photos = [[it["image"]["uri"], stop["name"] or ""]
              for stop in (n.get("mediaTour") or {}).get("stops", []) for it in stop["items"] if it.get("image")]
    if not photos:
        photos = [[e["node"]["image"]["uri"], ""] for e in n["heroMedia"]["edges"] if e["node"].get("image")]
    if not photos:
        raise AirbnbFormatError(f"{lid}: no photos")

    desc_t, desc_src = _ugc(n["descriptions"]["longDescriptionHtml"])
    rules, originals = [], [_strip_html(desc_src)]
    for g in n["rules"]["groupItems"]:
        for it in g["items"]:
            d = it.get("description")
            if d and d.get("__typename") == "PdpUGCText":
                t, src = _ugc(d["content"])
                rules.append(f"{it['title']}:\n{t.strip()}")
                originals.append(src)
            else:
                extra = (d or {}).get("text")
                rules.append(it["title"] + (f" ({extra})" if extra else ""))

    amenities = [a["title"] for g in n["amenities"]["seeAllAmenitiesGroups"]
                 if g.get("title") != "Not included" for a in g["amenities"] if a.get("available") is not False]
    host = (n.get("hostInfo") or {}).get("passportData") or {}
    return {
        "kind": n["sharingConfig"]["propertyType"],
        "overview": [x for x in n["overview"]["items"] if "guest" not in x],
        "rating": round(float(st["ratingAverage"]), 2) if count else None,
        "reviews": count,
        "guest_favourite": bool(q.get("isGuestFavorite")),
        "superhost": bool(host.get("isSuperhost")),
        "category_ratings": {c["categoryType"]: c["localizedRating"] for c in (q.get("categoryRatings") or [])},
        "photos": photos,
        "rooms": list(dict.fromkeys(p[1] for p in photos if p[1])),
        "description": _strip_html(desc_t),
        "house_rules": rules,
        "original_text": "\n".join(o for o in originals if o),
        "amenities": amenities,
    }
