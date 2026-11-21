---
name: stay-finder
description: Use when someone wants to find an Airbnb (or short-term rental) for specific dates and a budget in a city or named neighbourhoods, with must-haves and deal-breakers ("find me a place in X for these dates under Y, must have Z, nothing that says W"). Searches Airbnb's public pages, opens every listing under budget, applies keyword rules to the hosts' own text, can judge each listing from its photos, and builds one local HTML page with a zoomable map, filters, favourites and full photo tours.
---

# stay-finder

You do the whole job yourself with your own tools: an HTTP client to fetch pages, a way to parse JSON,
image reading for the photo check, and file writing for the page. Nothing here needs an account, a
login or an API key. Work in a scratch folder outside any repository.

Fail loudly. When a page lacks a field described below, stop and say which field on which URL is
missing (Airbnb changes its format now and then). Never guess a price, a coordinate or a rule hit.

## 1. Ask for what is missing (one message, only the gaps)

- **Where:** city and neighbourhoods (or a map box).
- **When:** check-in and check-out dates.
- **Who:** number of adults.
- **Budget:** the most for the **whole stay**, and the currency.
- **Must-haves:** e.g. entire place, wifi, kitchen, washer, workspace, balcony, lift, pets allowed.
- **Deal-breakers:** e.g. shared bathroom, no pets, stairs only, a house rule they cannot live with.
- Studios OK or not.
- **What it should look like:** light, view, space, a real bedroom, condition, style. If anything, do step 6.

Do not ask for anything already given.

### Budget discipline

- Never put a listing above the user's budget for the exact stay length on the page. The search
  filter alone is not proof: re-check every price you keep (step 3).
- If the user gave a budget for another length ("€80 a night", "€2,000 a month" for a 3-week stay),
  ask which total applies, or scale it proportionally to the real number of nights and say so in your
  reply and on the page ("budget scaled from €2,000/30 nights to €1,400 for 21 nights").

## 2. Areas: OpenStreetMap Nominatim

For each neighbourhood, request
`https://nominatim.openstreetmap.org/search?q=<Neighbourhood, City, Country>&format=json&polygon_geojson=1&limit=5`

- At most **1 request per second**, with an identifying `User-Agent` (e.g. `stay-finder (personal trip search)`).
  This is Nominatim's usage policy.
- Take the first result whose `geojson.type` is `Polygon` or `MultiPolygon`. Coordinates are
  `[lng, lat]`; keep each polygon's outer ring (`coordinates[0]`, or `coordinates[i][0]` for a multipolygon).
- No outline for a name: stop and tell the user; try the official district name, or ask for a map box.
- The search box is the bounding box of all outlines.
- A listing is **in an area** when its pin is inside the outline or within about 0.2 km of it
  (Airbnb moves pins a little for privacy). Say so on the page.

## 3. Search: Airbnb's search page

Fetch with a normal browser `User-Agent` and `Accept-Language: en`:

`https://www.airbnb.com/s/homes?checkin=YYYY-MM-DD&checkout=YYYY-MM-DD&adults=N&ne_lat=..&ne_lng=..&sw_lat=..&sw_lng=..&search_by_map=true&zoom_level=14&search_type=user_map_move&currency=EUR&locale=en&price_max=<stay total>&price_filter_input_type=2`

- `price_filter_input_type=2` makes `price_max` the **total for the stay**, not a nightly price.
- `room_types[]=Entire home/apt` for entire places only.
- `amenities[]=4` = wifi, `amenities[]=8` = kitchen (only these two numbers are verified; repeat the
  parameter for both). Other must-haves are checked on the listing pages instead.
- `cursor=<page cursor>` for the next pages.

**Where the data is.** The HTML contains `<script id="data-deferred-state-0" ...>JSON</script>`. No
such tag means you were blocked or the page changed: stop and say so. In the JSON, find the key
`staysSearch` (search recursively), then:

- `staysSearch.results.searchResults[]`: entries without `demandStayListing.id` are ads; skip them.
- `demandStayListing.id` is base64 of `DemandStayListing:<numeric id>`; the numeric id is the listing id.
- `demandStayListing.location.coordinate.latitude` / `.longitude`: the pin.
- Name: `nameLocalized.localizedStringWithTranslationPreference`, else
  `demandStayListing.description.name.localizedStringWithTranslationPreference`.
- Price: `structuredDisplayPrice.primaryLine`: `discountedPrice` (or `price` when there is no
  discount), `originalPrice` is the price before the discount. `qualifier` must say
  `for <N> nights` with N = the number of nights; if it does not, stop (it is not a stay total).
  Drop every result above the budget.
- `filterPanel.resultCount` (inside `results`): Airbnb's count of matches for the whole box.
- `results.paginationInfo.pageCursors`: the cursors of all pages for this box.

**Paging (Airbnb returns at most ~15 pages of ~18, and its pages overlap).** For each box:

1. Fetch the first page. `resultCount` 0: the box is done.
2. `per_page` = results on the first page. Matches but no results or no cursors: stop with an error.
3. One cursor means everything is on one page. Otherwise the box is **too big** when
   `resultCount > cursors × per_page`.
4. If not too big, fetch the pages for cursors 2 … ceil(resultCount / per_page); stop at an empty page.
   Collect by listing id (duplicates across pages are normal).
5. If you now hold at least `resultCount` distinct listings, the box is done. Otherwise split it into
   four quarters and search each the same way (up to 8 levels). A single-page box, or one still
   short at the deepest level, cannot be recovered: count the shortfall and report it on the page
   ("up to N more counted by Airbnb but not returned"). Still too big at the deepest level: stop and
   ask for a lower budget or smaller areas.

Keep only listings in an area (step 2). Tell the user how many that is before opening them; above
~300, ask to narrow the search first.

## 4. Listing pages

`https://www.airbnb.com/rooms/<id>?check_in=..&check_out=..&adults=N&currency=..&locale=en`

Same `data-deferred-state-0` JSON. Everything is under
`niobeClientData[0][1].data.node.pdpPresentation` (empty: listing removed or page blocked; stop and say which):

| what | path under `pdpPresentation` |
|---|---|
| photos with room names | `mediaTour.stops[]`: `name` and `items[].image.uri`; if absent, `heroMedia.edges[].node.image.uri` |
| overview | `overview.items` (e.g. "1 bedroom", "2 beds"; drop the "guests" item) |
| property type | `sharingConfig.propertyType` |
| rating | `quality.listingRatingStats.overallRatingStats.ratingAverage` and `.ratingCount` (0 = no reviews) |
| category ratings | `quality.categoryRatings[]`: `categoryType`, `localizedRating` |
| guest favourite / superhost | `quality.isGuestFavorite`, `hostInfo.passportData.isSuperhost` |
| description | `descriptions.longDescriptionHtml`: `localizedStringWithTranslationPreference` (or `localizedString`) is the English text, `source` the host's original; strip the HTML |
| house rules | `rules.groupItems[].items[]`: `title`, plus `description.content` (same translated/original pair) when `description.__typename` is `PdpUGCText`, else `description.text` |
| amenities | `amenities.seeAllAmenitiesGroups[]` except the group titled "Not included", `amenities[]` whose `available` is not `false`: `title` |

Photo URLs take a size: `<uri>?im_w=480` for strips, `?im_w=1200` for the large view.

**Be polite:** at least 0.5 s between Airbnb requests, at most 4 at a time. Save every fetched page
to disk so a rebuild does not fetch again. This is for one person's trip at low volume; do not
collect at scale or republish hosts' data.

## 5. The user's rules

Turn each must-have and deal-breaker into a keyword rule:

- `exclude`: rules a place out for certain (hidden, but viewable as "excluded by rules" with the quote).
- `flag`: stays, with a warning quoting the host and a "hide" checkbox. Use it when the user should
  judge the host's words.
- `highlight`: a green note with the quote and a "must" checkbox (e.g. balcony, pets welcome).

Write each rule as a case-insensitive regular expression in **English and the local language** of
the area, plus an optional `unless` pattern that cancels a match in the same sentence ("pets
allowed" vs "pets not allowed"). Match sentence by sentence over the title, the description and the
house rules, both the English text and the host's original. Keep the matching sentence as the quote.
Must-have amenities become checkboxes matched by substring against amenity titles ("Washer" matches
"Free washer – In unit"). Studios: a pattern such as `\b(studio|loft|kitchenette)\b` plus the local
word, over the title, property type and overview, shown as a "Studio / loft" chip and a hide checkbox.

## 6. Photo check (only when the user cares about anything visible)

Download the first ~12 photos of each listing still on the page at `?im_w=480`, save them as
`<id>/<nn>-<room name>.jpg`, look at them, and give each listing a verdict `great` / `ok` / `no`
and one sentence on what the photos actually show. Many listings: split them across parallel
subagents (15–25 each), each with the user's wishes, its ids, the guidance below and its own output
file; then merge, and stop if a listing was judged twice.

**Guidance for whoever judges the photos (pass this on to every judging agent, word for word):**

- Hosts often include photos of the building's **common areas** as if they were the unit: rooftop
  terraces, gyms, pools, lobbies, coworking rooms, lounges, BBQ areas, the building's gardens, and
  **views shot from the rooftop**. Room labels like "Gym", "Pool", "Exterior", "Additional photos",
  "Common area", or a terrace far bigger than the flat, are strong hints. Judge light, view and space
  only from photos clearly taken inside the unit (or from its own private balcony); a great view from
  the shared roof is not a view from the flat. Mention shared amenities separately in the note.
- Other traps:
  - wide-angle lenses that make small rooms look big;
  - a "1 bedroom" that is really a studio with a curtain or a half wall;
  - windows that look straight into another building or into an inner shaft;
  - night or lamp-lit photos that hide a dark room;
  - the same room photographed from several angles to pad the tour;
  - photos of a different unit in the same building (different floor, furniture or layout).
- `great` only when the unit's own photos clearly meet what the user asked for; `no` when the photos
  show it fails; `ok` otherwise (including when the photos do not show enough to tell). The note says
  what the photos actually show, not what the listing text claims.
- The judge sees only the first photos; the full tour may have more. When the deciding room is not
  among them, say so in the note.

## 7. The page: one local HTML file

Write a single `.html` file with all data embedded as JSON in a `<script>` (escape every `<` as
`<` so a listing name cannot close the tag). It needs no server.

- **Map:** Leaflet 1.9.4, either inlined into the page (escape any `</script` inside it as `<\/script`)
  or loaded from unpkg (`https://unpkg.com/leaflet@1.9.4/dist/leaflet.css` and `.../leaflet.js`).
  The page is opened as a local file, and `file://` pages send no `Referer`, which OpenStreetMap's
  own tile servers refuse. So use Esri's street tiles:
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}`
  (note `{y}` before `{x}`), `maxZoom: 19`, attribution
  `Tiles &copy; Esri, HERE, Garmin, OpenStreetMap contributors`, and
  `map.attributionControl.setPrefix(false)`. On the tile layer's `tileerror` event, show a visible
  message above the map. If `L` is undefined, show a visible error and throw; do not draw a
  substitute map.
  Area outlines as thin `L.polygon`s with a light fill. One `L.circleMarker` per listing, coloured by
  price band (or by photo verdict when judged), with a tooltip "name · price · verdict"; listings
  hidden by a filter as small grey rings. Click a marker: scroll to its card and highlight it. Hover
  a card: enlarge its marker. Re-fit the bounds only when the set of shown listings changes, not on
  every redraw. Do **not** add CSS like `svg{width:100%;height:auto}` that reaches into the map: it
  breaks Leaflet's vector layer.
- **Cards:** a horizontal photo strip (first 12, with room names; click opens a full-size viewer
  through the whole tour), name linking to the listing with the dates, area, type, overview, stay
  total and per night (with the pre-discount price struck through), rating and review count,
  category ratings, rule quotes, amenities, and collapsible description, house rules and full
  amenity list.
- **Filters:** max price, area checkboxes, min rating (+ include unrated), hide studios, a hide
  checkbox per `flag` rule, a must checkbox per `highlight` rule and per must-have amenity, the photo
  verdict (when judged), show all / favourites / deleted / excluded by rules, and sort (price, rating
  weighted by review count, most reviews, best photos first).
- **Favourites and deletions** in `localStorage`, keyed by the page's file name, with the filter
  settings. If storage throws, show a visible warning that marks will be lost; do not hide it.
- **Header and notes:** the search (dates, nights, adults, budget, areas), the counts (found by
  search, not returned, under budget in the areas, shown after exclude rules, photo verdicts), and
  the caveats below.

Then open the page in the user's browser.

## 8. Report

How many listings the search returned, how many were under budget in the areas, how many are on
the page, how many each `exclude` rule removed, and the photo verdict counts. Every time, the caveats:

- keyword rules can miss rules worded differently, or told only after booking; the quotes show what matched;
- "available" only means the listing appeared in a search for these dates; check the dates on the
  listing before messaging the host;
- prices are Airbnb's search totals, before taxes Airbnb may add at checkout;
- map pins are approximate near area borders;
- photo verdicts judge only the photos the host chose (and only the first ones), not an inspection.

A ready-made open-source program that implements this same method lives at
https://github.com/arturl95/stay-finder (optional; this skill does not need it).
