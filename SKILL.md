---
name: stay-finder
description: Use when someone wants to find an Airbnb (or short-term rental) for specific dates and a budget in a city or named neighbourhoods, with must-haves and deal-breakers ("find me a place in X for these dates under Y, must have Z, nothing that says W"). Searches Airbnb, opens every listing under budget, applies keyword rules to the hosts' own text, can judge each listing from its photos, and builds one local HTML page with a map, filters, favourites and full photo tours.
---

# stay-finder

The tool lives in this skill's folder (`stayfinder/`, `config.example.json`, `README.md`). It needs
Python 3.10+ and nothing else. Read `README.md` for the config keys and limits.

## 1. Ask for what is missing (one message, only the gaps)

- **Where:** city and neighbourhoods. Named neighbourhoods give the best map; a `bbox` works for anywhere else.
- **When:** check-in and check-out dates.
- **Who:** number of adults.
- **Budget:** the most for the whole stay, and the currency.
- **Must-haves:** e.g. entire place, wifi, kitchen, washer, workspace, balcony, lift, pets allowed.
- **Deal-breakers:** things that rule a place out, e.g. shared bathroom, no pets, stairs only, a host
  rule they cannot live with.
- Studios OK or not.
- **What it should look like:** anything visible that matters (light, view, space, a real bedroom,
  condition, style). If there is something, step 4 judges the photos.

Do not ask for anything already given.

## 2. Write the config

Copy `config.example.json` to a working folder outside this repository (e.g. the user's project or a
scratch folder) and fill it in:

- `areas`: `"Neighbourhood, City, Country"` per area. If OpenStreetMap has no outline for a name, the
  run stops and says so; try the official district name, or use `bbox`.
- Must-haves Airbnb can filter: `entire_place: true`; `amenity_ids: [4]` for wifi, `[8]` for kitchen
  (only these two are verified). Everything else becomes an `amenity_filters` entry (a must-have
  checkbox matched against amenity names, e.g. "Washer", "Dedicated workspace", "Elevator") or a
  `highlight` rule over the text.
- **Deal-breakers become rules.** For each one, write a regex `pattern` that catches how hosts phrase
  it, in English and in the local language of the area (descriptions are searched both ways). Use
  `exclude` when it rules a place out for certain, `flag` when the user should judge the host's words.
  Add `unless` for common opposite phrasings ("pets allowed" vs "pets not allowed"). Keep labels short:
  they become checkbox names.
- `studio_pattern` if the user does not want studios, e.g. `"\\b(studio|loft|kitchenette)\\b"` plus the
  local word.
- `max_total` is the whole-stay total; `output` is where the page goes.

## 3. Run

```sh
cd <this skill's folder>
python -m stayfinder run /path/to/trip.json
```

It prints progress and finally the page path. Typical: a few minutes for two neighbourhoods. If it
stops because more than `max_listings` match, lower the budget or narrow the areas (or raise
`max_listings` if the user wants them all). Any other error names what is wrong; fix the config, do
not work around it.

To change rules, filters or photo verdicts afterwards: edit the config (or the verdicts file) and run
`python -m stayfinder build /path/to/trip.json` (no new downloads). To get fresh prices and availability: `run ... --refresh`.

## 4. Judge the photos (when the user cares about anything visible)

Do this step when the user asked for anything photos can show: light, a view, space, a real bedroom,
condition, style, a balcony. Skip it otherwise.

1. Download the photos (first 12 of each listing on the page, small size, cached):

   ```sh
   python -m stayfinder photos /path/to/trip.json        # --max N for another number per listing
   ```

   It prints the folder (`.cache/photos/` next to the config) and writes `index.json` there: per
   listing id its name, area, price, overview (bedrooms, beds, baths), how many photos the full tour
   has, and the downloaded files with their room labels.
2. For each listing, Read its image files (and its `index.json` entry) and judge it against what the
   user asked for. Write the verdicts to a JSON file next to the config:

   ```json
   {"123456": {"verdict": "great", "note": "Living room and bedroom both have big windows with direct sun; open view over low roofs."}}
   ```

   `verdict` is `great`, `ok` or `no`; `note` is one sentence on what the photos actually show.
   Listings you did not judge are shown as "not checked".
3. Set `"judgements": "photo-verdicts.json"` in the config (path relative to the config) and run
   `python -m stayfinder build /path/to/trip.json`. Each card then shows "Photos: <verdict>" and the
   note, map dots are coloured by verdict, and the page gets a "Photo check" filter (default: hide
   `no`) and a "best photos first" sort. Unknown listing ids or bad verdicts stop the build with an error.

**Many listings:** split the ids from `index.json` across parallel subagents (about 15–25 listings
each). Give each one the user's wishes, its list of ids, the photos folder, the guidance below, and its
own output file (`photo-verdicts-1.json`, `photo-verdicts-2.json`, ...). Then set `judgements` to the
list of those files (`["photo-verdicts-1.json", "photo-verdicts-2.json"]`); `build` merges them and
stops if a listing is judged twice.

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
- The judge sees only the first photos (as many as `--max`); `photos_in_tour` in `index.json` says how
  many the full tour has. When the deciding room is not among them, say so in the note.

## 5. Open the page and report

Open the page in the user's browser (`open <path>` on macOS, `xdg-open` on Linux). Report:

- how many listings Airbnb returned, how many were under budget in the areas, how many are on the page,
  and how many each `exclude` rule removed;
- the caveats, every time:
  - keyword rules can miss rules worded differently, or told only after booking; the quotes on the
    page show what was matched;
  - "available" only means the listing appeared in a search for these dates; check the dates on the
    listing before messaging the host;
  - prices are Airbnb's search totals, before taxes where Airbnb adds them at checkout;
  - map pins are approximate near area borders;
  - if photos were judged: verdicts are a judgement from the photos the host chose to show (and only
    the first ones), not an inspection; hosts often show shared rooftops, pools or views from the roof
    as if they were the flat. Report how many listings were great / ok / no / not checked.

Favourites and deletions are saved in the browser per page file name.
