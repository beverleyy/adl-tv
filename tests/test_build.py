"""Tests for the static-demo build. Run:  python3 -m unittest discover tests"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import build_demo  # noqa: E402


class StaticDemoBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp()
        cls.files = build_demo.build(os.path.join(cls.out, "site"), version="12345")
        with open(os.path.join(cls.out, "site", "index.html"), encoding="utf-8") as f:
            cls.page = f.read()

    def test_has_everything_the_page_needs(self):
        for needed in ("index.html", ".nojekyll", "static/js/dashboard.js", "static/js/demo.js", "static/css/dashboard.css",
                       "static/leaflet/leaflet.js", "static/leaflet/leaflet.css", "static/fonts/source-sans-3-latin-400-normal.woff2"):
            self.assertIn(needed, self.files)

    def test_demo_backend_loads_before_the_dashboard(self):
        self.assertLess(self.page.index("static/js/demo.js"), self.page.index("static/js/dashboard.js"))

    def test_version_stamp_filled_in(self):
        self.assertNotIn("__VERSION__", self.page)
        self.assertIn("dashboard.css?v=12345", self.page)

    def test_nothing_points_at_the_site_root(self):
        """Project pages are served from /repo/, so /static/... would 404."""
        self.assertEqual(re.findall(r"""(?:href|src)=["']/(?!/)""", self.page), [])
        with open(os.path.join(self.out, "site", "static", "css", "dashboard.css"), encoding="utf-8") as f:
            self.assertEqual(re.findall(r"url\(\s*/[^/)]", f.read()), [])

    def test_the_real_servers_page_is_not_touched(self):
        with open(os.path.join(HERE, "..", "index.html"), encoding="utf-8") as f:
            real = f.read()
        self.assertNotIn("demo.js", real)
        self.assertIn("__VERSION__", real)


if __name__ == "__main__":
    unittest.main()
