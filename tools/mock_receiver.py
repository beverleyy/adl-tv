#!/usr/bin/env python3
"""
A pretend ADS-B receiver, so you can see the dashboard without one.

    python3 tools/mock_receiver.py                # serves http://localhost:9099/data/aircraft.json
    python3 server.py --receiver http://localhost:9099 --no-fr24

It also serves data/receiver.json with a made-up location, like a real receiver, so the dashboard finds its position
by itself. Use --no-location to see what happens when a receiver doesn't publish one.

It flies a handful of made-up aircraft on SFO-style arrivals and departures, plus some passing and local
traffic, in the same JSON format tar1090 / dump1090 serve. Standard library only, Python 3.5+.
"""
import argparse
import json
import math
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

T0 = time.time()
# callsign, ICAO hex, start lat, start lon, track, ground speed (kt), altitude (ft), climb (ft/min), category, type, loop (s)
TRAFFIC = [
    ("UAL123", "a0f001", 37.15, -121.80, 318, 230, 6000, -900, "A3", "B738", 600),   # arrival from the south
    ("DAL88",  "a0f002", 37.42, -122.55,  90, 300, 9000, -600, "A5", "A359", 300),   # arrival over the hills
    ("SWA415", "a0f003", 37.60, -122.37, 150, 250, 1500, 2200, "A3", "B38M", 300),   # SFO departure
    ("AAL200", "a0f004", 37.62, -122.10, 215, 470, 35000,   0, "A5", "B77W", 300),   # passing overhead, high
    ("ASA9",   "a0f005", 37.00, -121.95, 330, 210, 4800, -700, "A3", "E75L", 500),
    ("JBU77",  "a0f006", 37.80, -122.65, 130, 300, 12000, -500, "A3", "A21N", 500),
    ("UAL9",   "a0f007", 37.30, -122.60,  60, 330, 9000, -1200, "A4", "B752", 400),
    ("N123AB", "a0f008", 37.40, -122.10,  45,  90, 1500,    0, "A1", "C172", 300),   # light aircraft
    ("EJA773", "a0f009", 37.50, -122.10, 200, 230, 3400, -600, "A2", "CL35", 600),   # business jet
]


def move(lat, lon, track, gs, seconds):
    d = gs / 3600.0 * seconds                    # nautical miles
    return (lat + math.cos(math.radians(track)) * d / 60.0,
            lon + math.sin(math.radians(track)) * d / (60.0 * math.cos(math.radians(lat))))


def aircraft():
    t, out = time.time() - T0, []
    for (fl, hx, la, lo, trk, gs, alt, rate, cat, typ, loop) in TRAFFIC:
        s = t % loop
        lat, lon = move(la, lo, trk, gs, s)
        a = int(alt + rate * s / 60.0)
        out.append(dict(hex=hx, flight=fl.ljust(8), lat=lat, lon=lon, track=trk, gs=gs, alt_baro=a, alt_geom=a + 150,
                        geom_rate=rate, category=cat, t=typ, seen_pos=0.5, seen=0.2))
    return out


LOCATION = {"lat": 37.4301, "lon": -122.1702}      # made up: somewhere on the Stanford campus


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/data/receiver.json"):
            info = {"version": "mock", "refresh": 1000, "history": 0}
            info.update(LOCATION)
            body = json.dumps(info).encode()
        elif self.path.startswith("/data/aircraft.json"):
            body = json.dumps({"now": time.time(), "messages": int(time.time() * 300), "aircraft": aircraft()}).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=9099)
    ap.add_argument("--lat", type=float, help="location to publish in receiver.json")
    ap.add_argument("--lon", type=float)
    ap.add_argument("--no-location", action="store_true", help="publish no location, like a receiver that hides it")
    a = ap.parse_args()
    port = a.port
    if a.no_location:
        LOCATION.clear()
    elif a.lat is not None and a.lon is not None:
        LOCATION.update(lat=a.lat, lon=a.lon)
    print("Mock receiver at http://localhost:{}/data/aircraft.json".format(port))
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
