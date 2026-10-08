#!/usr/bin/env python3
"""
Build the static demo (for GitHub Pages) into ./site

    python3 tools/build_demo.py                 # writes ./site
    python3 -m http.server -d site 8000         # then open http://localhost:8000

GitHub Pages can only serve files, so there is no server.py behind the demo. Instead the page loads
static/js/demo.js, which simulates the receiver and answers the dashboard's /api/... calls in the browser.
The demo is made from the real index.html and static/ files, so it can't drift from the real dashboard.
Standard library only, Python 3.5+.
"""
import argparse
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DASHBOARD_TAG = '<script src="static/js/dashboard.js?v=__VERSION__"></script>'


def build(out, version=None):
    version = version or str(int(time.time()))
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(out, "static"))

    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
        page = f.read()
    if page.count(DASHBOARD_TAG) != 1:
        sys.exit("build_demo: couldn't find the dashboard <script> tag in index.html (did it change?)")
    # the demo backend must be loaded BEFORE the dashboard, which fetches /api/config as soon as it starts
    page = page.replace(DASHBOARD_TAG, '<script src="static/js/demo.js?v=__VERSION__"></script>\n' + DASHBOARD_TAG)
    page = page.replace("__VERSION__", version)
    page = page.replace("<title>Overhead</title>", "<title>Overhead (demo)</title>")

    # Project pages live at https://user.github.io/repo/, not at /, so nothing may point at the site root.
    bad = re.findall(r"""(?:href|src)=["']/(?!/)[^"']*""", page)
    if bad:
        sys.exit("build_demo: root-absolute URLs in index.html would break on GitHub Pages: {}".format(bad))

    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    open(os.path.join(out, ".nojekyll"), "w").close()                  # serve files exactly as they are
    files = sorted(os.path.relpath(os.path.join(d, n), out) for d, _, fs in os.walk(out) for n in fs)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "site"), help="output folder (default: ./site)")
    args = ap.parse_args()
    files = build(args.out)
    print("Built the demo: {} files in {}".format(len(files), args.out))


if __name__ == "__main__":
    main()
