"""Command line: python -m stayfinder run CONFIG [--refresh] | python -m stayfinder build CONFIG"""
import argparse
import sys

from . import pipeline
from .airbnb import AirbnbFormatError
from .config import ConfigError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m stayfinder",
                                 description="Find Airbnb stays and build a local HTML page to choose from.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="search Airbnb, open each listing under budget, build the page")
    r.add_argument("config", help="path to the JSON config")
    r.add_argument("--refresh", action="store_true",
                   help="fetch every page again instead of reusing the cache (new prices and availability)")
    b = sub.add_parser("build", help="rebuild the page from the cache only (after editing rules or filters)")
    b.add_argument("config", help="path to the JSON config")
    a = ap.parse_args(argv)
    if sys.version_info < (3, 10):
        sys.exit("stay-finder needs Python 3.10 or newer")
    try:
        out = pipeline.run(a.config, refresh=a.refresh) if a.cmd == "run" else pipeline.build(a.config)
    except (ConfigError, AirbnbFormatError, FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
