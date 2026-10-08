#!/usr/bin/env python3
"""
Overhead - a live wall display for a tar1090 / dump1090 ADS-B receiver.

Serves the dashboard and acts as a small caching proxy for:
  - the receiver's aircraft.json (so the browser never hits CORS problems)
  - aircraft photos / registrations / routes (Planespotters, adsbdb)
  - map tiles (CARTO dark basemap, cached to ./cache so a network blip
    doesn't blank the map)

Standard library only -- nothing to pip install. Runs on Python 3.5 or newer (Ubuntu 16.04 and up).

    cp config.example.json config.json      # then set your receiver URL and location
    python3 server.py

Its location comes from your settings if you give one, otherwise from the receiver itself (data/receiver.json,
which dump1090 / readsb / tar1090 publish), otherwise the middle of the Stanford campus - so it runs out of the box.

Open http://localhost:8081 in a browser (full screen / kiosk on a TV).
Settings come from the command line, then config.json, then environment variables, then defaults.
"""
import argparse
import csv
import io
import json
import math
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """http.server.ThreadingHTTPServer only exists from Python 3.7; this is the same thing, so the
    server also runs on the Python 3.5 that Ubuntu 16.04 ships."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A browser that hangs up early (reloads, cancelled map tiles) is normal for a kiosk: don't print a traceback.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        HTTPServer.handle_error(self, request, client_address)

HERE = os.path.dirname(os.path.realpath(__file__))
STATIC = os.path.realpath(os.path.join(HERE, "static"))
CACHE = os.path.join(HERE, "cache", "tiles-osm")   # re-pointed per basemap in main()
UA = "Mozilla/5.0 (compatible; OverheadSkyDashboard/2.1; internal lab display)"
STANDING_BASE = os.environ.get("STANDING_BASE", "https://raw.githubusercontent.com/vradarserver/standing-data/main")
ADSBDB_BASE = os.environ.get("ADSBDB_BASE", "https://api.adsbdb.com")

mimetypes.add_type("font/woff2", ".woff2")

# ---------- first-run asset check ----------
# The dashboard bundles Leaflet and the Source Sans 3 fonts in ./static. If that folder
# didn't get copied along with server.py, fetch what's missing once.
CDN = "https://cdn.jsdelivr.net/npm"
ASSETS = {
    "leaflet/leaflet.js": "{}/leaflet@1.9.4/dist/leaflet.js".format(CDN),
    "leaflet/leaflet.css": "{}/leaflet@1.9.4/dist/leaflet.css".format(CDN),
    "leaflet/images/layers.png": "{}/leaflet@1.9.4/dist/images/layers.png".format(CDN),
    "leaflet/images/layers-2x.png": "{}/leaflet@1.9.4/dist/images/layers-2x.png".format(CDN),
    "leaflet/images/marker-icon.png": "{}/leaflet@1.9.4/dist/images/marker-icon.png".format(CDN),
}
for _w in (400, 500, 600, 700):
    ASSETS["fonts/source-sans-3-latin-{}-normal.woff2".format(_w)] = (
        "{}/@fontsource/source-sans-3@5.3.0/files/source-sans-3-latin-{}-normal.woff2".format(CDN, _w))


def ensure_assets():
    for rel, url in ASSETS.items():
        dest = os.path.join(STATIC, *rel.split("/"))
        if os.path.isfile(dest):
            continue
        try:
            print("Downloading missing asset {} ...".format(rel))
            data = fetch_bytes(url, timeout=20)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
        except Exception as e:
            print("  could not download {}: {}".format(rel, e))
            print("  (copy the 'static' folder that came with server.py next to it instead)")


# ---------- tiny TTL cache ----------
_cache = {}
_lock = threading.Lock()


def cache_get(key):
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return True, hit[1]
    return False, None


def cache_put(key, value, ttl):
    with _lock:
        _cache[key] = (time.time() + ttl, value)


def fetch_bytes(url, timeout=8, payload=None):
    headers = {"User-Agent": UA}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url, timeout=6, payload=None):
    return json.loads(fetch_bytes(url, timeout, payload).decode("utf-8", "replace"))


# ---------- small geometry helpers ----------
R_NM = 3440.065


def _dist(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * R_NM * math.asin(math.sqrt(h))


def _brg(a, b, c, d):
    dn = math.radians(d - b)
    y = math.sin(dn) * math.cos(math.radians(c))
    x = math.cos(math.radians(a)) * math.sin(math.radians(c)) - math.sin(math.radians(a)) * math.cos(math.radians(c)) * math.cos(dn)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _angdiff(a, b):
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


# ---------- polite lookups ----------
class NotFound(Exception):
    """The service answered, and has no record (HTTP 404). Normal, not an error."""


class Transient(Exception):
    """Throttled / blocked / network trouble. Worth retrying later."""


TRANSIENT = {"_transient": True}


class Throttle:
    """One outbound request at a time per service, spaced out, with backoff.

    These are free community services. A burst of parallel lookups can get a client
    throttled (HTTP 403/429), so we serialize requests, honor Retry-After, and stop
    calling a service for a while after it has been refused or has failed repeatedly."""

    def __init__(self, name, min_gap, hint=""):
        self.name, self.min_gap, self.hint = name, min_gap, hint
        self.lock = threading.Lock()
        self.next_ok = 0.0
        self.blocked_until = 0.0
        self.failures = 0
        self.last_log = 0.0
        self.shown = {}                            # message -> when it was last printed

    def _log(self, msg):
        """One line per problem: a message already shown isn't repeated for 6 hours."""
        now = time.time()
        if now - self.shown.get(msg, -1e9) < 6 * 3600:
            return
        self.shown[msg], self.last_log = now, now
        print("[{}] {}{}".format(self.name, msg, self.hint), flush=True)

    def _run(self, fn):
        with self.lock:
            now = time.time()
            if now < self.blocked_until:
                raise Transient("backing off")
            if self.next_ok > now:
                time.sleep(self.next_ok - now)
            try:
                out = fn()
                if self.failures >= 3:
                    print("[{}] working again".format(self.name), flush=True)
                self.failures, self.shown = 0, {}
                return out
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self.failures = 0
                    raise NotFound()
                wait = None
                if e.code in (403, 429, 502, 503):
                    try:
                        wait = min(int(e.headers.get("Retry-After", "")), 600)
                    except ValueError:
                        wait = 60
                    self._log("HTTP {} - pausing for {}s, will retry afterwards".format(e.code, wait))
                else:
                    self._log("HTTP {}".format(e.code))
                self._fail(wait)
                raise Transient(str(e))
            except ValueError:                     # empty / non-JSON reply
                self._log("empty or invalid reply")
                self._fail()
                raise Transient("bad reply")
            except Exception as e:                 # timeouts, DNS, offline...
                self._log("{}: {}".format(type(e).__name__, e))
                self._fail()
                raise Transient(str(e))
            finally:
                self.next_ok = time.time() + self.min_gap

    def _fail(self, wait=None):
        """Keeps failing: leave it alone for longer and longer (5 min, 10, 20 ... up to 6 hours), so a dead
        service costs almost nothing and doesn't fill the terminal. One line when it starts, one if it recovers."""
        self.failures += 1
        pause = wait or 0
        if self.failures >= 3:
            pause = max(pause, min(6 * 3600, 300 * 2 ** (self.failures - 3)))
            if self.failures == 3:
                self._log("not responding - trying again less and less often (up to every 6 h); will say when it's back")
        if pause:
            self.blocked_until = time.time() + pause

    def get_json(self, url, timeout=6, payload=None):
        return self._run(lambda: fetch_json(url, timeout, payload))

    def get_text(self, url, timeout=20):
        return self._run(lambda: fetch_bytes(url, timeout).decode("utf-8-sig", "replace"))


ADSBDB = Throttle("adsbdb", 0.3)
STANDING = Throttle("standing-data", 0.25)
AIRPORT_LIST = Throttle("airport-list", 0.25)     # its own limiter: trouble with that host must not pause the route files
ROUTE_APIS = []        # [(url, Throttle)] live route services tried before the local data; set in main()
NO_ANSWER = object()   # "no live service gave an answer"

# Common carriers, so the card can say "United Airlines" rather than the registered owner
# (which is often a leasing company). Anything not listed falls back to the owner.
AIRLINES = {
    "UAL": "United Airlines", "DAL": "Delta Air Lines", "AAL": "American Airlines", "SWA": "Southwest Airlines",
    "ASA": "Alaska Airlines", "JBU": "JetBlue", "NKS": "Spirit Airlines", "FFT": "Frontier Airlines",
    "HAL": "Hawaiian Airlines", "AAY": "Allegiant Air", "VXP": "Avelo Airlines", "SCX": "Sun Country Airlines",
    "SKW": "SkyWest Airlines", "RPA": "Republic Airways", "ENY": "Envoy Air", "EDV": "Endeavor Air",
    "QXE": "Horizon Air", "GJS": "GoJet Airlines", "CPZ": "Compass Airlines", "ASH": "Mesa Airlines",
    "FDX": "FedEx Express", "UPS": "UPS Airlines", "GTI": "Atlas Air", "ABX": "ABX Air", "ATN": "Air Transport International",
    "ACA": "Air Canada", "WJA": "WestJet", "TSC": "Air Transat", "AMX": "Aeromexico", "VOI": "Volaris", "VIV": "Viva Aerobus",
    "BAW": "British Airways", "VIR": "Virgin Atlantic", "DLH": "Lufthansa", "AFR": "Air France", "KLM": "KLM",
    "SWR": "Swiss", "SAS": "SAS", "FIN": "Finnair", "ICE": "Icelandair", "IBE": "Iberia", "TAP": "TAP Air Portugal",
    "EIN": "Aer Lingus", "THY": "Turkish Airlines", "ELY": "El Al", "UAE": "Emirates", "QTR": "Qatar Airways", "ETD": "Etihad",
    "ANA": "All Nippon Airways", "JAL": "Japan Airlines", "KAL": "Korean Air", "AAR": "Asiana Airlines", "EVA": "EVA Air",
    "CAL": "China Airlines", "CPA": "Cathay Pacific", "SIA": "Singapore Airlines", "CCA": "Air China", "CES": "China Eastern",
    "CSN": "China Southern", "HVN": "Vietnam Airlines", "PAL": "Philippine Airlines", "AIC": "Air India",
    "QFA": "Qantas", "ANZ": "Air New Zealand", "LAN": "LATAM", "AVA": "Avianca",
}


def lookup_aircraft(hexcode):
    """adsbdb.com: registration, type, operator (and a fallback photo)."""
    try:
        d = ADSBDB.get_json("{}/v0/aircraft/{}".format(ADSBDB_BASE, hexcode))
    except NotFound:
        return None
    a = d.get("response") or {}
    if isinstance(a, dict) and isinstance(a.get("aircraft"), dict):
        a = a["aircraft"]
        return {"registration": a.get("registration"), "type": a.get("type"),
                "icao_type": a.get("icao_type"), "manufacturer": a.get("manufacturer"),
                "owner": a.get("registered_owner"), "fallback_photo": a.get("url_photo")}
    return None


def _airport(x):
    return {"iata": x.get("iata"), "icao": x.get("icao"), "city": x.get("city"),
            "name": x.get("name"), "lat": x.get("lat"), "lon": x.get("lon")}


def pick_leg(airports, lat, lon, trk, strict, vs=None):
    """A callsign can map to several routes (reused numbers, multi-stop flights).
    Find the leg A->B that the plane's current position and heading actually fit.

    Mid-route a plane must be close to the A-B path and pointed along it. Near an airport (terminal area,
    within 45 nm) planes legitimately wander - departures turn out over the ocean, arrivals fly long
    downwinds - so the path tolerance is much wider there, and instead we use climb/descent as the
    direction check: a plane near A that is descending is arriving at A, not departing it."""
    best = None
    for i in range(len(airports) - 1):
        A, B = airports[i], airports[i + 1]
        if None in (A.get("lat"), A.get("lon"), B.get("lat"), B.get("lon")):
            continue
        L = _dist(A["lat"], A["lon"], B["lat"], B["lon"])
        dA = _dist(A["lat"], A["lon"], lat, lon)
        dB = _dist(lat, lon, B["lat"], B["lon"])
        detour = dA + dB - L                      # ~0 when the plane is on the A-B path
        if strict:
            near_end = min(dA, dB) <= 45
            if detour > (max(90.0, 0.12 * L) if near_end else max(25.0, 0.12 * L)):
                continue
            if vs is not None:
                if dA <= 45 and vs < -500:        # near the origin and descending: it is landing there
                    continue
                if dB <= 45 and vs > 500:         # near the destination and climbing: it just left there
                    continue
            # far from both ends the plane must be pointed roughly at B (rules out the reverse direction)
            if trk is not None and dA > 40 and dB > 40 and _angdiff(trk, _brg(lat, lon, B["lat"], B["lon"])) > 70:
                continue
        if best is None or detour < best[0]:
            best = (detour, i)
    return None if best is None else best[1]


def _route(a, b, airline, source):
    return {"airline": airline, "source": source,
            "origin": {k: a.get(k) for k in ("iata", "icao", "city", "name")},
            "destination": {k: b.get(k) for k in ("iata", "icao", "city", "name")}}


class StandingData:
    """Virtual Radar Server "standing data" (CC0): routes, airports and airlines as plain CSV files.

    This is the dataset behind adsb.lol, adsbdb and friends, and it holds ONE route per callsign.
    We read it directly: files are downloaded on demand from GitHub, kept on disk, and refreshed
    daily, so there is no per-lookup web API to be rate-limited, blocked, or switched off."""

    ROUTE_TTL, AIRPORT_TTL, AIRLINE_TTL, NEG_TTL = 24 * 3600, 30 * 24 * 3600, 7 * 24 * 3600, 7 * 24 * 3600

    def __init__(self):
        self.dir = os.path.join(HERE, "cache", "standing")
        self.mem = {}                     # key -> (checked_at, table)
        self.lock = threading.Lock()

    # -- files on disk ------------------------------------------------------------
    def _file(self, rel, ttl):
        """Text of a standing-data file. None if it doesn't exist upstream. Raises Transient if
        we have no copy and can't download one; a stale copy is used if the download fails."""
        path = os.path.join(self.dir, *rel.split("/"))
        none = path + ".none"
        age = lambda p: time.time() - os.path.getmtime(p)
        if os.path.isfile(path) and age(path) < ttl:
            return open(path, encoding="utf-8-sig").read()
        if os.path.isfile(none) and age(none) < self.NEG_TTL:
            return None
        try:
            text = STANDING.get_text("{}/{}".format(STANDING_BASE, rel))
        except NotFound:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(none, "w").close()
            return None
        except Transient:
            if os.path.isfile(path):
                return open(path, encoding="utf-8-sig").read()
            raise
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
        return text

    def _table(self, key, rel, ttl, parse):
        with self.lock:
            ent = self.mem.get(key)
        if ent and time.time() - ent[0] < 1800:
            return ent[1]
        try:
            text = self._file(rel, ttl)
        except Transient:
            if ent:
                return ent[1]
            raise
        table = parse(text) if text is not None else None
        with self.lock:
            self.mem[key] = (time.time(), table)
        return table

    # -- parsers ------------------------------------------------------------------
    @staticmethod
    def _rows(text):
        return csv.DictReader(io.StringIO(text))

    def _parse_routes(self, text):
        return {r["Callsign"].strip().upper(): [c for c in r["AirportCodes"].strip().split("-") if c]
                for r in self._rows(text) if r.get("Callsign") and r.get("AirportCodes")}

    def _parse_airports(self, text):
        out = {}
        for r in self._rows(text):
            try:
                ap = {"iata": r.get("IATA") or None, "icao": r.get("ICAO") or None, "city": r.get("Location") or None,
                      "name": r.get("Name") or None, "lat": float(r["Latitude"]), "lon": float(r["Longitude"])}
            except (KeyError, ValueError, TypeError):
                continue
            for k in (r.get("Code"), r.get("ICAO"), r.get("IATA")):
                if k:
                    out.setdefault(k.strip().upper(), ap)
        return out

    def _parse_airlines(self, text):
        return {r["Code"].strip().upper(): r["Name"].strip() for r in self._rows(text) if r.get("Code") and r.get("Name")}

    # -- public lookups -----------------------------------------------------------
    @staticmethod
    def normalise(callsign):
        """Callsign -> (airline ICAO code, normalised callsign), per the standing-data schema."""
        m = re.match(r"^([A-Z]{2,3}|[A-Z][0-9]|[0-9][A-Z])(\d[A-Z0-9]*)", callsign.upper())
        if not m or len(m.group(1)) != 3 or not m.group(1).isalpha():
            return None, None                      # registrations (N123AB) and IATA-style callsigns
        num = m.group(2).lstrip("0")
        if not num or num.isalpha():
            num = "0" + num
        return m.group(1), m.group(1) + num

    def route_codes(self, callsign):
        """Airport codes in flying order for this callsign, e.g. ['KJFK','EGLL']. None if unknown."""
        code, cs = self.normalise(callsign)
        if not code:
            return None, None
        base = "routes/schema-01/{}/{}".format(code[0], code)
        tbl = self._table("r:" + code, "{}-all.csv".format(base), self.ROUTE_TTL, self._parse_routes)
        if tbl is None:                            # airlines with >10,000 routes are split by first digit
            digit = cs[3]
            tbl = self._table("r:{}-{}".format(code, digit), "{}-{}.csv".format(base, digit), self.ROUTE_TTL, self._parse_routes)
        return code, (tbl or {}).get(cs)

    def airport(self, code):
        code = code.upper()
        tbl = self._table("a:" + code[:2], "airports/schema-01/{}/{}.csv".format(code[0], code[:2]), self.AIRPORT_TTL, self._parse_airports)
        return (tbl or {}).get(code)

    def _parse_airlines_iata(self, text):
        out = {}
        for r in self._rows(text):
            iata, code, name = (r.get("IATA") or "").strip().upper(), (r.get("Code") or "").strip().upper(), (r.get("Name") or "").strip()
            if len(iata) == 2 and code and name:
                out.setdefault(iata, []).append((code, name))
        return out

    def airlines_for_iata(self, iata):
        """[(ICAO code, name), ...] of the airlines using this two-letter IATA code (several can share one)."""
        try:
            tbl = self._table("airlines-iata", "airlines/schema-01/airlines.csv", self.AIRLINE_TTL, self._parse_airlines_iata)
        except Transient:
            return []
        return (tbl or {}).get(iata.upper(), [])

    def airline_name(self, code):
        try:
            tbl = self._table("airlines", "airlines/schema-01/airlines.csv", self.AIRLINE_TTL, self._parse_airlines)
        except Transient:
            tbl = None
        return (tbl or {}).get(code) or AIRLINES.get(code)

    def warm(self):
        """Background: fetch the airline list and the biggest US carriers so first lookups are instant."""
        try:
            self.airline_name("UAL")
            AIRPORTS.get("SFO")
            for c in ("UAL", "DAL", "AAL", "SWA", "ASA", "SKW", "JBU", "FDX", "UPS", "HAL"):
                self._table("r:" + c, "routes/schema-01/{}/{}-all.csv".format(c[0], c), self.ROUTE_TTL, self._parse_routes)
        except Exception as e:
            print("[standing-data] warm-up skipped: {}".format(e))


STANDING_DATA = StandingData()

# How a regional's passengers know it. Only used when the flight is flown for another airline.
MARKETING_BRANDS = {"UA": "United Express", "DL": "Delta Connection", "AA": "American Eagle", "AC": "Air Canada Express"}


def marketing_info(callsign, number):
    """Who a flight is sold as, when that isn't the operator named in the callsign.

    The callsign says who FLIES it (SKW = SkyWest); FR24's flight number says who SELLS it: SKW5460 is SkyWest
    operating United's UA5460. Returns (airline, flight number, operator), with empty strings where there is
    nothing worth adding. Mainline flights return just their airline, and a flight number only if it differs
    from the callsign. An ambiguous two-letter code is never guessed at."""
    callsign = (callsign or "").strip().upper()
    m = re.match(r"^([A-Z0-9]{2})\s?(\d{1,4}[A-Z]?)$", (number or "").strip().upper())
    op_code = callsign[:3] if callsign[:3].isalpha() else ""
    if not m or not op_code:
        return "", "", ""
    iata, digits = m.group(1), m.group(2)
    cands = STANDING_DATA.airlines_for_iata(iata)
    mine = [c for c in cands if c[0] == op_code]
    if mine:                                                   # the operator's own code: nothing extra to explain
        mk_code, mk_name = mine[0]
    elif len(cands) == 1:
        mk_code, mk_name = cands[0]
    else:
        known = [c for c in cands if c[0] in AIRLINES]         # several airlines share this code: only trust a clear winner
        if len(known) != 1:
            return "", "", ""
        mk_code, mk_name = known[0]
    flight = "{} {}".format(iata, digits)
    if mk_code == op_code:
        same = digits.lstrip("0") == callsign[3:].lstrip("0")
        return mk_name, ("" if same else flight), ""
    return MARKETING_BRANDS.get(iata, mk_name), flight, (STANDING_DATA.airline_name(op_code) or op_code)


class AirportIndex:
    """Every airport in the world by IATA code (FR24's feed only gives the 3-letter code, e.g. AKL), so the
    sidebar can say "Auckland" under it. One CSV from the same CC0 standing data, cached on disk for 30 days."""
    URL = os.environ.get("AIRPORTS_CSV_URL", "https://vrs-standing-data.adsb.lol/airports.csv")
    CITIES = {   # last-resort names for common SFO destinations, in case the download isn't possible
        "AKL": "Auckland", "SYD": "Sydney", "MEL": "Melbourne", "BNE": "Brisbane", "NRT": "Tokyo", "HND": "Tokyo",
        "KIX": "Osaka", "ICN": "Seoul", "TPE": "Taipei", "HKG": "Hong Kong", "SIN": "Singapore", "MNL": "Manila",
        "PVG": "Shanghai", "PEK": "Beijing", "CAN": "Guangzhou", "DEL": "Delhi", "BOM": "Mumbai", "BLR": "Bengaluru",
        "DXB": "Dubai", "DOH": "Doha", "AUH": "Abu Dhabi", "IST": "Istanbul", "TLV": "Tel Aviv", "LHR": "London",
        "LGW": "London", "CDG": "Paris", "FRA": "Frankfurt", "MUC": "Munich", "AMS": "Amsterdam", "ZRH": "Zurich",
        "MAD": "Madrid", "BCN": "Barcelona", "DUB": "Dublin", "CPH": "Copenhagen", "ARN": "Stockholm", "KEF": "Reykjavik",
        "FCO": "Rome", "LIS": "Lisbon", "YVR": "Vancouver", "YYZ": "Toronto", "YUL": "Montreal", "YYC": "Calgary",
        "YEG": "Edmonton", "MEX": "Mexico City", "GDL": "Guadalajara", "CUN": "Cancun", "SJD": "Los Cabos",
        "PVR": "Puerto Vallarta", "LIM": "Lima", "BOG": "Bogota", "PTY": "Panama City", "SAL": "San Salvador",
        "GUA": "Guatemala City", "PPT": "Papeete", "NAN": "Nadi", "HNL": "Honolulu", "OGG": "Kahului", "KOA": "Kona",
        "LIH": "Lihue", "ITO": "Hilo", "ANC": "Anchorage", "FAI": "Fairbanks", "GUM": "Guam",
        "SFO": "San Francisco", "OAK": "Oakland", "SJC": "San Jose"}

    def __init__(self):
        self.by_iata, self.failed_at, self.lock = None, 0.0, threading.Lock()

    def _load(self):
        path = os.path.join(HERE, "cache", "standing", "airports-all.csv")
        try:
            if os.path.isfile(path) and time.time() - os.path.getmtime(path) < 30 * 86400:
                text = open(path, encoding="utf-8-sig").read()
            else:
                text = AIRPORT_LIST.get_text(self.URL, timeout=40)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(path + ".tmp", path)
            idx = {}
            for r in csv.DictReader(io.StringIO(text)):
                g = lambda *ks: next((r[k].strip() for k in ks if r.get(k) and r[k].strip()), None)
                iata = (g("IATA", "iata") or "").upper()
                if len(iata) != 3:
                    continue
                try:
                    lat, lon = float(g("Latitude", "latitude", "lat")), float(g("Longitude", "longitude", "lon"))
                except (TypeError, ValueError):
                    lat = lon = None
                idx.setdefault(iata, {"iata": iata, "icao": g("ICAO", "icao"), "city": g("Location", "location", "City", "city"),
                                      "name": g("Name", "name"), "lat": lat, "lon": lon})
            if idx:
                self.by_iata = idx
            else:
                raise ValueError("no airports parsed")
        except Exception as e:
            self.failed_at = time.time()
            print("[airports] couldn't load the airport list ({}); city names fall back to a short built-in list".format(type(e).__name__))

    def get(self, iata):
        iata = (iata or "").upper()
        with self.lock:
            if self.by_iata is None and time.time() - self.failed_at > 3600:
                self._load()
        a = (self.by_iata or {}).get(iata)
        if a:
            return a
        return {"iata": iata, "icao": None, "city": self.CITIES.get(iata), "name": None} if iata in self.CITIES else None


AIRPORTS = AirportIndex()


def live_route(cs, lat, lon, trk, vs=None):
    """Ask the live route API(s) (adsb.lol-style 'routeset': callsign + position in, route out, with a
    'plausible' verdict for where the plane is). Returns a route, None (the service says this callsign
    is not on a plausible route right now), or NO_ANSWER (down / empty / doesn't know the callsign)."""
    for url, th in ROUTE_APIS:
        try:
            d = th.get_json(url, payload={"planes": [{"callsign": cs, "lat": lat, "lng": lon}]})
        except (Transient, NotFound):
            continue
        e = next((x for x in (d if isinstance(d, list) else []) if (x.get("callsign") or "").upper() == cs), None)
        aps = [_airport({"iata": a.get("iata"), "icao": a.get("icao"), "city": a.get("location"),
                         "name": a.get("name"), "lat": a.get("lat"), "lon": a.get("lon")})
               for a in ((e or {}).get("_airports") or [])]
        if not e or len(aps) < 2:
            continue
        if e.get("plausible") is False:
            return None
        i = pick_leg(aps, lat, lon, trk, strict=False, vs=vs) or 0
        code = (e.get("airline_code") or "").upper()
        try:
            name = STANDING_DATA.airline_name(code) if code else None
        except Transient:
            name = AIRLINES.get(code)
        return _route(aps[i], aps[i + 1], name, urllib.parse.urlparse(url).hostname)
    return NO_ANSWER


DOWN = object()        # "this source could not be reached"


class FR24Blocked(Exception):
    """FR24 answered with something other than feed data (bot protection page, 403, ...)."""


class FR24Feed:
    """FlightRadar24's own website map feed (unofficial).

    ONE request returns every aircraft FR24 sees in an area, and each one carries its ICAO24 hex (so it joins
    straight onto your receiver's aircraft), registration, type, flight number and the origin and destination
    airports of THAT flight - which settles callsign reuse and tells SFO traffic from OAK/SJC traffic outright.

    Two ways to fetch it:
      * built-in (default): a plain standard-library request with the same URL, parameters and browser-style
        headers FR24's website sends. No packages needed.
      * the FlightRadarAPI library, used automatically ONLY if the built-in request keeps getting blocked and
        the library happens to be installed (it impersonates a browser's TLS fingerprint).
    Unofficial: it can break or be blocked at any time, and then everything quietly falls back to other sources."""

    HOSTS = ["https://data-cloud.flightradar24.com", "https://data-live.flightradar24.com"]
    PARAMS = {"faa": "1", "satellite": "1", "mlat": "1", "flarm": "1", "adsb": "1", "gnd": "1", "air": "1",
              "vehicles": "1", "estimated": "1", "maxage": "14400", "gliders": "1", "stats": "1", "limit": "5000"}
    HEADERS = {
        "accept": "application/json",
        "accept-encoding": "gzip",                       # no brotli in the standard library, so don't ask for it
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "origin": "https://www.flightradar24.com",
        "referer": "https://www.flightradar24.com/",
        "sec-ch-ua": '"Google Chrome";v="136", "Chromium";v="136", "Not-A.Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    }

    def __init__(self):
        self.flights, self.updated, self.status, self.client = {}, 0.0, "off", "builtin"
        self.fails, self.blocked_in_a_row, self.last_log, self.announced = 0, 0, 0.0, False

    def set_area(self, lat, lon, radius_nm):
        dlat = radius_nm / 60.0
        dlon = dlat / math.cos(math.radians(lat))
        self.radius_nm = radius_nm
        self.bounds = "{:.3f},{:.3f},{:.3f},{:.3f}".format(lat + dlat, lat - dlat, lon - dlon, lon + dlon)   # north,south,west,east

    def start(self, lat, lon, radius_nm, interval, client="auto"):
        self.set_area(lat, lon, radius_nm)
        self.urls = [os.environ["FR24_FEED_URL"]] if os.environ.get("FR24_FEED_URL") else [h + "/zones/fcgi/feed.js" for h in self.HOSTS]
        self.mode, self.interval, self.status = client, max(5, interval), "starting"
        self.client = "library" if client == "library" else "builtin"
        import importlib.util
        self.lib_ok = importlib.util.find_spec("FlightRadarAPI") is not None
        threading.Thread(target=self._run, daemon=True).start()
        return True

    # ---- the two clients -----------------------------------------------------------------------
    def _builtin(self):
        q = dict(self.PARAMS, bounds=self.bounds)
        last = None
        for base in self.urls:                           # data-cloud first, data-live as a spare
            url = base + "?" + urllib.parse.urlencode(q)
            for attempt in range(4):                     # FR24 now and then returns an empty area; ask again
                req = urllib.request.Request(url, headers=self.HEADERS)
                try:
                    with urllib.request.urlopen(req, timeout=20) as r:
                        raw = r.read()
                        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                            import gzip
                            raw = gzip.decompress(raw)
                except urllib.error.HTTPError as e:
                    last = FR24Blocked("HTTP {} from {}".format(e.code, urllib.parse.urlparse(base).hostname))
                    break                                # try the other host
                text = raw.decode("utf-8", "replace").lstrip()
                if not text.startswith("{"):             # an HTML challenge page, not data
                    last = FR24Blocked("got a web page instead of feed data (bot protection)")
                    break
                d = json.loads(text)
                rows = {k: v for k, v in d.items() if k[:1].isdigit() and isinstance(v, list)}
                if rows or not d.get("full_count"):
                    return [(v[0], v[11], v[12], v[9], v[8], v[13], v[14], v[16]) for v in rows.values() if len(v) > 16]
                time.sleep(1.5)
            # empty four times (or blocked): fall through to the next host
        raise last or FR24Blocked("feed kept coming back empty")

    def _library(self):
        from FlightRadarAPI import FlightRadar24API      # optional: pip install FlightRadarAPI
        from FlightRadarAPI.core import Core
        if os.environ.get("FR24_FEED_URL"):
            Core.real_time_flight_tracker_data_url = os.environ["FR24_FEED_URL"]
        if not hasattr(self, "api"):
            self.api = FlightRadar24API(timeout=20)
        return [(f.icao_24bit, f.origin_airport_iata, f.destination_airport_iata, f.registration, f.aircraft_code,
                 f.number, f.on_ground, f.callsign) for f in self.api.get_flights(bounds=self.bounds)]

    def _log(self, msg, every=300):
        if time.time() - self.last_log > every:
            self.last_log = time.time()
            print("[fr24] {}".format(msg))

    def _run(self):
        while True:
            wait = self.interval
            try:
                rows = self._library() if self.client == "library" else self._builtin()
                snap = {}
                for hx, orig, dest, reg, typ, num, gnd, cs in rows:
                    hx = (hx or "").lower()
                    if len(hx) == 6:
                        try:                                       # operator name from the callsign's airline code
                            op = STANDING_DATA.airline_name(cs[:3].upper()) if cs and cs[:3].isalpha() else None
                        except Exception:
                            op = None
                        try:                                       # who the flight is sold as, and its flight number
                            mk, fn, via = marketing_info(cs, num)
                        except Exception:
                            mk = fn = via = ""
                        snap[hx] = [cs or "", orig or "", dest or "", reg or "", typ or "", num or "", 1 if gnd else 0, op or "",
                                    mk, fn, via]
                self.flights, self.updated, self.status, self.fails, self.blocked_in_a_row = snap, time.time(), "ok", 0, 0
                if not self.announced:
                    self.announced = True
                    print("[fr24] feed working ({}, {} aircraft in the area)".format('built-in client' if self.client == 'builtin' else 'FlightRadarAPI library', len(snap)))
            except FR24Blocked as e:
                self.fails += 1; self.blocked_in_a_row += 1; self.status = "error"
                can_switch = self.client == "builtin" and self.mode == "auto" and self.lib_ok
                if can_switch and self.blocked_in_a_row >= 2:
                    self.client, self.blocked_in_a_row, self.fails = "library", 0, 0
                    print("[fr24] built-in request blocked ({}); switching to the installed FlightRadarAPI library".format(e))
                    continue
                if self.client == "builtin" and not self.lib_ok and self.blocked_in_a_row >= 3:
                    self._log("feed blocked ({}). Routes fall back to other sources; it keeps retrying. "
                              "If this persists, installing the library may help:  pip install FlightRadarAPI".format(e))
                else:
                    self._log("feed blocked ({}); routes fall back to other sources and it keeps retrying.".format(e))
                wait = self.interval if can_switch else min(300, self.interval * 2 ** min(self.fails, 5))
            except ImportError:
                self.status = "missing"
                self._log("FlightRadarAPI library requested but not installed:  pip install FlightRadarAPI")
                wait = 300
            except Exception as e:                                           # offline, timeout, format change...
                self.fails += 1; self.status = "error"
                hint = " Try  pip install -U FlightRadarAPI ." if self.client == "library" and "Cloudflare" in type(e).__name__ else ""
                self._log("feed failed ({}: {}); routes fall back to other sources and it keeps retrying.{}".format(type(e).__name__, e, hint))
                wait = min(300, self.interval * 2 ** min(self.fails, 5))
            time.sleep(wait)

    def get(self, hexcode):
        """This aircraft's FR24 entry if the feed is fresh, else None."""
        if not hexcode or time.time() - self.updated > 120:
            return None
        return self.flights.get(hexcode.lower())


FEED = FR24Feed()


def fr24_route(hexcode):
    """The route FR24 has for this exact flight (matched on ICAO24 hex), or NO_ANSWER."""
    e = FEED.get(hexcode)
    if not e or not e[1] or not e[2]:
        return NO_ANSWER
    callsign, orig, dest = e[0], e[1], e[2]

    def ap(iata):
        info = {"iata": iata, "icao": None, "city": None, "name": None}
        a = AIRPORTS.get(iata)                                                # the worldwide list (e.g. AKL -> Auckland)
        if not a or not a.get("city"):
            try:                                                              # US airports: ICAO is "K" + IATA
                k = STANDING_DATA.airport("K" + iata) if len(iata) == 3 else None
            except Transient:
                k = None
            a = k if k and k.get("iata") == iata else a
        if a:
            info.update({k: v for k, v in a.items() if v is not None and k != "iata"})
        return info

    try:
        airline = STANDING_DATA.airline_name(callsign[:3].upper()) if callsign[:3].isalpha() else None
    except Transient:
        airline = None
    r = _route(ap(orig), ap(dest), airline, "Flightradar24")
    r["verified"] = True
    return r


def adsbdb_route(cs, lat, lon, trk, vs=None):
    """adsbdb.com: one stored route per callsign, accepted only if the plane's position (and heading,
    away from airports) fit it. -> route | None (it has a route, but this plane isn't flying it) |
    NO_ANSWER (no record) | DOWN (unreachable)."""
    try:
        d = ADSBDB.get_json("{}/v0/callsign/{}".format(ADSBDB_BASE, cs))
    except NotFound:
        return NO_ANSWER
    except Transient:
        return DOWN
    r = (d.get("response") or {})
    r = r.get("flightroute") if isinstance(r, dict) else None
    if not isinstance(r, dict):
        return NO_ANSWER

    def ap(x):
        x = x or {}
        return _airport({"iata": x.get("iata_code"), "icao": x.get("icao_code"), "city": x.get("municipality"),
                         "name": x.get("name"), "lat": x.get("latitude"), "lon": x.get("longitude")})

    aps = [ap(r.get("origin")), ap(r.get("destination"))]
    if lat is not None and lon is not None and pick_leg(aps, lat, lon, trk, strict=True, vs=vs) is None:
        return None
    return _route(aps[0], aps[1], (r.get("airline") or {}).get("name"), "adsbdb")


def local_route(cs, lat, lon, trk, vs=None):
    """The CC0 standing data on disk (see StandingData). Same return values as adsbdb_route."""
    try:
        code, codes = STANDING_DATA.route_codes(cs)
        if code is None or codes is None:
            return NO_ANSWER
        aps = [STANDING_DATA.airport(c) for c in codes]
        if len(aps) < 2 or not all(aps):
            return NO_ANSWER
        i = pick_leg(aps, lat, lon, trk, strict=True, vs=vs) if (lat is not None and lon is not None) else 0
        if i is None:
            return None
        return _route(aps[i], aps[i + 1], STANDING_DATA.airline_name(code), "VRS standing data")
    except Transient:
        return DOWN


def lookup_route(cs, lat, lon, trk, vs=None, hexcode=None):
    """Route for this callsign *as flown by this plane right now*.

    Airlines reuse callsigns and the databases store ONE route per callsign, so a stored route is only
    a candidate: it is accepted only if the plane's position, direction and climb/descent fit one of its
    legs. A wrong route is worse than none, so misfits are dropped.

    Sources, in order: the FR24 feed (if on; per-flight, authoritative) -> live route API (adsb.lol-style, does its own
    plausibility check) -> adsbdb -> local CC0 standing data. The FIRST ROUTE THAT FITS wins: one source's misfit or gap never hides a
    route another source has right. No route is shown only if none of them has one that fits."""
    if STANDING_DATA.normalise(cs)[0] is None:
        return None                                # a registration or IATA-style callsign: no route to look up
    r = fr24_route(hexcode)                        # FR24 knows this exact flight: no guessing needed
    if isinstance(r, dict):
        return r
    results = []
    if lat is not None and lon is not None and ROUTE_APIS:
        results.append(live_route(cs, lat, lon, trk, vs))
    for fn in (adsbdb_route, local_route):
        if results and isinstance(results[-1], dict):
            break
        results.append(fn(cs, lat, lon, trk, vs))
    for r in results:
        if isinstance(r, dict):
            return r
    if results and all(r is DOWN for r in results[-2:]):
        raise Transient("no route source reachable")
    return None


def cached(key, fn, ttl_hit, ttl_miss):
    """ttl_hit / ttl_miss may be numbers or functions of the result."""
    """Cache fn() results. Real answers (found / not found) are kept a long time;
    transient failures are remembered only briefly so they get retried soon."""
    ok, val = cache_get(key)
    if ok:
        return val
    try:
        val = fn()
    except Transient:
        cache_put(key, TRANSIENT, 45)
        return TRANSIENT
    t = ttl_hit if val else ttl_miss
    cache_put(key, val, t(val) if callable(t) else t)
    return val


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    receiver = ""
    config = {}
    tile_url = ""

    def log_message(self, fmt, *args):
        pass

    def send_bytes(self, body, ctype, status=200, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True           # the browser stopped waiting (page reload, cancelled tile): fine

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj).encode(), "application/json", status)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                page = f.read()
            # static files are cached for a day; stamp their URLs so an update is picked up at once
            stamp = str(int(max(os.path.getmtime(os.path.join(STATIC, p)) for p in ("css/dashboard.css", "js/dashboard.js"))))
            self.send_bytes(page.replace(b"__VERSION__", stamp.encode()), "text/html; charset=utf-8")

        elif path.startswith("/static/"):
            full = os.path.realpath(os.path.join(STATIC, path[len("/static/"):]))
            if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
                return self.send_error(404)
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                self.send_bytes(f.read(), ctype, cache="public, max-age=86400")

        elif path == "/api/fr24":
            self.send_json({"status": FEED.status, "client": FEED.client, "age": round(time.time() - FEED.updated, 1) if FEED.updated else None,
                            "n": len(FEED.flights), "flights": FEED.flights})

        elif path == "/api/config":
            self.send_json(self.config)

        elif path == "/api/aircraft":
            try:
                d = fetch_json(self.receiver + "/data/aircraft.json", timeout=5)
                d["_cfg_rev"] = self.config.get("config_rev", 0)       # the page reloads itself if this changes
                self.send_json(d)
            except Exception as e:
                self.send_json({"error": str(e)}, 502)

        elif re.fullmatch(r"/api/info/[0-9a-fA-F]{6}", path):
            # Aircraft details only. Photos are fetched by the browser straight from Planespotters
            # (as tar1090 does); here we just offer adsbdb's photo as a fallback.
            hx = path.rsplit("/", 1)[1].lower()
            ac = cached("a:" + hx, lambda: lookup_aircraft(hx), 24 * 3600, 6 * 3600)
            transient = ac is TRANSIENT
            if transient:
                ac = None
            photo = None
            if ac and ac.get("fallback_photo"):
                photo = {"src": ac["fallback_photo"], "link": None, "credit": None, "source": "airport-data.com"}
            self.send_json({"hex": hx, "photo": photo, "aircraft": ac, "retry": transient})

        elif re.fullmatch(r"/api/route/[A-Za-z0-9]{2,8}", path):
            cs = path.rsplit("/", 1)[1].upper()
            q = urllib.parse.parse_qs(self.path.partition("?")[2])

            def num(k, lo, hi):
                try:
                    v = float(q.get(k, [""])[0])
                    return v if lo <= v <= hi else None
                except ValueError:
                    return None

            lat, lon, trk, vs = num("lat", -90, 90), num("lon", -180, 180), num("trk", 0, 360), num("vs", -9000, 9000)
            hx = (q.get("hex", [""])[0] or "")[:6].lower()
            hx = hx if re.fullmatch(r"[0-9a-f]{6}", hx) else ""
            feed_on = FEED.status in ("ok", "starting", "error")
            ttl = lambda v: 900 if (not feed_on or (v and v.get("verified"))) else 45       # re-check soon so FR24's answer can take over
            rt = cached("r:{}:{}".format(cs, hx), lambda: lookup_route(cs, lat, lon, trk, vs, hx), ttl, ttl)
            operator = STANDING_DATA.airline_name(cs[:3]) if re.match(r"^[A-Z]{3}\d", cs) else None   # who flies it, from the callsign
            self.send_json({"callsign": cs, "operator": operator, "route": None if rt is TRANSIENT else rt, "verified": bool(rt and rt is not TRANSIENT and rt.get("verified")),
                            "retry": rt is TRANSIENT, "ttl": 60 if rt is TRANSIENT else (ttl(rt) if rt else 45 if feed_on else 180)})

        elif re.fullmatch(r"/tiles/[a-z]+/\d{1,2}/\d{1,6}/\d{1,6}\.png", path):
            # the map source is part of the address, so a browser can never mix up tiles cached from another source
            # (CARTO's dark tiles shown under the OpenStreetMap filter came out pale grey)
            self.serve_tile("/tiles/" + path.split("/", 3)[3])
        elif re.fullmatch(r"/tiles/\d{1,2}/\d{1,6}/\d{1,6}\.png", path):
            self.serve_tile(path)

        else:
            self.send_error(404)

    def serve_tile(self, path):
        _, _, z, x, y = path.split("/")
        y = y[:-4]
        local = os.path.join(CACHE, z, x, y + ".png")
        fresh = os.path.isfile(local) and time.time() - os.path.getmtime(local) < 30 * 86400
        if not fresh:
            try:
                url = self.tile_url.format(z=z, x=x, y=y)
                data = fetch_bytes(url, timeout=8)
                os.makedirs(os.path.dirname(local), exist_ok=True)
                with open(local + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(local + ".tmp", local)
            except Exception as e:
                if not os.path.isfile(local):  # nothing cached to fall back on
                    print("[tile] {}/{}/{}: {}".format(z, x, y, e))
                    return self.send_error(404)
        with open(local, "rb") as f:
            self.send_bytes(f.read(), "image/png", cache="public, max-age=604800")


def parse_receiver_location(d):
    """(lat, lon) from a receiver.json dict, or None if it carries no usable location."""
    try:
        lat, lon = float(d["lat"]), float(d["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon


def looks_rounded(lat, lon):
    """readsb / dump1090 can publish an 'approximate' location. Coordinates that are both whole multiples of 0.01
    degree (about 1 km) were almost certainly rounded, which is too coarse for 'could I see that plane from here'."""
    return abs(lat - round(lat, 2)) < 1e-9 and abs(lon - round(lon, 2)) < 1e-9


def probe_receiver_location(base):
    """Ask the receiver where it is. Returns one of
         ("found", (lat, lon))   it published a location
         ("none", None)          it answered, but publishes no location (switched off, or an old version)
         ("unreachable", None)   couldn't ask - it may still be booting."""
    try:
        d = fetch_json(base + "/data/receiver.json", timeout=5)
    except urllib.error.HTTPError as e:
        return ("none", None) if e.code in (404, 410) else ("unreachable", None)
    except ValueError:                                   # a web page instead of JSON: no receiver.json here
        return "none", None
    except Exception:
        return "unreachable", None
    loc = parse_receiver_location(d if isinstance(d, dict) else {})
    return ("found", loc) if loc else ("none", None)


# Where the map is centred when no location is configured: the middle of the Stanford campus (Main Quad).
# It is only a stand-in; set "lat" and "lon" in config.json to wherever your antenna really is.
DEFAULT_LAT, DEFAULT_LON = 37.4275, -122.1697


def load_config(path):
    """Settings from a JSON file (keys = option names with underscores, e.g. "carto_key"). Missing file = {}."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        conf = json.load(f)
    return {k: v for k, v in conf.items() if not k.startswith("_")}      # "_comment" keys are ignored


def parse_args(argv=None):
    env = os.environ.get
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=env("OVERHEAD_CONFIG", os.path.join(HERE, "config.json")))
    config_path = pre.parse_known_args(argv)[0].config
    ap = argparse.ArgumentParser(description="Overhead: a live ADS-B wall display.")
    ap.add_argument("--config", default=config_path, help="JSON settings file (default: config.json next to server.py)")
    ap.add_argument("--receiver", default=env("RECEIVER_URL", "http://localhost:8080"),
                    help="base URL of your tar1090 / dump1090 web page")
    ap.add_argument("--host", default=env("HOST", "127.0.0.1"),
                    help="use 0.0.0.0 to let other machines on the network open the dashboard")
    ap.add_argument("--port", type=int, default=int(env("PORT", 8081)))
    ap.add_argument("--lat", type=float, default=float(env("LAB_LAT")) if env("LAB_LAT") else None, help="receiver latitude (default: Stanford campus)")
    ap.add_argument("--lon", type=float, default=float(env("LAB_LON")) if env("LAB_LON") else None, help="receiver longitude (default: Stanford campus)")
    ap.add_argument("--lab-elev-ft", type=float, default=float(env("LAB_ELEV_FT", 100)))
    ap.add_argument("--lab-name", default=env("LAB_NAME", "ADL"), help="label for the receiver on the map")
    ap.add_argument("--title", default=env("TITLE", "Overhead"))
    ap.add_argument("--subtitle", default=env("SUBTITLE", "Live from the ADL rooftop"), help="text in the top-left corner")
    ap.add_argument("--radius-nm", type=float, default=float(env("RADIUS_NM", 25)),
                    help="how far out to consider 'nearby' traffic")
    ap.add_argument("--west-nm", type=float, default=float(env("WEST_NM", 7)),
                    help="how far west of SFO the map reaches (arrivals over the Peninsula hills)")
    ap.add_argument("--view-nm", type=float, default=float(env("VIEW_NM", 0.6)),
                    help="margin (nm) around the receiver, SFO, OAK and SJC, which the map is framed on")
    ap.add_argument("--vis-nm", type=float, default=float(env("VIS_NM", 2)),
                    help="max straight-line distance at which a plane counts as visible")
    ap.add_argument("--min-elev", type=float, default=float(env("MIN_ELEV", 10)),
                    help="min degrees above the horizon to count as visible")
    ap.add_argument("--vis-max-alt", type=float, default=float(env("VIS_MAX_ALT", 7000)),
                    help="planes above this altitude (ft) don't count as 'in sight'")
    ap.add_argument("--route-api", default=env("ROUTE_API_URLS", ""),
                    help="optional live route service(s) in adsb.lol 'routeset' format, comma-separated, tried first "
                         "(e.g. https://api.adsb.lol/api/0/routeset). Off by default: that one stopped answering in 2026.")
    ap.add_argument("--overview-nm", type=float, default=float(env("OVERVIEW_NM", 200)),
                    help="radius of the receiver-range overview in the corner of the map")
    ap.add_argument("--allow-ga", action="store_true", default=bool(env("ALLOW_GA")),
                    help="also feature general-aviation aircraft (default: airline/cargo/etc. traffic only)")
    ap.add_argument("--no-fr24", action="store_true", default=bool(env("NO_FR24")),
                    help="don't use the (unofficial) Flightradar24 map feed")
    ap.add_argument("--fr24-radius-nm", type=float, default=float(env("FR24_RADIUS_NM", 60)),
                    help="area of the FR24 feed around the receiver/SFO midpoint")
    ap.add_argument("--fr24-client", choices=["auto", "builtin", "library"], default=env("FR24_CLIENT", "auto"),
                    help="auto = built-in request, switching to the FlightRadarAPI library only if blocked and installed")
    ap.add_argument("--fr24-sec", type=int, default=int(env("FR24_SEC", 15)),
                    help="seconds between FR24 feed refreshes (min 5; be polite)")
    ap.add_argument("--dwell", type=int, default=int(env("DWELL_SEC", 14)),
                    help="seconds each aircraft is featured")
    ap.add_argument("--basemap", choices=["carto", "osm"], default=env("BASEMAP"),
                    help="carto = dark CARTO map (needs a free key); osm = OpenStreetMap, darkened in the "
                         "browser, no key. Default: carto if a key is given, otherwise osm.")
    ap.add_argument("--carto-key", default=env("CARTO_KEY", ""),
                    help="free key from https://carto.com/basemaps/apikey/")
    ap.add_argument("--tile-url", default=env("TILE_URL"),
                    help="advanced: override the tile URL template ({z}/{x}/{y})")
    ap.add_argument("--tile-size", type=int, default=int(env("TILE_SIZE", 0)),
                    help="advanced: pixel size of tiles from --tile-url (256 or 512)")
    conf = load_config(config_path)
    known = {a.dest for a in ap._actions}
    unknown = sorted(set(conf) - known)
    if unknown:
        print("[config] ignoring unknown setting(s) in {}: {}".format(config_path, ", ".join(unknown)))
    ap.set_defaults(**{k: v for k, v in conf.items() if k in known})
    args = ap.parse_args(argv)
    if (args.lat is None) != (args.lon is None):
        ap.error("set both \"lat\" and \"lon\" (in config.json or on the command line), not just one")
    args.default_location = args.lat is None
    if args.default_location:
        args.lat, args.lon = DEFAULT_LAT, DEFAULT_LON
    return args


def fr24_center(lat, lon):
    """The FR24 feed is centred halfway between the receiver and SFO, so it covers both."""
    return (lat + 37.6213) / 2, (lon + (-122.3790)) / 2


def apply_location(lat, lon, radius_nm):
    """Switch a running server to a newly discovered location: config, FR24 feed area, and tell open pages."""
    Handler.config["lat"], Handler.config["lon"] = lat, lon
    Handler.config["location_source"] = "receiver"
    if FEED.status != "off":
        c = fr24_center(lat, lon)
        FEED.set_area(c[0], c[1], radius_nm)
    Handler.config["config_rev"] = Handler.config.get("config_rev", 0) + 1       # open pages reload themselves
    print("Location: the receiver is up now; took its location from receiver.json.")
    if looks_rounded(lat, lon):
        print("          It looks rounded (approximate). For accurate in-sight results set \"lat\" and \"lon\" in config.json.")


def watch_for_receiver_location(receiver, fr24_radius_nm):
    """The receiver may boot after this machine (e.g. after a power cut): keep asking, politely, until it answers."""
    wait = float(os.environ.get("LOCATION_RETRY_SEC", 15))
    while True:
        time.sleep(wait)
        status, found = probe_receiver_location(receiver)
        if status == "found":
            apply_location(found[0], found[1], fr24_radius_nm)
            return
        if status == "none":
            print("Location: the receiver answered but publishes no location; staying on the Stanford campus default.")
            return
        wait = min(300.0, wait * 1.5)


def main():
    args = parse_args()

    basemap = args.basemap or ("carto" if args.carto_key else "osm")
    if basemap == "carto" and not args.carto_key:
        print("NOTE: CARTO now requires a free API key (https://carto.com/basemaps/apikey/).")
        print("      No key given, so using OpenStreetMap tiles instead.")
        basemap = "osm"
    if basemap == "carto":
        tile_url = ("https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png?key="
                    + urllib.parse.quote(args.carto_key))
        tile_size = 512
        attribution = "Map \u00A9 OpenStreetMap contributors \u00A9 CARTO"
    else:
        tile_url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        tile_size = 256
        attribution = "Map \u00A9 OpenStreetMap contributors"
    if args.tile_url:
        tile_url = args.tile_url
        tile_size = args.tile_size or 512
    global CACHE
    CACHE = os.path.join(HERE, "cache", "tiles-" + basemap)

    receiver = args.receiver.rstrip("/")
    location_source, location_note, location_status = "config", None, None
    if args.default_location:                                        # nothing configured: ask the receiver itself
        location_status, found = probe_receiver_location(receiver)
        if location_status == "found":
            args.lat, args.lon = found
            location_source = "receiver"
            location_note = "Location: taken from the receiver's own receiver.json."
            if looks_rounded(*found):
                location_note += ("\n          It looks rounded (the receiver may publish only an approximate position). "
                                  "For accurate in-sight results set \"lat\" and \"lon\" in config.json.")
        elif location_status == "none":
            location_source = "default"
            location_note = ("Location: the receiver publishes none, so using the Stanford campus default. "
                             "Set \"lat\" and \"lon\" in config.json to your antenna's position.")
        else:
            location_source = "default"
            location_note = ("Location: couldn't reach the receiver yet, so using the Stanford campus default for now. "
                             "It keeps asking and switches over by itself once the receiver answers.")
    ensure_assets()
    if not args.no_fr24:
        centre = fr24_center(args.lat, args.lon)
        FEED.start(centre[0], centre[1], args.fr24_radius_nm, args.fr24_sec, args.fr24_client)
    for u in [x.strip() for x in args.route_api.split(",") if x.strip()]:
        ROUTE_APIS.append((u, Throttle("route api " + (urllib.parse.urlparse(u).hostname or u), 0.5,
                                       " - using other route sources instead")))
    threading.Thread(target=STANDING_DATA.warm, daemon=True).start()
    Handler.receiver = args.receiver.rstrip("/")
    Handler.tile_url = tile_url
    Handler.config = {
        "lat": args.lat, "lon": args.lon, "location_source": location_source, "config_rev": 0,
        "lab_elev_ft": args.lab_elev_ft, "lab_name": args.lab_name,
        "title": args.title, "subtitle": args.subtitle,
        "radius_nm": args.radius_nm, "view_nm": args.view_nm, "west_nm": args.west_nm,
        "fr24_feed": FEED.status == "starting", "overview_nm": args.overview_nm, "allow_ga": args.allow_ga, "vis_nm": args.vis_nm, "vis_max_alt": args.vis_max_alt, "min_elev": args.min_elev, "dwell_sec": args.dwell,
        "sfo": {"lat": 37.6213, "lon": -122.3790},
        "basemap": basemap, "attribution": attribution + " \u00B7 Routes: " + ("Flightradar24, " if FEED.status == "starting" else "") + "adsb.lol, adsbdb, VRS standing data (CC0) \u00B7 Photos: Planespotters.net",
        "tile_size": tile_size, "tile_offset": 0 if tile_size == 256 else -1,
    }
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Receiver: {}".format(Handler.receiver))
    if os.path.isfile(args.config):
        print("Settings: {}".format(args.config))
    if location_note:
        print(location_note)
    if location_status == "unreachable":
        threading.Thread(target=watch_for_receiver_location, args=(receiver, args.fr24_radius_nm), daemon=True).start()
    print("Basemap:  {}".format(basemap))
    print("Open http://localhost:{}".format(args.port))
    srv.serve_forever()


if __name__ == "__main__":
    main()
