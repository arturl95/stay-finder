---
name: stay-finder
description: Use when someone wants to find an Airbnb (or short-term rental) for specific dates and a budget in a city or named neighbourhoods, with must-haves and deal-breakers ("find me a place in X for these dates under Y, must have Z, nothing that says W"). Searches Airbnb, opens every listing under budget, applies keyword rules to the hosts' own text, and builds one local HTML page with a map, filters, favourites and full photo tours.
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

To change rules or filters afterwards: edit the config and run `python -m stayfinder build /path/to/trip.json`
(no new downloads). To get fresh prices and availability: `run ... --refresh`.

## 4. Open the page and report

Open the page in the user's browser (`open <path>` on macOS, `xdg-open` on Linux). Report:

- how many listings Airbnb returned, how many were under budget in the areas, how many are on the page,
  and how many each `exclude` rule removed;
- the caveats, every time:
  - keyword rules can miss rules worded differently, or told only after booking; the quotes on the
    page show what was matched;
  - "available" only means the listing appeared in a search for these dates; check the dates on the
    listing before messaging the host;
  - prices are Airbnb's search totals, before taxes where Airbnb adds them at checkout;
  - map pins are approximate near area borders.

Favourites and deletions are saved in the browser per page file name.
