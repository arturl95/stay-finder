"""Photo check: download a few photos per listing for someone (or an AI agent) to look at, and read
back their verdicts.

`download_all` saves <folder>/<listing id>/<nn>-<room slug>.jpg and <folder>/index.json.
`load_judgements` reads {"<listing id>": {"verdict": "great"|"ok"|"no", "note": "..."}} files.
"""
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .airbnb import UA

VERDICTS = ("great", "ok", "no")
JUDGEMENT_KEYS = {"verdict", "note"}
IMAGE_WIDTH = 480   # the same small size the page shows in its photo strips
THREADS = 4
GAP_S = 0.2         # at least this long between two photo downloads, across all threads


class JudgementError(ValueError):
    pass


def slug(text):
    """'Full bathroom 2' -> 'full-bathroom-2', 'Área común' -> 'area-comun'. Photos without a room name
    get 'unlabelled'."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40].strip("-")
    return s or "unlabelled"


def image_url(uri, lid):
    if "?" in uri:
        raise RuntimeError(f"listing {lid}: photo URL already has a query string, cannot add a size: {uri}")
    return f"{uri}?im_w={IMAGE_WIDTH}"


def fetch_image(url):
    """Bytes of one image. Raises on an HTTP error, a non-image answer or an empty body."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            ctype = r.headers.get("Content-Type", "")
            body = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {url}") from e
    if not ctype.startswith("image/"):
        raise RuntimeError(f"{url} answered {ctype!r}, not an image")
    return body


def plan(records, folder, max_n):
    """What to download and what goes in index.json, without touching the network.
    Returns (index, jobs); jobs are (listing id, number, url, path)."""
    index, jobs = {}, []
    for r in records:
        files = []
        for i, (uri, room) in enumerate(r["photos"][:max_n]):
            name = f"{i + 1:02d}-{slug(room)}.jpg"
            files.append({"file": f"{r['id']}/{name}", "room": room, "url": image_url(uri, r["id"])})
            jobs.append((r["id"], i + 1, files[-1]["url"], Path(folder) / r["id"] / name))
        index[r["id"]] = {
            "name": r["name"], "area": r["area"], "price": r["total"], "kind": r["kind"],
            "overview": r["overview"], "url": r["url"],
            "photos_in_tour": len(r["photos"]), "photos": files,
        }
    return index, jobs


def _previous_urls(index_path):
    """file -> url from the last index.json, so a file is only reused when it is the same photo."""
    if not index_path.exists():
        return {}
    old = json.loads(index_path.read_text(encoding="utf-8"))
    return {p["file"]: p["url"] for entry in old["listings"].values() for p in entry["photos"]}


def download_all(cfg, records, folder, max_n, log, download):
    """Download the first max_n photos of each record into folder and write folder/index.json.
    A file that is already there (same photo URL as last time) is skipped. Returns a summary dict."""
    if not isinstance(max_n, int) or isinstance(max_n, bool) or max_n < 1:
        raise ValueError(f"--max must be a whole number of at least 1, got {max_n!r}")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / "index.json"
    previous = _previous_urls(index_path)
    index, jobs = plan(records, folder, max_n)
    todo = [j for j in jobs if not (j[3].exists() and previous.get(f"{j[0]}/{j[3].name}") == j[2])]
    log(f"Photo check: {len(records)} listings, {len(jobs)} photos, {len(todo)} to download "
        f"({len(jobs) - len(todo)} already there).")

    lock, last, done = threading.Lock(), [0.0], [0]

    def get(job):
        lid, n, url, path = job
        with lock:
            delay = last[0] + GAP_S - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            last[0] = time.monotonic()
        try:
            body = download(url)
        except Exception as e:
            raise RuntimeError(f"listing {lid}: photo {n} could not be downloaded ({url}): {e}") from e
        if not body.startswith(b"\xff\xd8\xff"):
            raise RuntimeError(f"listing {lid}: photo {n} is not a JPEG ({url}, {len(body)} bytes)")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        with lock:
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(todo):
                log(f"  photos: {done[0]}/{len(todo)}")

    with ThreadPoolExecutor(THREADS) as pool:
        list(pool.map(get, todo))

    tmp = index_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"title": cfg["title"], "currency": cfg["currency"], "max_per_listing": max_n,
                               "listings": index}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(index_path)
    return {"folder": folder, "index": index_path, "listings": len(records), "photos": len(jobs),
            "downloaded": len(todo)}


def _check_file(path, data, known_ids):
    errs = []
    if not isinstance(data, dict):
        return [f"{path}: must be a JSON object {{\"<listing id>\": {{\"verdict\": ..., \"note\": ...}}}}"]
    for lid, j in data.items():
        if lid not in known_ids:
            errs.append(f"{path}: listing {lid!r} is not on the page (unknown id, or left out by min_rating)")
            continue
        if not isinstance(j, dict):
            errs.append(f"{path}: listing {lid}: must be an object with verdict and note")
            continue
        extra, missing = set(j) - JUDGEMENT_KEYS, JUDGEMENT_KEYS - set(j)
        if extra:
            errs.append(f"{path}: listing {lid}: unknown keys {sorted(extra)}; allowed: {sorted(JUDGEMENT_KEYS)}")
        if missing:
            errs.append(f"{path}: listing {lid}: missing {sorted(missing)}")
        if "verdict" in j and j["verdict"] not in VERDICTS:
            errs.append(f"{path}: listing {lid}: verdict must be one of {list(VERDICTS)}, got {j['verdict']!r}")
        if "note" in j and not (isinstance(j["note"], str) and j["note"].strip()):
            errs.append(f"{path}: listing {lid}: note must be a non-empty string")
    return errs


def load_judgements(paths, base, known_ids):
    """Read one judgements file, or a list of them (one per judging agent), relative to `base`.
    Returns {listing id: {"verdict", "note"}}. Raises JudgementError listing every problem."""
    paths = [paths] if isinstance(paths, str) else paths
    out, errs, seen_in = {}, [], {}
    for p in paths:
        full = Path(p) if Path(p).is_absolute() else Path(base) / p
        if not full.is_file():
            errs.append(f"judgements file not found: {full}")
            continue
        try:
            data = json.loads(full.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errs.append(f"{full} is not valid JSON: {e}")
            continue
        file_errs = _check_file(full, data, known_ids)
        errs += file_errs
        if file_errs:
            continue
        for lid, j in data.items():
            if lid in seen_in:
                errs.append(f"listing {lid} is judged twice: in {seen_in[lid]} and in {full}")
                continue
            seen_in[lid] = full
            out[lid] = {"verdict": j["verdict"], "note": j["note"].strip()}
    if errs:
        raise JudgementError("judgements have problems:\n  - " + "\n  - ".join(errs))
    return out
