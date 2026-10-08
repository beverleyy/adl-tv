"""Unit tests for the server's decision logic. Run:  python3 -m unittest discover tests   (Python 3.5+)"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server  # noqa: E402

A = lambda code, lat, lon: {"iata": code, "lat": lat, "lon": lon}
SFO, LAX, DEN = A("SFO", 37.619, -122.375), A("LAX", 33.9425, -118.408), A("DEN", 39.856, -104.674)


class Callsigns(unittest.TestCase):
    def test_airline_callsigns_are_normalised(self):
        self.assertEqual(server.StandingData.normalise("UAL0123"), ("UAL", "UAL123"))
        self.assertEqual(server.StandingData.normalise("DAL1"), ("DAL", "DAL1"))

    def test_registrations_have_no_route(self):
        self.assertEqual(server.StandingData.normalise("N123AB"), (None, None))


class RouteFitsThePlane(unittest.TestCase):
    """A stored route is only accepted if the plane's position, heading and climb/descent fit it."""

    def fits(self, aps, lat, lon, trk, vs):
        return server.pick_leg(aps, lat, lon, trk, strict=True, vs=vs) is not None

    def test_departure_turning_out_over_the_ocean_fits(self):
        self.assertTrue(self.fits([SFO, LAX], 37.95, -122.75, 320, 2200))

    def test_descending_near_the_origin_is_an_arrival_not_this_route(self):
        self.assertFalse(self.fits([SFO, LAX], 37.95, -122.75, 320, -1500))

    def test_cruise_in_the_right_direction_fits(self):
        self.assertTrue(self.fits([SFO, DEN], 39.5, -117.0, 85, 0))

    def test_cruise_in_the_reverse_direction_does_not(self):
        self.assertFalse(self.fits([SFO, DEN], 39.5, -117.0, 265, 0))

    def test_far_from_the_route_does_not(self):
        self.assertFalse(self.fits([SFO, DEN], 28.0, -82.0, 90, 0))


class DeadServicesAreQuiet(unittest.TestCase):
    def test_backoff_grows_and_logs_stay_short(self):
        t = server.Throttle("test", 0)
        out, pauses = io.StringIO(), []

        def dead():
            raise ValueError("empty reply")
        with contextlib.redirect_stdout(out):
            for _ in range(8):
                try:
                    t._run(dead)
                except server.Transient:
                    pass
                pauses.append(round(max(0, t.blocked_until - time.time()) / 60))
                t.blocked_until = 0
            t._run(lambda: "ok")
        self.assertEqual(pauses, [0, 0, 5, 10, 20, 40, 80, 160])
        self.assertEqual(len(out.getvalue().strip().splitlines()), 3)      # problem, backing off, working again


class Settings(unittest.TestCase):
    def test_command_line_beats_config_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"lat": 1.5, "lon": 2.5, "lab_name": "Roof", "port": 9000}, f)
        try:
            a = server.parse_args(["--config", f.name, "--port", "9001"])
            self.assertEqual((a.lat, a.lon, a.lab_name, a.port), (1.5, 2.5, "Roof", 9001))
        finally:
            os.remove(f.name)

    def test_true_false_settings_work_from_the_config_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"lat": 1, "lon": 2, "no_fr24": True, "allow_ga": True, "_comment": "ignored"}, f)
        try:
            a = server.parse_args(["--config", f.name])
            self.assertTrue(a.no_fr24 and a.allow_ga)
            self.assertFalse(server.parse_args(["--config", f.name, "--lat", "3", "--lon", "4"]).lat != 3)
        finally:
            os.remove(f.name)

    def test_location_defaults_to_the_stanford_campus(self):
        a = server.parse_args(["--config", "/nonexistent.json"])
        self.assertEqual((a.lat, a.lon, a.default_location), (server.DEFAULT_LAT, server.DEFAULT_LON, True))
        self.assertEqual((a.lab_name, a.subtitle), ("ADL", "Live from the ADL rooftop"))

    def test_an_explicit_location_is_used_as_is(self):
        a = server.parse_args(["--config", "/nonexistent.json", "--lat", "51.5", "--lon", "-0.1"])
        self.assertEqual((a.lat, a.lon, a.default_location), (51.5, -0.1, False))

    def test_half_a_location_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                server.parse_args(["--config", "/nonexistent.json", "--lat", "51.5"])



class ReceiverLocation(unittest.TestCase):
    """The location a receiver publishes about itself in data/receiver.json."""

    def test_parsing(self):
        self.assertEqual(server.parse_receiver_location({"lat": 37.4301, "lon": -122.1702}), (37.4301, -122.1702))
        self.assertEqual(server.parse_receiver_location({"lat": "51.5", "lon": "-0.12"}), (51.5, -0.12))
        for bad in ({}, {"lat": 37.4}, {"lat": None, "lon": None}, {"lat": 0, "lon": 0}, {"lat": 95, "lon": 10},
                    {"lat": 10, "lon": 200}, {"lat": "n/a", "lon": "n/a"}):
            self.assertIsNone(server.parse_receiver_location(bad), bad)

    def test_rounded_locations_are_recognised(self):
        self.assertTrue(server.looks_rounded(37.43, -122.17))        # an "approximate" location
        self.assertTrue(server.looks_rounded(37.4, -122.2))
        self.assertFalse(server.looks_rounded(37.4301, -122.1702))   # exact enough
        self.assertFalse(server.looks_rounded(37.43, -122.1702))

    def probe(self, routes):
        """Run probe_receiver_location against a throwaway local web server serving `routes` {path: (status, body)}."""
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                status, body = routes.get(self.path, (404, b""))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            return server.probe_receiver_location("http://127.0.0.1:%d" % httpd.server_address[1])
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_found(self):
        body = json.dumps({"version": "x", "lat": 37.4301, "lon": -122.1702}).encode()
        self.assertEqual(self.probe({"/data/receiver.json": (200, body)}), ("found", (37.4301, -122.1702)))

    def test_receiver_that_publishes_no_location(self):
        self.assertEqual(self.probe({"/data/receiver.json": (200, b'{"version": "x", "refresh": 1000}')}), ("none", None))

    def test_no_receiver_json_at_all(self):
        self.assertEqual(self.probe({}), ("none", None))                                         # 404
        self.assertEqual(self.probe({"/data/receiver.json": (200, b"<html>hi</html>")}), ("none", None))   # a web page

    def test_receiver_that_is_down_is_retried_not_given_up_on(self):
        self.assertEqual(self.probe({"/data/receiver.json": (503, b"")}), ("unreachable", None))
        self.assertEqual(server.probe_receiver_location("http://127.0.0.1:9"), ("unreachable", None))     # nothing listening



class WhoTheFlightIsSoldAs(unittest.TestCase):
    """Regionals: the callsign names the operator (SKW), the flight number names the airline on the ticket (UA)."""

    @classmethod
    def setUpClass(cls):
        table = {"UA": [("UAL", "United Airlines")], "DL": [("DAL", "Delta Air Lines")], "AA": [("AAL", "American Airlines")],
                 "AS": [("ASA", "Alaska Airlines")], "OO": [("SKW", "SkyWest Airlines")], "QX": [("QXE", "Horizon Air")],
                 "YX": [("MEP", "Midwest Airlines"), ("RPA", "Republic Airways")], "MQ": [("EGF", "American Eagle Airlines"), ("ENY", "Envoy Air")],
                 "G7": [("GJS", "GoJet Airlines"), ("GNF", "Gandalf Airlines")], "QK": [("JZA", "Air Canada Jazz")], "AC": [("ACA", "Air Canada")],
                 "XX": [("AAA", "One Air"), ("BBB", "Two Air")]}
        names = {"UAL": "United Airlines", "SKW": "SkyWest Airlines", "QXE": "Horizon Air", "RPA": "Republic Airways", "ENY": "Envoy Air",
                 "JZA": "Air Canada Jazz", "DAL": "Delta Air Lines", "GJS": "GoJet Airlines"}
        now = time.time()
        server.STANDING_DATA.mem["airlines-iata"] = (now, table)
        server.STANDING_DATA.mem["airlines"] = (now, names)

    @classmethod
    def tearDownClass(cls):
        server.STANDING_DATA.mem.pop("airlines-iata", None)
        server.STANDING_DATA.mem.pop("airlines", None)

    def test_skywest_flying_for_united(self):
        self.assertEqual(server.marketing_info("SKW5460", "UA5460"), ("United Express", "UA 5460", "SkyWest Airlines"))

    def test_skywest_flying_for_delta(self):
        self.assertEqual(server.marketing_info("SKW3456", "DL3456"), ("Delta Connection", "DL 3456", "SkyWest Airlines"))

    def test_horizon_flying_as_alaska(self):
        self.assertEqual(server.marketing_info("QXE2218", "AS2218"), ("Alaska Airlines", "AS 2218", "Horizon Air"))

    def test_republic_and_envoy_with_shared_two_letter_codes(self):
        self.assertEqual(server.marketing_info("RPA4400", "DL4400"), ("Delta Connection", "DL 4400", "Republic Airways"))
        self.assertEqual(server.marketing_info("ENY3500", "AA3500"), ("American Eagle", "AA 3500", "Envoy Air"))

    def test_canadian_regional(self):
        self.assertEqual(server.marketing_info("JZA1234", "AC1234"), ("Air Canada Express", "AC 1234", "Air Canada Jazz"))

    def test_mainline_says_nothing_extra(self):
        self.assertEqual(server.marketing_info("UAL755", "UA755"), ("United Airlines", "", ""))

    def test_mainline_with_a_different_flight_number_shows_it(self):
        self.assertEqual(server.marketing_info("UAL755", "UA1234"), ("United Airlines", "UA 1234", ""))

    def test_a_regionals_own_code_is_not_mistaken_for_a_partner(self):
        self.assertEqual(server.marketing_info("SKW5460", "OO5460"), ("SkyWest Airlines", "", ""))
        self.assertEqual(server.marketing_info("RPA9", "YX9")[0], "Republic Airways")      # shared code, operator picks the right one

    def test_a_shared_code_is_resolved_only_when_there_is_a_clear_winner(self):
        # GoJet and "Gandalf Airlines" both use G7: GoJet is a known active carrier, so it wins...
        self.assertEqual(server.marketing_info("SKW100", "G7100"), ("GoJet Airlines", "G7 100", "SkyWest Airlines"))
        # ...but two unknown airlines sharing a code, neither of them the operator, is never guessed at
        self.assertEqual(server.marketing_info("ZZZ100", "XX100"), ("", "", ""))

    def test_nothing_to_go_on(self):
        for cs, num in (("SKW5460", ""), ("SKW5460", None), ("N123AB", "UA5460"), ("SKW5460", "ZZ5460"), ("SKW5460", "garbage")):
            self.assertEqual(server.marketing_info(cs, num), ("", "", ""), (cs, num))


if __name__ == "__main__":
    unittest.main()
