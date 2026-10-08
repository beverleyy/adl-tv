/* Overhead dashboard - browser side. Served by server.py; see README.md. */
/* =====================================================================
   helpers
   ===================================================================== */
const $ = id => document.getElementById(id);
const R_NM = 3440.065, rad = d => d * Math.PI / 180, deg = r => r * 180 / Math.PI;
const dist = (a, b, c, d) => { const dl = rad(c - a), dn = rad(d - b);
  const h = Math.sin(dl / 2) ** 2 + Math.cos(rad(a)) * Math.cos(rad(c)) * Math.sin(dn / 2) ** 2;
  return 2 * R_NM * Math.asin(Math.sqrt(h)); };
const brg = (a, b, c, d) => { const dn = rad(d - b);
  const y = Math.sin(dn) * Math.cos(rad(c));
  const x = Math.cos(rad(a)) * Math.sin(rad(c)) - Math.sin(rad(a)) * Math.cos(rad(c)) * Math.cos(dn);
  return (deg(Math.atan2(y, x)) + 360) % 360; };
const angDiff = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };
const WORDS = ['north','northeast','east','southeast','south','southwest','west','northwest'];
const ABBR  = ['N','NE','E','SE','S','SW','W','NW'];
const word = b => WORDS[Math.round(b / 45) % 8], abbr = b => ABBR[Math.round(b / 45) % 8];
const fmt = n => Math.round(n).toLocaleString('en-US');
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const COLOR = { amber: css('--amber'), cyan: css('--cyan'), coral: css('--coral'), slate: css('--slate'), green: css('--green') };

const S = {
  cfg: null, planes: new Map(), info: {}, routes: {}, pend: new Set(), shown: {}, photoHex: null, fr: {}, frAt: 0, frStatus: 'off', range: null, rangeDirty: false, rangeSaved: 0, farthest: 0,
  cur: null, curSince: 0, changing: false,
  vis: [], arr: [], dep: [], cands: [], total: 0,
  lastNow: null, lastNowAt: Date.now(), lastOk: Date.now(), msgs: null, msgsAt: 0, msgRate: null,
};

/* =====================================================================
   map + canvas overlay
   ===================================================================== */
let map, fx, ctx, W = 0, H = 0, dpr = 1, unit = 1, scale = 256, cx0 = 0, cy0 = 0;

function worldX(lon) { return (lon + 180) / 360 * scale; }
function worldY(lat) { const s = Math.sin(rad(lat)); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale; }
const pt = [0, 0];
function proj(lat, lon) { pt[0] = worldX(lon) - cx0 + W / 2; pt[1] = worldY(lat) - cy0 + H / 2; return pt; }

const MAJOR = ['SFO', 'OAK', 'SJC'];                // the three commercial airports framed on the map and colour-coded
function fitMap() {
  // Frame the receiver + SFO, OAK, SJC (OAK as far from the top as SJC is from the bottom), reaching at least west_nm west of
  // SFO for the arrivals over the Peninsula hills - and slide it right until the receiver's whole 10 nm ring clears the radar
  // disc, which also shows more of the Pacific. If that would push SJC off the right edge, zoom out a quarter step.
  const c = S.cfg, west = L.latLng(...destPt(APT_BY.SFO.lat, APT_BY.SFO.lon, 270, c.west_nm));
  const pts = [[c.lat, c.lon], ...MAJOR.map(k => [APT_BY[k].lat, APT_BY[k].lon]), [west.lat, west.lng]];
  const la = pts.map(q => q[0]), lo = pts.map(q => q[1]), m = c.view_nm / 60, mLon = m / Math.cos(rad(c.lat));
  map.invalidateSize(false);
  const u = parseFloat(getComputedStyle(document.documentElement).fontSize), pad = 1.2 * u;
  const top = L.latLng(Math.max(...la) + m, c.lon), bottom = L.latLng(Math.min(...la) - m, c.lon);
  map.fitBounds(L.latLngBounds([bottom.lat, Math.min(...lo) - mLon], [top.lat, Math.max(...lo) + mLon]),
    { animate: false, paddingTopLeft: [pad, pad], paddingBottomRight: [pad, pad] });
  const size = map.getSize(), mr = $('map').getBoundingClientRect(), ovEl = document.querySelector('.ov');
  const dr = ovEl ? ovEl.getBoundingClientRect() : null;
  const disc = dr ? { x: dr.left - mr.left + dr.width / 2, y: dr.top - mr.top + dr.height / 2, r: dr.width / 2 } : null;
  let z = map.getZoom();
  for (let i = 0; i < 40; i++) {
    const P = ll => map.project(ll, z), pxNm = 1852 / (156543.03392 * Math.cos(rad(c.lat)) / Math.pow(2, z));
    const cy = (P(top).y + P(bottom).y) / 2;                                   // OAK and SJC equally padded
    let cx = P(west).x + size.x / 2 - (pad + c.view_nm * pxNm);               // west strip pinned to the left edge
    if (disc) {                                                                // ...then clear the 10 nm ring of the radar
      const lab = P(L.latLng(c.lat, c.lon)), lx = lab.x - cx + size.x / 2, ly = lab.y - cy + size.y / 2;
      const need = disc.r + 10.3 * pxNm, dy = ly - disc.y;
      if (Math.hypot(lx - disc.x, dy) < need) cx -= Math.sqrt(Math.max(0, need * need - dy * dy)) - (lx - disc.x);
    }
    const sjcX = P(L.latLng(APT_BY.SJC.lat, APT_BY.SJC.lon)).x - cx + size.x / 2;
    if (sjcX <= size.x - pad - c.view_nm * pxNm || i === 39) { map.setView(map.unproject(L.point(cx, cy), z), z, { animate: false }); break; }
    z -= 0.02;                                                                 // doesn't fit: zoom out a hair and retry
  }
  sizeCanvas();
}
function sizeCanvas() {
  dpr = window.devicePixelRatio || 1;
  W = fx.clientWidth; H = fx.clientHeight;
  fx.width = W * dpr; fx.height = H * dpr;
  unit = parseFloat(getComputedStyle(document.documentElement).fontSize) / 20;  // 1 at 20px base
  const dr = document.querySelector('.ov').getBoundingClientRect(), fr = fx.getBoundingClientRect();   // the overview disc hides the map beneath it
  S.dome = { cx: dr.left - fr.left + dr.width / 2, cy: dr.top - fr.top + dr.height / 2, r: dr.width / 2 * 1.04 };
  const ctr = map.getCenter();
  scale = 256 * Math.pow(2, map.getZoom());
  cx0 = worldX(ctr.lng); cy0 = worldY(ctr.lat);
}

/* =====================================================================
   data in
   ===================================================================== */
function predict(p, t) {            // where the plane should be at time t (ms), from its last report
  if (p.gs == null || p.track == null) return [p.lat, p.lon];
  const dt = Math.min(Math.max((t - p.tRep) / 1000, 0), 20);
  const d = p.gs / 3600 * dt;       // nm travelled
  return [p.lat + Math.cos(rad(p.track)) * d / 60,
          p.lon + Math.sin(rad(p.track)) * d / (60 * Math.cos(rad(p.lat)))];
}
function display(p, t) {            // predicted position plus a decaying correction so updates glide, not jump
  const [la, lo] = predict(p, t);
  const k = Math.max(0, 1 - (t - p.err.t) / 1400);
  return [la + p.err.dlat * k, lo + p.err.dlon * k];
}

function ingest(d) {
  const t = Date.now(), seen = new Set();
  for (const a of d.aircraft || []) {
    if (a.lat == null || a.lon == null) continue;
    const sp = a.seen_pos ?? 0; if (sp > 30) continue;
    seen.add(a.hex);
    let p = S.planes.get(a.hex);
    const isNew = !p;
    if (isNew) { p = { hex: a.hex, trail: [], err: { dlat: 0, dlon: 0, t: 0 } }; S.planes.set(a.hex, p); }

    p.ground = a.alt_baro === 'ground';
    p.alt = typeof a.alt_baro === 'number' ? a.alt_baro : null;
    p.altGeom = typeof a.alt_geom === 'number' ? a.alt_geom : null;
    p.gs = a.gs ?? null;
    p.track = a.track ?? p.track ?? null;
    p.rate = a.geom_rate ?? a.baro_rate ?? 0;
    p.flight = (a.flight || '').trim();
    p.r = a.r; p.t = a.t; p.cat = a.category; p.squawk = a.squawk; p.seenPos = sp;

    if (isNew || a.lat !== p.lat || a.lon !== p.lon) {
      const before = isNew ? null : display(p, t);
      p.lat = a.lat; p.lon = a.lon; p.tRep = t - sp * 1000;
      if (before) {
        const [la, lo] = predict(p, t), dlat = before[0] - la, dlon = before[1] - lo;
        p.err = (Math.abs(dlat) < 0.05 && Math.abs(dlon) < 0.05) ? { dlat, dlon, t } : { dlat: 0, dlon: 0, t };
      }
      p.trail.push([a.lat, a.lon, p.tRep]);
    }
  }
  for (const hex of [...S.planes.keys()]) if (!seen.has(hex)) S.planes.delete(hex);
  for (const p of S.planes.values()) { const cut = t - 240000; while (p.trail.length && p.trail[0][2] < cut) p.trail.shift(); if (p.trail.length > 400) p.trail.splice(0, p.trail.length - 400); }

  S.total = S.planes.size;
  if (d.now !== S.lastNow) { S.lastNow = d.now; S.lastNowAt = t; }
  if (d.messages != null) {
    if (S.msgs != null && t - S.msgsAt > 0 && d.messages >= S.msgs) {
      const r = (d.messages - S.msgs) / ((t - S.msgsAt) / 1000);
      S.msgRate = S.msgRate == null ? r : S.msgRate * .8 + r * .2;
    }
    S.msgs = d.messages; S.msgsAt = t;
  }
}

/* =====================================================================
   lookups (photo / type / route)
   ===================================================================== */
const inflight = k => [...S.pend].filter(x => x[0] === k).length;
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* Photos: ask Planespotters straight from this browser (the same call tar1090 makes).
   One request at a time, ~1/sec. If they refuse, back off and use the backup photo meanwhile. */
const PS = { blockedUntil: 0, fails: 0, state: 'ok' };
let psChain = Promise.resolve();
async function psFetch(hex) {            // -> photo | null (none exists) | undefined (couldn't ask)
  if (Date.now() < PS.blockedUntil) return undefined;
  try {
    const r = await fetch('https://api.planespotters.net/pub/photos/hex/' + hex);
    if (r.status === 404) { PS.fails = 0; PS.state = 'ok'; return null; }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json(); PS.fails = 0; PS.state = 'ok';
    const p = (d.photos || [])[0], big = p && (p.thumbnail_large || p.thumbnail);
    return big?.src ? { src: big.src, link: p.link, credit: p.photographer, source: 'Planespotters.net' } : null;
  } catch (e) {
    PS.fails++;                           // one hiccup is just retried; two in a row means back off (30s, 60s... up to 5 min)
    if (PS.fails >= 2) {
      PS.state = 'blocked';
      PS.blockedUntil = Date.now() + Math.min(300, 30 * 2 ** (PS.fails - 2)) * 1000;
      if (PS.fails === 2) console.warn('Planespotters lookups failing (' + e.message + '). Using backup photos and retrying later.');
    }
    return undefined;
  }
}
function psLookup(hex) {
  if (Date.now() < PS.blockedUntil) return Promise.resolve(undefined);
  const p = psChain.then(() => psFetch(hex));
  psChain = p.then(() => sleep(1200), () => sleep(1200));
  return p;
}

async function ensureInfo(hex, urgent) {
  const e = S.info[hex];
  if (e && Date.now() - e.t < (e.d?.retry ? 60000 : 600000)) return;
  if (S.pend.has('i' + hex)) return;
  if (!urgent && inflight('i') >= 2) return;           // be gentle with the lookup services
  S.pend.add('i' + hex);
  // Aircraft details (adsbdb) and the photo (Planespotters) are fetched in parallel, and a slow adsbdb is
  // given at most 2.5 s, so it can never hold the photo up.
  const infoP = Promise.race([fetch('api/info/' + hex).then(r => r.json()), sleep(2500).then(() => ({ retry: true }))]).catch(() => ({ retry: true }));
  const [d, ps] = await Promise.all([infoP, psLookup(hex)]);
  if (ps) d.photo = ps;                                // Planespotters beats the backup photo
  else if (ps === undefined) d.retry = true;           // couldn't ask - try again soon
  S.info[hex] = { t: Date.now(), d };
  S.pend.delete('i' + hex);
}

/* Routes: a callsign can be reused for several routes, so every lookup carries the plane's
   position and heading and the server returns only a route that fits where it actually is. */
const rkey = p => p.hex + '|' + p.flight;
const routeDue = p => p.flight && !/^N\d/.test(p.flight) && !(S.routes[rkey(p)]?.exp > Date.now()) && !S.pend.has('r' + rkey(p));
async function ensureRoute(p, urgent) {
  if (!p.flight || /^N\d/.test(p.flight)) return;
  const k = rkey(p), prev = S.routes[k];
  if (S.pend.has('r' + k)) return;
  if (prev && prev.exp > Date.now()) return;
  if (!urgent && inflight('r') >= 2) return;
  S.pend.add('r' + k);
  try {
    const q = `?hex=${p.hex}&lat=${p.lat.toFixed(4)}&lon=${p.lon.toFixed(4)}` + (p.track != null ? `&trk=${Math.round(p.track)}` : '') + `&vs=${Math.round(p.rate || 0)}`;
    const d = await (await fetch('api/route/' + encodeURIComponent(p.flight) + q)).json();
    S.routes[k] = { route: d.retry ? (prev?.route ?? null) : d.route, exp: Date.now() + (d.ttl || 180) * 1000, verified: !!d.verified, operator: d.operator || prev?.operator || null };
  } catch { S.routes[k] = { route: prev?.route ?? null, exp: Date.now() + 60000, operator: prev?.operator || null }; }
  S.pend.delete('r' + k);
}

/* =====================================================================
   classification
   ===================================================================== */
/* Which icon family an aircraft belongs to. By ICAO type designator when we know it (from the receiver's
   own data or adsbdb), else by the ADS-B emitter category, else by whether it flies under an airline callsign. */
const TYPE_CLASSES = [
  ['super',  /^(A388|A225)$/],
  ['quad',   /^(A124|A342|A343|A345|A346|B741|B742|B743|B744|B748|B74[SR]|B701|B703|B720|IL96|IL76|DC8\d?|C17|C5M?|K35R|KC35|B461|B462|B463|RJ1H|RJ70|RJ85|BA46|VC10|E3CF|E3TF|R135)$/],
  ['midlarge', /^(B752|B753|B762|B763|B764|A306|A30B|A310)$/],   // 757, 767, A300/A310: between the narrowbodies and the big widebodies
  ['large',  /^(A332|A333|A338|A339|A359|A35K|B772|B773|B77L|B77W|B778|B779|B788|B789|B78X|MD11|DC10|L101|IL86)$/],
  ['medium', /^(A318|A319|A320|A321|A19N|A20N|A21N|BCS3|B731|B732|B733|B734|B735|B736|B737|B738|B739|B37M|B38M|B39M|B3XM|B721|B722|MD8\d|MD9\d|C919|A223)$/],
  ['small',  /^(CRJ\d|CRJX|E135|E145|E45X|E170|E75[LS]|E190|E195|E290|E295|BCS1|B712|F100|F70|F28|SU95|AR85|ARJ\d)$/],
  ['prop',   /^(DH8[A-D]|AT4\d|AT5\d|AT6\d|AT7\d|SF34|JS3\d|JS4\d|B190|D328|F50|F27|SB20|E120|L410|AN2\d|AN32|C130|C30J|C160)$/],
  ['biz',    /^(C25[A-Z]|C500|C501|C510|C525|C526|C550|C551|C560|C56X|C650|C680|C68A|C700|C750|CL30|CL35|CL60|CL64|GLF\d|GALX|G150|G280|LJ\d\d|FA\d[A-Z0-9]|F2TH|F900|E50P|E55P|E35L|E545|E550|H25[A-C]|HDJT|ASTR|WW24|BE40|PRM1|SF50|EA50)$/],
  ['heli',   /^(R22|R44|R66|B06|B407|B412|B429|B212|EC\d\d|AS\d\d|S76|S92|S61|A109|A119|A139|H60|H47|UH1|MD52|MD60|EH10|H500|GAZL|CABR|B47G)$/],
  ['ga',     /^(C1\d\d|C2\d\d|C3\d\d|C4\d\d|PA\d\d|P28[A-Z]|SR2\d|S22T|BE\d\d|BE9[A-Z0-9]|B350|PC12|PC24|TBM\d|P180|M20[A-Z]|DA\d\d|DV20|AC\d\d|T6|AA5|BL\d\d|RV\d\d|COZY|GLID|ULAC)$/],
];
function acClass(p) {
  const t = (typeOf(p) || '').toUpperCase();
  if (t) for (const [cls, re] of TYPE_CLASSES) if (re.test(t)) return cls;
  const cat = p.cat || '', airline = /^[A-Z]{3}\d/.test(p.flight || '');
  if (cat === 'A7') return 'heli';
  if (/^(A1|B\d|C\d)$/.test(cat)) return 'ga';
  if (cat === 'A5') return 'large';
  if (cat === 'A4') return 'midlarge';              // ADS-B 'high vortex large' is exactly the 757
  if (cat === 'A3') return 'medium';
  if (cat === 'A2') return airline ? 'small' : 'biz';
  if (cat === 'A6') return 'biz';
  return airline ? 'medium' : 'ga';
}

/* General aviation is excluded from being featured: the fun here is the airline traffic into and out of SFO.
   GA = light aircraft, bizjets and helicopters, anything without an airline-style callsign (registrations like
   N123AB), and fractional-bizjet operators. They still show on the map and in the table, dimmed. */
const BIZ_OPERATORS = new Set(['EJA', 'LXJ', 'XOJ', 'VJT', 'WUP']);
function isGA(p) {
  if (S.cfg.allow_ga) return false;
  if (p.cls === 'ga' || p.cls === 'biz' || p.cls === 'heli') return true;
  const t = (typeOf(p) || '').toUpperCase();
  const airlinerType = t && TYPE_CLASSES.some(([c, re]) => c !== 'ga' && c !== 'biz' && c !== 'heli' && re.test(t));
  if (airlinerType) return BIZ_OPERATORS.has((p.flight || '').slice(0, 3));   // e.g. a 737 flying under its registration
  if (!/^[A-Z]{3}\d/.test(p.flight)) return true;
  return BIZ_OPERATORS.has(p.flight.slice(0, 3));
}

function onMap(p) {                       // is it inside the visible map (not off an edge, not under the sky-view disc)?
  if (!W) return true;
  const [x, y] = proj(p.lat, p.lon), m = 14 * unit;
  if (x < m || y < m || x > W - m || y > H - m) return false;
  return !(S.dome && Math.hypot(x - S.dome.cx, y - S.dome.cy) < S.dome.r);
}

/* The Bay Area is crowded with airports whose approaches overlap SFO's, so "descending toward SFO" is not enough
   to call something an SFO arrival. For each nearby airport we score how well a plane looks like it is landing
   there (pointed at it, on roughly a 3-degree path) or just left it (moving away, at a normal climb-out profile).
   Lower is better; Infinity means it doesn't fit at all. */
const APTS = [
  { c: 'SFO', lat: 37.6188, lon: -122.3754, elev: 13, big: 1 },  { c: 'OAK', lat: 37.7213, lon: -122.2208, elev: 9, big: 1 },
  { c: 'SJC', lat: 37.3626, lon: -121.9291, elev: 62, big: 1 },   { c: 'SQL', lat: 37.5119, lon: -122.2495, elev: 5 },
  { c: 'HWD', lat: 37.6592, lon: -122.1217, elev: 52 },   { c: 'PAO', lat: 37.4611, lon: -122.1150, elev: 4 },
  { c: 'NUQ', lat: 37.4161, lon: -122.0491, elev: 33 },   { c: 'HAF', lat: 37.5134, lon: -122.5012, elev: 66 },
  { c: 'LVK', lat: 37.6934, lon: -121.8204, elev: 400 },  { c: 'RHV', lat: 37.3329, lon: -121.8190, elev: 133 },
  { c: 'CCR', lat: 37.9897, lon: -122.0569, elev: 26 },
];
const APT_BY = Object.fromEntries(APTS.map(a => [a.c, a]));
function pathFit(p, ap, mode) {
  if (p.alt == null || p.track == null) return Infinity;
  const d = dist(p.lat, p.lon, ap.lat, ap.lon);
  if (d > 45) return Infinity;
  const agl = Math.max(0, p.alt - ap.elev), vs = p.rate || 0;
  let dt, expect;
  if (mode === 'arr') {
    if (!(vs < -200 || agl < 1500)) return Infinity;                       // descending, or already low
    dt = angDiff(p.track, brg(p.lat, p.lon, ap.lat, ap.lon));             // pointed at the airport
    if (dt > (d < 6 ? 45 : d < 15 ? 60 : 75)) return Infinity;
    expect = 318 * d;                                                     // ft above the field on a 3-degree path
  } else {
    if (!(vs > 300 || (agl < 1500 && (p.gs ?? 0) > 100))) return Infinity; // climbing, or just got airborne
    dt = angDiff(p.track, brg(ap.lat, ap.lon, p.lat, p.lon));             // moving away from the airport
    if (dt > (d < 6 ? 90 : 70)) return Infinity;
    expect = 500 * d;                                                     // typical climb-out gradient
  }
  // airliners land at the commercial airports; Hayward, San Carlos & co. only win if the plane really is pointed at them
  return dt / 60 + Math.abs(Math.log((agl + 800) / (expect + 800))) + (ap.big || p.cls === 'biz' ? 0 : 0.45);
}
function airportGuess(p) {
  const out = {};
  for (const mode of ['arr', 'dep']) {
    const fits = {}; let best = null;
    for (const ap of APTS) {
      const s = pathFit(p, ap, mode); fits[ap.c] = s;
      if (s < Infinity && (!best || s < best.s)) best = { c: ap.c, s };
    }
    out[mode] = { best, fits, sfo: fits.SFO };
  }
  return out;
}
function routeClash(g, rt) {      // the filed route names a nearby airport the plane is clearly NOT using
  const dest = rt.destination?.iata, orig = rt.origin?.iata;
  const clash = (m, code) => code && APT_BY[code] && m.best && m.best.s < 1.0 && m.best.c !== code && (m.fits[code] ?? Infinity) > m.best.s + 0.25;
  return clash(g.arr, dest) || clash(g.dep, orig);
}

function classify() {
  const c = S.cfg;
  for (const p of S.planes.values()) {
    p.d = dist(c.lat, c.lon, p.lat, p.lon);
    p.b = brg(c.lat, c.lon, p.lat, p.lon);
    p.dSfo = dist(p.lat, p.lon, c.sfo.lat, c.sfo.lon);
    p.toSfo = brg(p.lat, p.lon, c.sfo.lat, c.sfo.lon);
    p.cls = acClass(p); p.ga = isGA(p);

    // is it somewhere you could actually look up and see it?
    const altFt = p.altGeom ?? p.alt;
    p.elev = p.slant = null;
    if (altFt != null && !p.ground) {
      const up = (altFt - c.lab_elev_ft) / 6076.12;
      p.elev = deg(Math.atan2(up, p.d)); p.slant = Math.hypot(up, p.d);
    }
    // The ceiling uses the barometric altitude - the number shown on the card and in the table. GPS altitude can read
    // 1,000+ ft higher on a warm or high-pressure day, which made a plane shown at 5,850 ft fail a 7,000 ft ceiling.
    p.visible = p.elev != null && p.elev >= c.min_elev && p.slant <= c.vis_nm && (p.alt ?? altFt) <= c.vis_max_alt;

    // Landing / departing SFO - or one of its neighbours (OAK, SJC, SQL, HWD, PAO...).
    //  1. If Flightradar24's feed knows this exact flight, its origin/destination decides: no guessing.
    //  2. Otherwise work out which airport the plane is really using from its position, direction and height,
    //     and trust a filed route only where it agrees (callsigns get reused, and the approaches here overlap).
    // Labels don't depend on climbing/descending at this instant: approaches and climb-outs have level-off segments.
    let rt = S.routes[rkey(p)]?.route || null;
    const ok = p.alt != null && !p.ground && p.track != null;
    const fr = frOf(p), frRoute = fr && fr.orig && fr.dest && !fr.gnd ? fr : null;
    const g = ok && p.cls !== 'ga' && p.cls !== 'heli' ? airportGuess(p) : null;   // bizjets included: they land at SFO too
    p.sfo = null; p.otherApt = null; p.routeConflict = false; p.frRoute = !!frRoute;
    if (frRoute && p.alt != null && !p.ground) {
      if (frRoute.dest === 'SFO' && p.alt < 25000 && p.dSfo < 80) p.sfo = 'arr';
      else if (frRoute.orig === 'SFO' && p.alt < 25000 && p.dSfo < 80) p.sfo = 'dep';
      else if (APT_BY[frRoute.dest] && p.alt < 12000 && p.dSfo < 60) p.otherApt = { mode: 'arr', code: frRoute.dest };
      else if (APT_BY[frRoute.orig] && p.alt < 12000 && p.dSfo < 60) p.otherApt = { mode: 'dep', code: frRoute.orig };
      const same = rt && rt.origin?.iata === frRoute.orig && rt.destination?.iata === frRoute.dest;
      rt = same ? rt : { origin: { iata: frRoute.orig }, destination: { iata: frRoute.dest }, airline: rt?.airline ?? null, source: 'Flightradar24' };
    } else if (g) {
      if (rt && routeClash(g, rt)) { rt = null; p.routeConflict = true; }
      const dest = rt?.destination?.iata, orig = rt?.origin?.iata, alt = p.alt, fast = (p.gs ?? 0) >= 120;
      const ba = g.arr.best, bd = g.dep.best;
      const rivalArr = ba && ba.c !== 'SFO' && ba.s < 1.3 && ba.s + 0.2 < g.arr.sfo;
      const rivalDep = bd && bd.c !== 'SFO' && bd.s < 1.3 && bd.s + 0.2 < g.dep.sfo;
      if (rt && (dest || orig)) {                                   // a route that survived the checks: use it
        if (dest === 'SFO' && alt < 20000 && p.dSfo < 60) p.sfo = 'arr';
        else if (orig === 'SFO' && alt < 20000 && p.dSfo < 60) p.sfo = 'dep';
      } else if (fast) {                                            // no route: geometry alone, and it must beat the neighbours
        if (g.arr.sfo < 1.3 && !rivalArr && g.arr.sfo <= (ba?.s ?? Infinity) + 0.15) p.sfo = 'arr';
        else if (g.dep.sfo < 1.3 && !rivalDep && g.dep.sfo <= (bd?.s ?? Infinity) + 0.15) p.sfo = 'dep';
        // a label earned by geometry is kept for a while, so a level-off doesn't make it flicker
        if (p.sfo) p.sfoMemo = { mode: p.sfo, t: Date.now() };
        else if (p.sfoMemo && Date.now() - p.sfoMemo.t < 90000 && p.dSfo < 40 && alt < 15000 && !rivalArr && !rivalDep) p.sfo = p.sfoMemo.mode;
      }
      if (!p.sfo && fast) {                                         // clearly using a neighbouring airport? say so
        if (ba && ba.c !== 'SFO' && ba.s < 1.0) p.otherApt = { mode: 'arr', code: ba.c };
        else if (bd && bd.c !== 'SFO' && bd.s < 1.0) p.otherApt = { mode: 'dep', code: bd.c };
      }
    }
    p.route = rt;
    // which of SFO / OAK / SJC (if any) this plane is landing at or departing from: drives the colours
    p.phase = p.sfo ? { mode: p.sfo, apt: 'SFO' } : (p.otherApt && MAJOR.includes(p.otherApt.code)) ? { mode: p.otherApt.mode, apt: p.otherApt.code } : null;
    p.tier = p.ground ? 'green' : p.visible ? 'amber' : p.phase?.mode === 'arr' ? 'cyan' : p.phase?.mode === 'dep' ? 'coral' : 'slate';
  }
  const all = [...S.planes.values()];
  for (const p of all) p.onMap = onMap(p);
  S.vis = all.filter(p => p.visible && p.onMap).sort((a, b) => a.slant - b.slant);
  S.arr = all.filter(p => p.phase?.mode === 'arr' && p.onMap).sort((a, b) => a.d - b.d);
  S.dep = all.filter(p => p.phase?.mode === 'dep' && p.onMap).sort((a, b) => a.d - b.d);
  const near = all.filter(p => p.onMap && p.d <= c.radius_nm && !p.ground && (p.alt ?? 1e9) <= c.vis_max_alt).sort((a, b) => a.d - b.d);
  const close = all.filter(p => p.onMap && !p.ground && p.d <= 2);                    // right next to us
  S.cands = [...new Set([...close, ...S.vis, ...S.arr, ...S.dep, ...near])].filter(p => !p.ga);   // no GA in the spotlight

  // The receiver's range outline: the farthest aircraft heard in each 10-degree slice, remembered between visits
  const cap = c.overview_nm * 1.5;
  S.farthest = 0;
  for (const p of all) {
    if (p.d > S.farthest && p.d <= cap) S.farthest = p.d;
    if ((p.seenPos ?? 99) < 5 && p.gs != null && p.d <= cap) { const bi = Math.floor(p.b / 10) % 36; if (p.d > S.range[bi]) { S.range[bi] = p.d; S.rangeDirty = true; } }
  }

  // Look up routes/photos only for planes that could be featured (never GA), a few at a time, best first.
  // Routes also go to anything airline-like on the map, so SFO arrivals/departures get recognised.
  const routeWant = [...new Set([...S.cands, ...all.filter(p => p.onMap && !p.ga && !p.ground).sort((a, b) => a.d - b.d)])];
  let n = 0;
  for (const p of routeWant) {
    if (n >= 3) break;
    if (routeDue(p)) { ensureRoute(p); n++; }
  }
  S.cands.slice(0, 10).forEach(p => ensureInfo(p.hex));
}

/* Flightradar24's view of this exact aircraft (matched on ICAO24 hex), if the feed is on and fresh. */
function frOf(p) {
  if (!S.cfg?.fr24_feed || Date.now() - S.frAt > 120000) return null;
  const e = S.fr[p.hex]; if (!e) return null;
  return { callsign: e[0], orig: e[1], dest: e[2], reg: e[3], type: e[4], num: e[5], gnd: !!e[6], op: e[7] || '', mk: e[8] || '', fn: e[9] || '', via: e[10] || '' };
}
async function pollFr24() {
  try {
    const d = await (await fetch('api/fr24')).json();
    S.frStatus = d.status;
    if (d.status === 'ok' && d.age != null) { S.fr = d.flights; S.frAt = Date.now() - d.age * 1000; }
  } catch { S.frStatus = 'error'; }
}

// The remembered range outline is measured from the receiver, so it is stored per location.
const rangeKey = () => 'overhead-range-v2:' + S.cfg.lat.toFixed(2) + ',' + S.cfg.lon.toFixed(2);

const nameOf = p => p.flight || frOf(p)?.callsign || p.r || frOf(p)?.reg || S.info[p.hex]?.d?.aircraft?.registration || p.hex.toUpperCase();
const typeOf = p => frOf(p)?.type || S.info[p.hex]?.d?.aircraft?.icao_type || p.t || '';   // FR24 first: adsbdb is sometimes wrong
const routeStr = p => p.route?.origin?.iata && p.route?.destination?.iata ? `${p.route.origin.iata}\u2192${p.route.destination.iata}` : '';

/* =====================================================================
   what to feature
   ===================================================================== */
function pick(force) {
  const list = S.cands; if (!list.length) return null;
  const cur = list.find(p => p.hex === S.cur);
  const fresh = cur && Date.now() - S.curSince < S.cfg.dwell_sec * 1000;
  // Anything within 2 nm of us always gets the spotlight, interrupting whatever is on screen.
  // Several at once take turns; a single one stays up while it's close.
  const close = list.filter(p => p.d <= 2 && !p.ground);
  if (close.length) {
    if (cur && close.includes(cur) && !force && (fresh || close.length === 1)) return cur;
    const pool = close.length > 1 ? close.filter(p => p !== cur) : close;
    return pool.sort((a, b) => ((S.shown[a.hex] || 0) - (S.shown[b.hex] || 0)) || (a.d - b.d))[0];
  }
  if (cur && !force && fresh) return cur;
  const score = p => {
    let s = S.shown[p.hex] || 0;                      // least recently featured goes first
    s -= p.visible ? 90000 : p.phase ? 35000 : 0;     // in-sight planes get the spotlight most often, then airport traffic
    const i = S.info[p.hex]?.d; if (i && !i.photo) s += 40000;
    if (p.hex === S.cur) s += 1e13;
    return s + p.d * 100;
  };
  return list.slice().sort((a, b) => score(a) - score(b))[0];
}

/* =====================================================================
   panel rendering
   ===================================================================== */
function chipFor(p) {
  const parts = [];
  if (p.visible) parts.push('In sight');
  if (p.phase) parts.push((p.phase.mode === 'arr' ? 'Landing at ' : 'Departing ') + p.phase.apt);
  else if (p.otherApt) parts.push((p.otherApt.mode === 'arr' ? 'Landing at ' : 'Departing ') + p.otherApt.code);
  if (!parts.length) parts.push('Nearby');
  return [parts.join(' \u00B7 '), p.tier];
}
function metasFor(p) {
  const m = [], vs = p.rate || 0;
  if (p.visible) m.push(['amber', `Look ${word(p.b)}, ${Math.round(p.elev)}\u00B0 up`, `${p.slant.toFixed(1)} nm away`]);
  const ph = p.phase || (p.otherApt ? { mode: p.otherApt.mode, apt: p.otherApt.code } : null);
  if (ph) {
    const ap = APT_BY[ph.apt], dA = ap ? dist(p.lat, p.lon, ap.lat, ap.lon) : null, colour = p.phase ? (ph.mode === 'arr' ? 'cyan' : 'coral') : 'slate';
    if (ph.mode === 'arr') {
      const eta = Math.max(1, Math.round((dA ?? 0) / Math.max(p.gs || 150, 60) * 60));
      m.push([colour, dA != null && dA < 2 ? `On final approach to ${ph.apt}` : `Landing at ${ph.apt} in about ${eta} min`, vs < -150 ? `descending ${fmt(-vs)} ft/min` : 'level']);
    } else m.push([colour, `Climbing out of ${ph.apt}`, vs > 150 ? `${fmt(vs)} ft/min` : 'level']);
  }
  if (!m.length) m.push(['slate', `${p.d.toFixed(0)} nm ${abbr(p.b)} of ${S.cfg.lab_name}`, '']);
  return m;
}
function paintLive(p) {
  const arrow = p.rate > 300 ? ' \u25B2' : p.rate < -300 ? ' \u25BC' : '';
  $('alt').innerHTML = (p.alt != null ? fmt(p.alt) : '-') + '<small>ft' + arrow + '</small>';
  $('spd').innerHTML = (p.gs != null ? fmt(p.gs) : '-') + '<small>kt</small>';
  $('dst').innerHTML = p.d.toFixed(p.d < 10 ? 1 : 0) + '<small>nm ' + abbr(p.b) + '</small>';
  const [txt, tier] = chipFor(p), chip = $('chip');
  chip.textContent = txt; chip.className = 'chip ' + tier;
  $('metas').innerHTML = metasFor(p).map(([t, a, b]) => `<div class="meta ${t}"><i></i><div>${esc(a)}${b ? ` <span>${esc(b)}</span>` : ''}</div></div>`).join('');
}
// Readable names for type codes, used when adsbdb's description disagrees with FR24 (FR24 wins) or is missing.
const TYPE_NAMES = { A318: 'Airbus A318', A319: 'Airbus A319', A320: 'Airbus A320', A321: 'Airbus A321', A19N: 'Airbus A319neo',
  A20N: 'Airbus A320neo', A21N: 'Airbus A321neo', BCS1: 'Airbus A220-100', BCS3: 'Airbus A220-300', A332: 'Airbus A330-200',
  A333: 'Airbus A330-300', A339: 'Airbus A330-900neo', A359: 'Airbus A350-900', A35K: 'Airbus A350-1000', A388: 'Airbus A380',
  B737: 'Boeing 737-700', B738: 'Boeing 737-800', B739: 'Boeing 737-900', B37M: 'Boeing 737 MAX 7', B38M: 'Boeing 737 MAX 8',
  B39M: 'Boeing 737 MAX 9', B3XM: 'Boeing 737 MAX 10', B752: 'Boeing 757-200', B753: 'Boeing 757-300', B762: 'Boeing 767-200',
  B763: 'Boeing 767-300', B764: 'Boeing 767-400', B772: 'Boeing 777-200', B77L: 'Boeing 777-200LR', B77W: 'Boeing 777-300ER',
  B778: 'Boeing 777-8', B779: 'Boeing 777-9', B788: 'Boeing 787-8', B789: 'Boeing 787-9', B78X: 'Boeing 787-10',
  B744: 'Boeing 747-400', B748: 'Boeing 747-8', MD11: 'McDonnell Douglas MD-11', E170: 'Embraer 170', E75L: 'Embraer 175',
  E75S: 'Embraer 175', E190: 'Embraer 190', E195: 'Embraer 195', E290: 'Embraer 190-E2', E295: 'Embraer 195-E2',
  CRJ2: 'Bombardier CRJ200', CRJ7: 'Bombardier CRJ700', CRJ9: 'Bombardier CRJ900', DH8D: 'De Havilland Dash 8-400',
  AT76: 'ATR 72-600', CL35: 'Bombardier Challenger 350', GLF6: 'Gulfstream G650', C172: 'Cessna 172', SR22: 'Cirrus SR22' };
function typeLabel(p) {
  const ac = S.info[p.hex]?.d?.aircraft, fr = frOf(p)?.type;
  const adsbdbName = [ac?.manufacturer, ac?.type].filter(Boolean).join(' ');
  if (adsbdbName && (!fr || (ac.icao_type || '').toUpperCase() === fr.toUpperCase())) return adsbdbName;
  return TYPE_NAMES[fr] || fr || TYPE_NAMES[p.t] || p.t || adsbdbName || '';
}

function paintIdentity(p) {
  const ac = S.info[p.hex]?.d?.aircraft, rt = p.route;
  $('callsign').textContent = nameOf(p);
  const type = typeLabel(p);
  const fr = frOf(p);
  const who = rt?.airline || fr?.op || S.routes[rkey(p)]?.operator || ac?.owner || '';   // operator: route data, FR24, or just the callsign
  const reg = frOf(p)?.reg || ac?.registration || p.r || '';
  const bits = [];                                              // line 1: operator / airline. line 2: aircraft type and tail
  if (type) bits.push(esc(type));
  if (reg && reg !== nameOf(p)) bits.push(esc(reg));
  // Line 1 is who the flight is sold as ("United Express \u00B7 UA 5460"), line 2 who flies it ("Operated by SkyWest"),
  // line 3 the aircraft. Without Flightradar24's flight number all we know is the operator, from the callsign.
  const line1 = fr?.mk ? fr.mk + (fr.fn ? ' \u00B7 ' + fr.fn : '') : who;
  const line2 = fr?.via ? 'Operated by ' + fr.via : '';
  $('sub').innerHTML = (line1 ? `<div class="op">${esc(line1)}</div>` : '') + (line2 ? `<div class="via">${esc(line2)}</div>` : '') +
    (bits.length ? `<div class="ac">${bits.join(' &nbsp;&middot;&nbsp; ')}</div>` : '');
  const r = $('route');
  if (rt?.origin?.iata && rt?.destination?.iata) {
    const both = rt.origin.city && rt.destination.city;            // city names only when both ends have one, so the two sides match
    $('oCode').textContent = rt.origin.iata; $('oCity').textContent = both ? rt.origin.city : '';
    $('dCode').textContent = rt.destination.iata; $('dCity').textContent = both ? rt.destination.city : '';
    $('oCity').style.display = $('dCity').style.display = both ? '' : 'none';
    r.style.display = 'flex';
  } else r.style.display = 'none';
}
function paintPhoto(p) {
  return new Promise(res => {
    const ph = $('photo'), ph0 = S.info[p.hex]?.d?.photo;
    if (!ph0) { ph.classList.add('empty'); ph.style.setProperty('--ar', 1.5); $('credit').textContent = ''; return res(); }
    const img = new Image();
    img.onload = () => { S.photoHex = p.hex; $('fg').src = ph0.src; ph.classList.remove('empty');
      ph.style.setProperty('--ar', Math.min(1.9, Math.max(1.35, img.naturalWidth / img.naturalHeight)).toFixed(3));
      $('credit').textContent = 'Photo' + (ph0.credit ? ': ' + ph0.credit : '') + (ph0.source ? ' / ' + ph0.source : ''); res(); };
    img.onerror = () => { ph.classList.add('empty'); $('credit').textContent = ''; res(); };
    img.src = ph0.src;
  });
}
async function switchTo(p) {
  S.cur = p.hex; S.curSince = Date.now(); S.shown[p.hex] = Date.now(); S.changing = true;
  const stage = $('stage'); stage.classList.add('out');
  const wait = ms => new Promise(r => setTimeout(r, ms));
  await Promise.race([Promise.all([ensureInfo(p.hex, true), ensureRoute(p, true)]), wait(2500)]);
  await wait(350);
  const q = S.planes.get(p.hex) || p;
  classify();                       // routes may have just arrived
  const q2 = S.planes.get(p.hex) || q;
  paintIdentity(q2); paintLive(q2); await paintPhoto(q2);
  stage.classList.remove('out'); S.changing = false;
}

const hintStr = p => p.phase ? (p.phase.mode === 'arr' ? '\u2192' + p.phase.apt : p.phase.apt + '\u2192') : p.otherApt ? (p.otherApt.mode === 'arr' ? '\u2192' + p.otherApt.code : p.otherApt.code + '\u2192') : '';
const tierRank = { amber: 0, cyan: 1, coral: 1, slate: 2, green: 3 };
function rowHTML(p) {
  const arrow = p.rate > 300 ? '<i class="up">\u25B2</i>' : p.rate < -300 ? '<i class="dn">\u25BC</i>' : '';
  const alt = p.ground ? 'GND' : p.alt != null ? fmt(p.alt) : '-';
  return `<div class="row t-${p.tier}${p.hex === S.cur ? ' cur' : ''}${p.ga ? ' ga' : ''}">` +
    `<span class="cs">${esc(nameOf(p))}</span><span class="ty">${esc(typeOf(p))}</span><span class="rt">${esc(routeStr(p) || hintStr(p))}</span>` +
    `<span class="n">${alt}${arrow}</span><span class="n">${p.gs != null ? Math.round(p.gs) : '-'}</span><span class="n">${p.d.toFixed(p.d < 10 ? 1 : 0)}</span></div>`;
}
function paintTable() {
  // everything on the map: what you can see first, then SFO traffic, then the rest by distance
  const rows = [...S.planes.values()].filter(p => p.onMap).sort((a, b) => (tierRank[a.tier] - tierRank[b.tier]) || (a.d - b.d));
  const box = $('tbl'), body = $('tbody');
  const html = n => rows.slice(0, n).map(rowHTML).join('') + (rows.length > n ? `<div class="more">+${rows.length - n} more on the map</div>` : '');
  let k = Math.min(rows.length, 60);
  body.innerHTML = rows.length ? html(k) : '<div class="empty">Nothing on the map right now</div>';
  while (k > 1 && box.scrollHeight > box.clientHeight + 1) body.innerHTML = html(--k);   // drop rows until it fits
  $('tblCount').textContent = `${S.vis.length} in sight \u00B7 ${S.arr.length} landing \u00B7 ${S.dep.length} departing`;
}

/* =====================================================================
   receiver-range overview (everything the receiver hears, on a small map)
   ===================================================================== */
let ov, ovctx, OW = 0, ovScale = 256, ovCx = 0, ovCy = 0;
const pt2 = [0, 0];
function projOv(lat, lon) {
  const s = Math.sin(rad(lat));
  pt2[0] = (lon + 180) / 360 * ovScale - ovCx + OW / 2;
  pt2[1] = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * ovScale - ovCy + OW / 2;
  return pt2;
}
function destPt(lat, lon, brgDeg, nm) {
  const d = nm / R_NM, b = rad(brgDeg), p1 = rad(lat), l1 = rad(lon);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [deg(p2), deg(l2)];
}
function initOverview() {
  ov = L.map('ovmap', { zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
    boxZoom: false, keyboard: false, touchZoom: false, zoomSnap: 0, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false });
  L.tileLayer(S.cfg.tile_url || 'tiles/' + S.cfg.basemap + '/{z}/{x}/{y}.png', { tileSize: S.cfg.tile_size, zoomOffset: S.cfg.tile_offset, maxZoom: 19, className: 'basemap-' + S.cfg.basemap }).addTo(ov);
  ovctx = $('ovfx').getContext('2d');
  fitOverview();
}
function fitOverview() {
  if (!ov) return;
  const c = S.cfg, R = c.overview_nm;
  ov.invalidateSize(false);
  const cv = $('ovfx'), r = window.devicePixelRatio || 1;
  OW = cv.clientWidth; cv.width = OW * r; cv.height = OW * r;
  // zoom so that R nm is 95% of the disc's radius, centred exactly on the receiver (fitBounds would centre on the
  // Mercator middle of the box, which is slightly north of it, so the rings sat off-centre)
  const z = Math.log2((OW / 2 * 0.95) * 156543.03392 * Math.cos(rad(c.lat)) / (1852 * R));
  ov.setView([c.lat, c.lon], z, { animate: false });
  const ctr = ov.getCenter(); ovScale = 256 * Math.pow(2, ov.getZoom());
  ovCx = (ctr.lng + 180) / 360 * ovScale;
  const s = Math.sin(rad(ctr.lat)); ovCy = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * ovScale;
}
function drawOverview() {
  if (!ovctx || !OW || !S.cfg) return;
  const c = S.cfg, g = ovctx, r = window.devicePixelRatio || 1;
  g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, OW, OW);
  g.fillStyle = 'rgba(20,19,17,.4)'; g.fillRect(0, 0, OW, OW);                       // sit the map back
  const [cx, cy] = projOv(c.lat, c.lon).slice();
  const pxNm = 1852 / (156543.03392 * Math.cos(rad(c.lat)) / Math.pow(2, ov.getZoom()));

  g.font = `600 ${10 * unit}px 'Source Sans 3'`; g.textAlign = 'center';
  for (let nm = 50; nm <= c.overview_nm; nm += 50) {                                 // range rings
    g.setLineDash([3 * unit, 4 * unit]); g.lineWidth = 1; g.strokeStyle = 'rgba(244,244,244,.28)';
    g.beginPath(); g.arc(cx, cy, nm * pxNm, 0, 7); g.stroke(); g.setLineDash([]);
    if (nm * pxNm < OW / 2 - 6 * unit) { g.fillStyle = 'rgba(244,244,244,.65)'; g.fillText(nm, cx, cy - nm * pxNm - 2 * unit); }
  }
  const pts = [];                                                                    // range outline: farthest plane heard, per bearing
  for (let i = 0; i < 36; i++) if (S.range[i] > 5) { const [la, lo] = destPt(c.lat, c.lon, i * 10 + 5, S.range[i]); pts.push(projOv(la, lo).slice()); }
  if (pts.length > 2) {
    g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();
    g.fillStyle = 'rgba(244,244,244,.07)'; g.fill(); g.lineWidth = 1.2; g.strokeStyle = 'rgba(244,244,244,.5)'; g.stroke();
  }
  const list = [...S.planes.values()].sort((a, b) => (b.tier === 'slate') - (a.tier === 'slate'));   // interesting ones on top
  for (const p of list) {
    const [x, y] = projOv(p.lat, p.lon);
    if (Math.hypot(x - OW / 2, y - OW / 2) > OW / 2 - 1) continue;
    const tier = p.tier !== 'slate' && p.tier !== 'green';
    if (p.tier === 'green') { g.fillStyle = COLOR.green; g.beginPath(); g.arc(x, y, 1.8 * unit, 0, 7); g.fill(); continue; }
    g.fillStyle = tier ? COLOR[p.tier] : 'rgba(244,244,244,.85)';
    g.beginPath(); g.arc(x, y, (tier ? 2.9 : 1.8) * unit, 0, 7); g.fill();
  }
  const b = map.getBounds(), q = [b.getNorthWest(), b.getNorthEast(), b.getSouthEast(), b.getSouthWest()].map(l => projOv(l.lat, l.lng).slice());
  g.beginPath(); q.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath();   // where the big map is looking
  g.fillStyle = 'rgba(255,255,255,.08)'; g.fill(); g.lineWidth = 1.4; g.strokeStyle = 'rgba(255,255,255,.9)'; g.stroke();
  g.fillStyle = '#fff'; g.beginPath(); g.arc(cx, cy, 2.8 * unit, 0, 7); g.fill();
  g.fillStyle = 'rgba(244,244,244,.8)'; g.font = `700 ${11 * unit}px 'Source Sans 3'`; g.fillText('N', OW / 2, 13 * unit);
}

/* =====================================================================
   frame loop
   ===================================================================== */
/* =====================================================================
   aircraft icons: one top-down silhouette per type family
   ===================================================================== */
const CLASS_PX = { ga: 18, heli: 21, biz: 22, prop: 25, small: 25, medium: 32, midlarge: 35, large: 38, quad: 40, super: 46 };
const ICONS = (() => {
  const CX = 32;
  const poly = pts => { const q = new Path2D(); pts.forEach(([x, y], i) => (i ? q.lineTo(x, y) : q.moveTo(x, y))); q.closePath(); return q; };
  const capsule = (x, y0, len, r) => { const q = new Path2D(); q.moveTo(x - r, y0 + r); q.arc(x, y0 + r, r, Math.PI, 0); q.lineTo(x + r, y0 + len - r); q.arc(x, y0 + len - r, r, 0, Math.PI); q.closePath(); return q; };

  function jet(o) {
    const top = 32 - o.L / 2, bot = 32 + o.L / 2, f = o.fw, parts = [];
    const body = new Path2D();                                              // fuselage
    body.moveTo(CX, top);
    body.quadraticCurveTo(CX + f, top, CX + f, top + o.nose);
    body.lineTo(CX + f, bot - o.tail);
    body.quadraticCurveTo(CX + f * 0.8, bot - o.tail * 0.3, CX + o.tw, bot);
    body.lineTo(CX - o.tw, bot);
    body.quadraticCurveTo(CX - f * 0.8, bot - o.tail * 0.3, CX - f, bot - o.tail);
    body.lineTo(CX - f, top + o.nose);
    body.quadraticCurveTo(CX - f, top, CX, top);
    body.closePath(); parts.push(body);
    const wy = top + o.wy, ty = bot - o.ty;
    for (const s of [1, -1]) {
      parts.push(poly([[CX + s * f * 0.5, wy], [CX + s * o.hs, wy + o.sweep], [CX + s * o.hs, wy + o.sweep + o.c1], [CX + s * f * 0.5, wy + o.c0]]));          // wing
      parts.push(poly([[CX + s * f * 0.4, ty], [CX + s * o.ts, ty + o.ss], [CX + s * o.ts, ty + o.ss + o.tc1], [CX + s * f * 0.4, ty + o.tc0]]));            // tailplane
    }
    for (const e of o.eng || []) for (const s of [1, -1]) {                  // engines slung ahead of the wing
      const le = wy + ((e.x - f * 0.5) / (o.hs - f * 0.5)) * o.sweep, y0 = le - e.lead;
      parts.push(capsule(CX + s * e.x, y0, e.len, e.r));
      if (o.discs) parts.push(poly([[CX + s * e.x - e.r * 2.1, y0 - 0.2], [CX + s * e.x + e.r * 2.1, y0 - 0.2], [CX + s * e.x + e.r * 2.1, y0 + 1.5], [CX + s * e.x - e.r * 2.1, y0 + 1.5]]));   // prop disc
    }
    for (const e of o.rear || []) for (const s of [1, -1]) parts.push(capsule(CX + s * e.x, bot - e.y0, e.len, e.r));   // rear-mounted engines
    if (o.hump) { const h = new Path2D(); h.ellipse(CX, top + o.hump.y, f + o.hump.w, o.hump.h, 0, 0, Math.PI * 2); parts.push(h); }
    if (o.spinner) parts.push(poly([[CX - 6.5, top - 0.2], [CX + 6.5, top - 0.2], [CX + 6.5, top + 1.5], [CX - 6.5, top + 1.5]]));
    return parts;
  }
  function heli() {
    const CXY = 27, body = new Path2D(); body.ellipse(CX, 29, 5.2, 10.5, 0, 0, Math.PI * 2);
    const bar = deg => { const q = new Path2D(); q.addPath(poly([[-27, -1.5], [27, -1.5], [27, 1.5], [-27, 1.5]]), new DOMMatrix().translate(CX, CXY).rotate(deg)); return q; };
    return [body, poly([[CX - 1.4, 36], [CX + 1.4, 36], [CX + 1, 59], [CX - 1, 59]]), poly([[CX - 4.5, 55], [CX + 4.5, 55], [CX + 4.5, 58.5], [CX - 4.5, 58.5]]), bar(28), bar(-62)];
  }
  return {
    ga:     jet({ L: 44, fw: 2.5, nose: 8, tail: 20, tw: .9, wy: 13, c0: 10, sweep: 0, c1: 8, hs: 30, ty: 9, tc0: 5, ss: 0, tc1: 4, ts: 11, spinner: true }),
    heli:   heli(),
    biz:    jet({ L: 56, fw: 1.8, nose: 10, tail: 14, tw: .6, wy: 25, c0: 8.5, sweep: 8, c1: 2.8, hs: 17.5, ty: 10, tc0: 5.5, ss: 3, tc1: 2, ts: 7.5, rear: [{ x: 4.5, r: 1.7, y0: 19, len: 12 }] }),
    prop:   jet({ L: 50, fw: 2.2, nose: 8, tail: 13, tw: .8, wy: 19, c0: 8, sweep: 1.5, c1: 6, hs: 24, ty: 11, tc0: 5.5, ss: 2, tc1: 3.5, ts: 10, discs: true, eng: [{ x: 8.5, r: 1.6, len: 15, lead: 8 }] }),
    small:  jet({ L: 56, fw: 2.1, nose: 8, tail: 15, tw: .7, wy: 23, c0: 8.5, sweep: 8, c1: 3, hs: 21, ty: 11, tc0: 5.5, ss: 3, tc1: 2.2, ts: 9, eng: [{ x: 7.6, r: 1.5, len: 6.5, lead: 3.5 }] }),
    medium: jet({ L: 58, fw: 2.7, nose: 9, tail: 15, tw: .9, wy: 22, c0: 11, sweep: 11, c1: 4, hs: 27, ty: 12, tc0: 6.5, ss: 3.5, tc1: 2.5, ts: 10.5, eng: [{ x: 10.5, r: 2.2, len: 9, lead: 4.5 }] }),
    midlarge: jet({ L: 60, fw: 3.2, nose: 10, tail: 16, tw: 1, wy: 22, c0: 12.5, sweep: 12, c1: 4.3, hs: 28.5, ty: 13, tc0: 7.5, ss: 4, tc1: 2.8, ts: 11.5, eng: [{ x: 11, r: 2.8, len: 11, lead: 5.5 }] }),
    large:  jet({ L: 62, fw: 3.7, nose: 11, tail: 17, tw: 1.2, wy: 22, c0: 15, sweep: 13, c1: 5, hs: 30, ty: 14, tc0: 8.5, ss: 4.5, tc1: 3, ts: 12.5, eng: [{ x: 11.5, r: 3.4, len: 13, lead: 6.5 }] }),
    quad:   jet({ L: 62, fw: 3.5, nose: 11, tail: 17, tw: 1.1, wy: 22, c0: 15, sweep: 14, c1: 4.8, hs: 30, ty: 14, tc0: 8.5, ss: 4.5, tc1: 3, ts: 12, hump: { y: 17, w: 0.7, h: 8.5 },
                   eng: [{ x: 9.5, r: 2.5, len: 11, lead: 6 }, { x: 18.5, r: 2.5, len: 11, lead: 6 }] }),
    super:  jet({ L: 60, fw: 4.7, nose: 12, tail: 16, tw: 1.6, wy: 19, c0: 19, sweep: 12.5, c1: 6.5, hs: 31.5, ty: 15, tc0: 9.5, ss: 5, tc1: 3.5, ts: 13.5,
                   eng: [{ x: 10.5, r: 3.1, len: 12.5, lead: 6.5 }, { x: 20.5, r: 3.1, len: 12.5, lead: 6.5 }] }),
  };
})();

function drawIcon(g, cls, color, edge, glow) {      // g is already translated/rotated/scaled into the 64-unit box
  const parts = ICONS[cls] || ICONS.medium;
  g.lineJoin = 'round'; g.lineWidth = 3.2; g.strokeStyle = edge;
  for (const q of parts) g.stroke(q);                // dark edge first...
  g.fillStyle = color; g.shadowColor = color; g.shadowBlur = glow;
  for (const q of parts) g.fill(q);                  // ...then the fill covers the inner edges
  g.shadowBlur = 0;
}


function drawRings() {
  const c = S.cfg, [x, y] = proj(c.lat, c.lon);
  const mpp = 156543.03392 * Math.cos(rad(c.lat)) / Math.pow(2, map.getZoom());   // meters per pixel
  const pxNm = 1852 / mpp;
  ctx.textAlign = 'center'; ctx.font = `600 ${12 * unit}px 'Source Sans 3'`; ctx.lineJoin = 'round';
  for (const nm of [2, 5, 10]) {
    const r = nm * pxNm;
    ctx.setLineDash([6 * unit, 9 * unit]); ctx.strokeStyle = 'rgba(210,194,149,.34)'; ctx.lineWidth = 1.2 * unit;   // Stanford sandstone
    ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.stroke();
    ctx.setLineDash([]); ctx.lineWidth = 3 * unit; ctx.strokeStyle = 'rgba(0,0,0,.6)';
    ctx.strokeText(nm + ' nm', x, y - r - 5 * unit);
    ctx.fillStyle = 'rgba(210,194,149,.75)'; ctx.fillText(nm + ' nm', x, y - r - 5 * unit);
  }
  ctx.setLineDash([]);
}
function drawMarkers(t) {
  const c = S.cfg;
  let [x, y] = proj(c.lat, c.lon);
  const pulse = (t % 2400) / 2400;
  ctx.strokeStyle = `rgba(255,255,255,${.5 * (1 - pulse)})`; ctx.lineWidth = 2 * unit;
  ctx.beginPath(); ctx.arc(x, y, (6 + 26 * pulse) * unit, 0, 7); ctx.stroke();
  ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x, y, 5 * unit, 0, 7); ctx.fill();
  ctx.font = `600 ${15 * unit}px 'Source Sans 3'`; ctx.textAlign = 'left'; ctx.fillStyle = '#fff';
  ctx.shadowColor = '#000'; ctx.shadowBlur = 6; ctx.fillText(c.lab_name, x + 11 * unit, y + 5 * unit);
  ctx.font = `600 ${10 * unit}px 'Source Sans 3'`;                // smaller fields: just a quiet dot and code
  for (const ap of APTS) {
    if (MAJOR.includes(ap.c)) continue;
    [x, y] = proj(ap.lat, ap.lon);
    if (x < 0 || y < 0 || x > W || y > H) continue;
    ctx.fillStyle = 'rgba(255,255,255,.5)'; ctx.beginPath(); ctx.arc(x, y, 2.4 * unit, 0, 7); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,.45)'; ctx.fillText(ap.c, x + 5 * unit, y + 3.5 * unit);
  }
  ctx.font = `600 ${15 * unit}px 'Source Sans 3'`;
  for (const k of MAJOR) {                                       // the airports
    [x, y] = proj(APT_BY[k].lat, APT_BY[k].lon);
    ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = 2 * unit;
    ctx.beginPath(); ctx.arc(x, y, 7 * unit, 0, 7); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.9)'; ctx.fillText(k, x + 12 * unit, y + 5 * unit);
  }
  ctx.shadowBlur = 0;
}
const JET_LABEL = new Set(['small', 'medium', 'midlarge', 'large', 'quad', 'super']);
function drawTrail(p, color, full) {
  const t = Date.now(), tr = p.trail, maxAge = full ? 240000 : 90000;
  if (tr.length < 1) return;
  const pts = []; for (const q of tr) if (t - q[2] <= maxAge) pts.push(q);
  const [hl, ho] = display(p, t); pts.push([hl, ho, t]);
  if (pts.length < 2) return;
  const n = pts.length, cuts = [0, Math.floor(n * .5), Math.floor(n * .8), n - 1], alphas = full ? [.14, .30, .55] : [.10, .20, .34];
  ctx.lineWidth = (full ? 2.2 : 1.4) * unit; ctx.strokeStyle = color; ctx.lineJoin = 'round';
  for (let s = 0; s < 3; s++) {
    ctx.globalAlpha = alphas[s]; ctx.beginPath();
    for (let i = cuts[s]; i <= cuts[s + 1]; i++) { const [x, y] = proj(pts[i][0], pts[i][1]); i === cuts[s] ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function frame() {
  requestAnimationFrame(frame);
  if (!S.cfg || !W) return;
  const t = Date.now();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
  drawRings();

  const list = [...S.planes.values()];
  const rank = p => (p.hex === S.cur ? 0 : p.visible ? 1 : p.phase ? 2 : 3);
  list.sort((a, b) => rank(b) - rank(a));            // draw the interesting ones last, on top

  for (const p of list) if (p.tier === 'slate' || p.tier === 'green') drawTrail(p, COLOR[p.tier], false);
  for (const p of list) if (p.tier !== 'slate' && p.tier !== 'green') drawTrail(p, p.phase ? COLOR[p.phase.mode === 'arr' ? 'cyan' : 'coral'] : COLOR.amber, true);

  const feat = S.planes.get(S.cur);
  if (feat?.visible) {                                // sight line from the lab to the plane you should look at
    const c = S.cfg, [lx, ly] = proj(c.lat, c.lon).slice(), [pl, po] = display(feat, t), [px, py] = proj(pl, po);
    ctx.strokeStyle = 'rgba(254,221,92,.6)'; ctx.lineWidth = 1.5 * unit; ctx.setLineDash([5 * unit, 6 * unit]);
    ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(px, py); ctx.stroke(); ctx.setLineDash([]);
  }

  const labels = [];
  for (const p of list) {
    const [la, lo] = display(p, t), [x, y] = proj(la, lo);
    if (x < -40 || y < -40 || x > W + 40 || y > H + 40) continue;
    const color = p.ground ? COLOR.green : p.phase ? COLOR[p.phase.mode === 'arr' ? 'cyan' : 'coral'] : p.visible ? COLOR.amber : COLOR.slate;
    const quiet = p.tier === 'slate' || p.tier === 'green';          // other traffic and taxiing aircraft: no glow, no label
    const cls = p.cls || 'medium', sz = CLASS_PX[cls] * unit * (quiet ? .95 : 1.05);
    const isCur = p.hex === S.cur;
    if (p.visible) {
      ctx.strokeStyle = COLOR.amber; ctx.globalAlpha = .9; ctx.lineWidth = 2 * unit;
      ctx.beginPath(); ctx.arc(x, y, sz * .78, 0, 7); ctx.stroke(); ctx.globalAlpha = 1;
    }
    if (isCur) {
      const k = (t % 1600) / 1600;
      ctx.strokeStyle = `rgba(255,255,255,${.7 * (1 - k)})`; ctx.lineWidth = 2.5 * unit;
      ctx.beginPath(); ctx.arc(x, y, sz * (.9 + k * 1.2), 0, 7); ctx.stroke();
    }
    ctx.save(); ctx.translate(x, y); ctx.rotate(rad(p.track ?? 0)); ctx.scale(sz / 64, sz / 64); ctx.translate(-32, -32);
    ctx.globalAlpha = quiet ? .95 : 1;
    drawIcon(ctx, cls, color, 'rgba(29,28,25,.9)', quiet ? 0 : 14 * unit);
    ctx.restore(); ctx.globalAlpha = 1;
    // passing traffic gets a callsign too if it's a regional jet or bigger (not GA, bizjets, props, helicopters, or on the ground)
    const passing = p.tier === 'slate' && JET_LABEL.has(cls);
    if (!quiet || isCur || passing) labels.push({ p, x, y, sz, isCur, passing });
  }
  labels.sort((a, b) => rank(a.p) - rank(b.p));
  const placed = [];
  for (const { p, x, y, sz, isCur, passing } of labels) {
    const two = isCur || p.visible, name = nameOf(p);
    const sub = two ? `${p.alt != null ? fmt(p.alt) + ' ft' : ''}${typeOf(p) ? ' \u00B7 ' + typeOf(p) : ''}` : '';
    const fs = isCur ? 20 : passing ? 13.5 : 16;                          // passing traffic: smaller, quieter label
    ctx.font = `700 ${fs * unit}px 'Source Sans 3'`;
    const w1 = ctx.measureText(name).width;
    ctx.font = `500 ${12.5 * unit}px 'Source Sans 3'`;
    const w = Math.max(w1, sub ? ctx.measureText(sub).width : 0), h = (two ? 34 : passing ? 15 : 18) * unit;
    const rx = x + sz * .8 + 6 * unit, ry = y - h / 2 - 2 * unit;
    if (placed.some(r => rx < r[0] + r[2] && rx + w > r[0] && ry < r[1] + r[3] && ry + h > r[1])) continue;
    placed.push([rx, ry, w, h]);
    ctx.textAlign = 'left'; ctx.shadowColor = 'rgba(0,0,0,.95)'; ctx.shadowBlur = 5 * unit;
    ctx.fillStyle = p.visible ? COLOR.amber : p.phase?.mode === 'arr' ? COLOR.cyan : p.phase?.mode === 'dep' ? COLOR.coral : passing ? 'rgba(244,244,244,.78)' : '#f4f4f4';
    ctx.font = `700 ${fs * unit}px 'Source Sans 3'`; ctx.fillText(name, rx, ry + (passing ? 12.5 : 15) * unit);
    if (sub) { ctx.fillStyle = 'rgba(255,255,255,.8)'; ctx.font = `500 ${12.5 * unit}px 'Source Sans 3'`; ctx.fillText(sub, rx, ry + 30 * unit); }
    ctx.shadowBlur = 0;
  }
  drawMarkers(t);
}

/* =====================================================================
   main loop
   ===================================================================== */
function setIdle(on, msg) { document.body.classList.toggle('is-idle', on); if (msg) $('idleP').textContent = msg; }

async function tick(force) {
  try {
    const d = await (await fetch('api/aircraft')).json();
    if (d.error) throw new Error(d.error);
    S.lastOk = Date.now(); ingest(d);
    if (d._cfg_rev != null && S.cfg.config_rev != null && d._cfg_rev !== S.cfg.config_rev) { location.reload(); return; }   // the server found the receiver's real location
  } catch (e) { /* handled by watchdog below */ }

  const off = Date.now() - S.lastOk > 15000, stale = Date.now() - S.lastNowAt > 20000;
  $('banner').textContent = off ? "Can't reach the receiver. Retrying." : 'Receiver data is not updating';
  document.body.classList.toggle('offline', off || stale);
  if (off) return;

  classify(); paintTable(); drawOverview();
  $('ovcap').textContent = `Tracking ${S.total} aircraft \u00B7 out to ${Math.round(S.farthest)} nm`;
  if (S.rangeDirty && Date.now() - S.rangeSaved > 60000) { S.rangeSaved = Date.now(); S.rangeDirty = false; try { localStorage.setItem(rangeKey(), JSON.stringify(S.range)); } catch {} }
  // message rate, plus a note only when something needs attention
  $('tracked').textContent = [S.msgRate != null ? `${fmt(S.msgRate)} msgs/s` : '', PS.state === 'blocked' ? 'photos: backup source' : '', S.cfg.fr24_feed && S.frStatus !== 'ok' ? 'FR24 feed down' : ''].filter(Boolean).join(' \u00B7 ');

  if (!S.cands.length) {
    S.cur = null; setIdle(true, S.total
      ? `No airline traffic on the map right now. The receiver is tracking ${S.total} aircraft in all.`
      : 'The receiver has not picked up any aircraft yet.');
    return;
  }
  setIdle(false);
  if (S.changing) return;
  const next = pick(force);
  if (next && next.hex !== S.cur) switchTo(next);
  else if (next) {
    if (!S.curSince) S.curSince = Date.now();
    paintLive(next); paintIdentity(next);
    if (S.info[next.hex]?.d?.photo && S.photoHex !== next.hex) paintPhoto(next);   // photo arrived after the card
  }
}

function clock() {
  const n = new Date(), tz = 'America/Los_Angeles';
  $('clock').textContent = n.toLocaleTimeString('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit' });
  $('date').textContent = n.toLocaleDateString('en-US', { timeZone: tz, weekday: 'long', month: 'long', day: 'numeric' });
}

(async function init() {
  S.cfg = await (await fetch('api/config')).json();
  $('subtitle').textContent = S.cfg.subtitle; $('dstl').textContent = 'from ' + S.cfg.lab_name; document.title = S.cfg.title;
  await Promise.all([400, 500, 600, 700].map(w => document.fonts.load(`${w} 16px 'Source Sans 3'`))).catch(() => {});
  fx = $('fx'); ctx = fx.getContext('2d');
  map = L.map('map', { zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
    boxZoom: false, keyboard: false, touchZoom: false, zoomSnap: 0, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false });
  L.tileLayer(S.cfg.tile_url || 'tiles/' + S.cfg.basemap + '/{z}/{x}/{y}.png', { tileSize: S.cfg.tile_size, zoomOffset: S.cfg.tile_offset, maxZoom: 19, className: 'basemap-' + S.cfg.basemap }).addTo(map);
  $('attrib').textContent = S.cfg.attribution;
  try { const a = JSON.parse(localStorage.getItem(rangeKey())); S.range = Array.isArray(a) && a.length === 36 ? a.map(Number) : null; } catch { S.range = null; }
  S.range = S.range || new Array(36).fill(0);
  fitMap(); initOverview(); fitMap();
  addEventListener('resize', () => { fitMap(); fitOverview(); });
  clock(); setInterval(clock, 1000);
  requestAnimationFrame(frame);
  if (S.cfg.fr24_feed) { await pollFr24(); setInterval(pollFr24, 5000); }
  await tick(); setInterval(() => tick(false), 1000);
  addEventListener('keydown', e => { if (e.code === 'Space' || e.code === 'ArrowRight') { e.preventDefault(); tick(true); } });
  // fresh page every night keeps a months-long kiosk session tidy
  setInterval(() => { const n = new Date(); if (n.getHours() === 4 && n.getMinutes() === 0 && performance.now() > 3.6e6) location.reload(); }, 60000);
})();
