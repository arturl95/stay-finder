# stay-finder (Claude Code plugin)

Find Airbnb stays for your dates and budget in the neighbourhoods you name, drop the ones whose hosts
say something you can't live with, and get **one local HTML page** to choose from:

- a zoomable street map with the neighbourhood outlines and a dot per listing
- filters: price, area, rating, studios, each of your own rules, must-have amenities
- favourites and deletions saved in your browser
- the full photo tour of every listing, category ratings, description and house rules
- for every rule that fired, the host's own words, so you can judge it yourself
- optionally, a photo check: a verdict and a one-line note per listing from looking at its photos

## Install

In Claude Code 2.1.275 or later:

```
/plugin install stay-finder --marketplace arturl95/stay-finder
```

or, in any version with plugins:

```
/plugin marketplace add arturl95/stay-finder
/plugin install stay-finder@stay-finder
```

Then ask Claude for "a stay in <place> for <dates> under <budget>, must have …, nothing that says …".
It asks for anything missing, searches, and opens the page.

## What it contains

One skill (`skills/stay-finder/SKILL.md`): instructions only. The plugin ships no scripts, programs,
hooks or servers. Claude follows the instructions with its own tools (fetching web pages, reading
images, writing a file), so it asks your permission for those steps as usual.

## What data goes where

Claude, following the skill, makes these requests from your machine:

| to | what is sent | why |
|---|---|---|
| `www.airbnb.com` (public search and listing pages) | the map box of your areas, your dates, number of adults, currency and budget, as URL parameters of an ordinary search | to find listings and read their pages |
| `a0.muscache.com` (Airbnb's image server) | photo URLs of the listings | photo check and the page's photo strips |
| `nominatim.openstreetmap.org` | the neighbourhood names you gave | to get each area's outline |

When you open the page, your browser also loads map tiles for the area you look at from Esri
(`server.arcgisonline.com`), the listing photos from Airbnb's image server, and the map library
(Leaflet) from `unpkg.com` unless Claude inlined it into the page.

Nothing else. No account, login, name, email or contact detail is used or sent; nothing is sent to
the plugin's author or to any server of this plugin (it has none). Results stay in files on your
machine. It does not book or contact anyone.

## Limits

- Keyword rules can miss things a host words differently or only says after booking. Every hit shows the quote.
- "Available" means the listing appeared in a search for these dates, not a confirmed booking.
- Prices are Airbnb's search totals, before taxes Airbnb may add at checkout.
- Map pins are approximate.
- Photo verdicts judge only the photos the host chose.
- Airbnb can change its page format; the skill then stops and names what is missing.
- For your own trip planning at low volume. Respect Airbnb's terms; do not collect or republish at scale.

## Source and license

https://github.com/arturl95/stay-finder. The same repository also holds an optional ready-made
Python program that implements this method. MIT license, see `LICENSE`.
