# stay-finder

Find Airbnb stays for your dates and budget in the neighbourhoods you name, drop the ones whose hosts
say something you can't live with, and get **one local HTML page** to choose from:

- a map with the neighbourhood outlines and a dot per listing (click a dot to jump to its card)
- filters: price, area, rating, studios, each of your own rules, must-have amenities
- favourites and deletions saved in your browser
- the full photo tour of every listing (with room names), category ratings, description and house rules
- for every rule that fired, the host's own words, so you can judge it yourself

It reads Airbnb's public search and listing pages, the same HTML a browser gets. It does not log in,
book, or contact anyone. Python 3.10+, standard library only.

## Quick start

```sh
cp config.example.json my-trip.json        # then edit areas, dates, budget, rules
python -m stayfinder run my-trip.json      # search, open each listing, build the page
open out/lisbon-week.html                  # or whatever "output" says
```

After changing only rules, `studio_pattern`, `amenity_filters`, `min_rating` or `title`, rebuild
without touching Airbnb:

```sh
python -m stayfinder build my-trip.json
```

`run` caches every page it downloads in `.cache/` next to the config, so running it again is fast.
Use `python -m stayfinder run my-trip.json --refresh` to download everything again (new prices and
availability). If you change the search itself (areas, dates, guests, budget, currency, `entire_place`,
`amenity_ids`, `border_tolerance_km`), `build` refuses and tells you to `run` again.

## How it works

1. **Areas.** Each place name in `areas` is looked up on OpenStreetMap (Nominatim) and its outline is
   used. A listing counts as in an area when its map pin is inside the outline or within
   `border_tolerance_km` of it (Airbnb moves pins a little for privacy).
2. **Search.** Airbnb is searched over the box around all areas, for your dates and guests, with
   `max_total` as the price limit for the whole stay. Airbnb only pages through about 270 results per
   map box and its pages overlap, so a box with more matches, or one whose pages came back short, is
   split into four and searched again until each box is complete.
3. **Listing pages.** Every listing under budget inside an area has its page opened: photo tour,
   overview, ratings, house rules, amenities, description (translated, plus the host's original).
4. **Rules** run over the title, description and house rules, in English and in the host's language.
5. **Page.** Everything is embedded as JSON in one HTML file; it needs no server.

Requests are polite: at least 0.5 s between Airbnb requests (4 listing pages at a time at most) and
1 request per second to Nominatim.

## Config reference

| key | required | meaning |
|---|---|---|
| `title` | yes | heading of the page |
| `areas` | one of `areas`/`bbox` | place names, e.g. `"Alfama, Lisbon, Portugal"`. The label on the page is the text before the first comma, so labels must differ. |
| `bbox` | one of `areas`/`bbox` | `[south, west, north, east]` in degrees, instead of named areas |
| `checkin`, `checkout` | yes | `YYYY-MM-DD` |
| `adults` | yes | 1–16 |
| `currency` | yes | 3-letter code, e.g. `EUR`, `USD` |
| `max_total` | yes | most you would pay for the whole stay, in `currency` |
| `output` | yes | path of the HTML file to write (relative to the config file), ending in `.html` |
| `entire_place` | no (false) | only entire homes |
| `amenity_ids` | no (`[]`) | Airbnb amenity numbers to require in the search itself. Verified: `4` = wifi, `8` = kitchen. Other numbers are passed to Airbnb unchecked. |
| `min_rating` | no (0) | leave out listings rated below this; listings with no reviews are kept (the page can hide them) |
| `border_tolerance_km` | no (0.2) | how far outside an area outline a pin may be and still count |
| `rules` | no (`[]`) | keyword rules, see below |
| `studio_pattern` | no | regular expression; a listing whose title, property type or overview matches is marked "studio / loft" and can be hidden |
| `amenity_filters` | no (`[]`) | amenity names that become "must have" checkboxes. Matching is case-insensitive and by substring: `"Washer"` matches `"Free washer – In unit"`. `build` prints a note when no listing matches a name. |
| `max_listings` | no (300) | stop before opening more listing pages than this; lower the budget or areas, or raise it |

Keys starting with `_` are ignored, so you can use `"_comment"`. Unknown keys are an error, and all
problems in a config are reported at once.

## Rules

```json
{"label": "No pets", "action": "flag", "pattern": "\\b(no pets|pets? not allowed)\\b"}
{"label": "Balcony or terrace", "action": "highlight", "pattern": "\\b(balcon(y|ies)|terrace)\\b"}
{"label": "Shared bathroom", "action": "exclude", "pattern": "\\bshared bath(room)?\\b"}
{"label": "Pets welcome", "action": "highlight", "pattern": "\\bpets? (are )?welcome\\b", "unless": "\\bnot\\b"}
```

- `pattern` is a case-insensitive Python regular expression, matched sentence by sentence.
- `unless` (optional) cancels a match when it also matches the same sentence.
- `exclude`: the listing is hidden. "Show: excluded by rules" lists them with the quote.
- `flag`: the listing stays, with a warning quoting the host, and a "hide" checkbox.
- `highlight`: a green note with the quote, and a "must" checkbox.
- Write patterns in the host's language too when the area calls for it (descriptions are searched both
  translated and original).

## Limits (read these)

- **Keyword rules can miss things.** A host can say the same thing in words your pattern does not
  cover, or a pattern can match a sentence that means the opposite. Every hit shows the quote; read it.
  Rules a host only tells you after booking are not seen at all.
- **Availability is only "appeared in a search for these dates".** That is not a confirmed booking:
  open the listing with your dates before you message the host.
- **Prices** are Airbnb's stay totals as shown in search, before taxes where Airbnb adds them at checkout.
- **Map pins are approximate**, so a place near a border may really be on the other side.
- **Airbnb can change its page format at any time.** When it does, stay-finder stops with an error
  naming what is missing instead of guessing. Fixes go in `stayfinder/airbnb.py`.
- **Respect Airbnb's terms.** This is for your own trip planning at low volume: one search, a few
  hundred pages, with pauses between requests. Do not use it to collect data at scale or republish
  it. The cache and the page contain hosts' listing data; keep them to yourself.

## Tests

```sh
python -m unittest discover -s tests
```

They run offline on small hand-made fixtures. If `node` is installed, the page's script is also run
against a stub DOM and every filter, favourites, the photo viewer and the map are exercised.
`node tests/page_harness.js out/your-page.html` runs the same checks on a page you built.

## Use with Claude Code

This repository is a Claude Code plugin with one skill. Install it from inside Claude Code:

```
/plugin marketplace add arturl95/stay-finder
/plugin install stay-finder@stay-finder
```

Or clone it into `~/.claude/skills/stay-finder`. Then ask for "a stay in <place> for <dates> under <budget>":
the skill asks for anything missing, writes the config, runs the search and opens the page.

## License

MIT
