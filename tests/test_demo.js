// Tests for the in-browser demo simulation (static/js/demo.js).  Run:  node tests/test_demo.js
'use strict';
const assert = require('assert');
const path = require('path');
const demo = require(path.join(__dirname, '..', 'static', 'js', 'demo.js'));

const R_NM = 3440.065, rad = d => d * Math.PI / 180;
const dist = (a, b, c, d) => {
  const dl = rad(c - a), dn = rad(d - b), h = Math.sin(dl / 2) ** 2 + Math.cos(rad(a)) * Math.cos(rad(c)) * Math.sin(dn / 2) ** 2;
  return 2 * R_NM * Math.asin(Math.sqrt(h));
};
const angDiff = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };
const cfg = demo.config(), T0 = 1791500000;      // any moment will do: the simulation is a pure function of time

let failed = 0;
function test(name, fn) {
  try { fn(); console.log('PASS  ' + name); } catch (e) { failed++; console.log('FAIL  ' + name + '\n      ' + (e && e.message)); }
}

test('every aircraft record is valid, at several moments', () => {
  for (const t of [T0, T0 + 137, T0 + 901, T0 + 2500, T0 + 7777]) {
    const snap = demo.snapshot(t), seen = new Set(), calls = new Set();
    assert.ok(snap.aircraft.length >= 45, 'only ' + snap.aircraft.length + ' aircraft at t=' + t);
    for (const a of snap.aircraft) {
      assert.ok(/^[0-9a-f]{6}$/.test(a.hex), 'bad hex ' + a.hex);
      assert.ok(!seen.has(a.hex), 'duplicate hex ' + a.hex); seen.add(a.hex);
      const cs = a.flight.trim(); assert.ok(!calls.has(cs), 'two aircraft share callsign ' + cs + ' at once'); calls.add(cs);
      assert.ok(a.lat > -90 && a.lat < 90 && a.lon > -180 && a.lon < 180 && Number.isFinite(a.lat + a.lon), 'bad position');
      assert.ok(a.track >= 0 && a.track < 360 && a.gs >= 0 && Number.isFinite(a.geom_rate), 'bad motion for ' + cs);
      assert.ok(a.alt_baro === 'ground' || (Number.isFinite(a.alt_baro) && a.alt_baro > 0 && a.alt_baro < 45000), 'bad altitude for ' + cs);
      assert.ok(/^A[1-7]$/.test(a.category) && demo.TYPES[a.t], 'bad type/category for ' + cs);
    }
  }
});

test('aircraft move continuously: no teleporting, no snapping headings, no altitude jumps (10 minutes, every second)', () => {
  let prev = new Map(), checked = 0;
  for (let t = T0; t < T0 + 600; t++) {
    const cur = new Map(demo.snapshot(t).aircraft.map(a => [a.hex, a]));
    for (const [hex, a] of cur) {
      const p = prev.get(hex);
      if (!p) continue;
      const step = dist(p.lat, p.lon, a.lat, a.lon);
      assert.ok(step < 0.2, a.flight.trim() + ' jumped ' + step.toFixed(2) + ' nm in one second at t=' + t);          // 0.2 nm/s = 720 kt
      if (p.alt_baro !== 'ground' && a.alt_baro !== 'ground') assert.ok(Math.abs(a.alt_baro - p.alt_baro) < 120, a.flight.trim() + ' altitude jumped at t=' + t);
      if (a.gs > 60) assert.ok(angDiff(p.track, a.track) < 30, a.flight.trim() + ' heading snapped ' + angDiff(p.track, a.track).toFixed(0) + ' deg at t=' + t);
      checked++;
    }
    prev = cur;
  }
  assert.ok(checked > 20000, 'checked too few steps: ' + checked);
});

test('every lane has enough distinct flights that a callsign never flies twice at once', () => {
  for (const ln of demo.LANES) {
    const alive = Math.ceil(ln.path.dur / ln.spacing - 1e-9);                       // the most aircraft this lane ever has in the air at once
    if (/^[A-Z]{3}\d+$/.test(ln.pool[0].cs)) assert.ok(ln.pool.length >= alive, 'lane ' + ln.id + ' (' + ln.pool[0].cs + '...) has ' + ln.pool.length + ' flights for ' + alive + ' in the air');
  }
  const all = demo.LANES.flatMap(l => l.pool.map(f => f.cs));
  assert.strictEqual(new Set(all).size, all.length, 'a callsign appears in two pools');
});

test('the sky has variety over half an hour: arrivals, departures, regionals, OAK/SJC, GA, helicopter, bizjet, taxiing', () => {
  const seen = { arrSFO: 0, depSFO: 0, regional: 0, oak: 0, sjc: 0, ga: 0, heli: 0, biz: 0, ground: 0, heavy: 0 };
  for (let t = T0; t < T0 + 1800; t += 20) {
    for (const s of demo.states(t)) {
      const f = s.f;
      if (f.dest === 'SFO' && !s.ground) seen.arrSFO++;
      if (f.orig === 'SFO' && !s.ground) seen.depSFO++;
      if (f.via) seen.regional++;
      if (f.dest === 'OAK' || f.orig === 'OAK') seen.oak++;
      if (f.dest === 'SJC' || f.orig === 'SJC') seen.sjc++;
      if (f.type === 'C172' || f.type === 'SR22') seen.ga++;
      if (f.type === 'EC35') seen.heli++;
      if (f.type === 'CL35') seen.biz++;
      if (s.ground) seen.ground++;
      if (['B789', 'B77W', 'A359'].includes(f.type)) seen.heavy++;
    }
  }
  for (const k of Object.keys(seen)) assert.ok(seen[k] > 0, 'never saw: ' + k);
});

test('somebody is "in sight" of the receiver at some point (within 2 nm, 10+ degrees up, under 7,000 ft)', () => {
  let hit = null;
  for (let t = T0; t < T0 + 3600 && !hit; t += 5) {
    for (const s of demo.states(t)) {
      if (s.ground) continue;
      const d = dist(cfg.lat, cfg.lon, s.lat, s.lon), up = (s.alt - cfg.lab_elev_ft) / 6076.12;
      if (Math.hypot(d, up) <= cfg.vis_nm && Math.atan2(up, d) * 180 / Math.PI >= cfg.min_elev && s.alt <= cfg.vis_max_alt) hit = s.f.cs;
    }
  }
  assert.ok(hit, 'nothing came in sight within an hour');
});

test('there are always aircraft on the map, and not too many', () => {
  for (let t = T0; t < T0 + 3600; t += 120) {
    const near = demo.states(t).filter(s => dist(cfg.lat, cfg.lon, s.lat, s.lon) < 22).length;
    assert.ok(near >= 12 && near <= 60, near + ' aircraft near the receiver at t=' + t);
  }
});

test('the regional flights say who they fly for', () => {
  assert.deepStrictEqual(demo.marketingInfo('SKW5460', 'UA5460'), ['United Express', 'UA 5460', 'SkyWest Airlines']);
  assert.deepStrictEqual(demo.marketingInfo('QXE2218', 'AS2218'), ['Alaska Airlines', 'AS 2218', 'Horizon Air']);
  assert.deepStrictEqual(demo.marketingInfo('UAL755', 'UA755'), ['United Airlines', '', '']);
  let qxe = [], skw = [];
  for (let t = T0; t < T0 + 3600; t += 30) {
    const rows = Object.values(demo.fr24(t).flights);
    qxe = qxe.concat(rows.filter(r => r[0].startsWith('QXE'))); skw = skw.concat(rows.filter(r => r[0].startsWith('SKW')));
  }
  assert.ok(qxe.length && qxe.every(r => r[8] === 'Alaska Airlines' && r[9] === 'AS ' + r[0].slice(3) && r[10] === 'Horizon Air'), 'Horizon rows lack marketing info');
  // SkyWest's callsign numbers aren't the numbers its flights are sold under, so the demo doesn't claim one
  assert.ok(skw.length && skw.every(r => !r[5] && !r[8] && !r[9] && r[7] === 'SkyWest Airlines'), 'SkyWest rows claim a marketing flight number');
});

test('the API replies have the shapes the dashboard expects', () => {
  assert.ok(['lat', 'lon', 'tile_url', 'tile_size', 'basemap', 'attribution', 'vis_nm', 'overview_nm', 'fr24_feed', 'config_rev', 'sfo'].every(k => k in cfg));
  assert.ok(/^https:\/\/tile\.openstreetmap\.org\//.test(cfg.tile_url));
  const fr = demo.fr24(T0), any = Object.keys(fr.flights)[0];
  assert.strictEqual(fr.status, 'ok'); assert.strictEqual(fr.flights[any].length, 11);
  const s = demo.states(T0).find(x => x.f.orig && x.f.dest), r = demo.route(s.f.cs, s.hex, T0);
  assert.strictEqual(r.route.origin.iata, s.f.orig); assert.strictEqual(r.route.destination.iata, s.f.dest); assert.ok(r.route.destination.city);
  assert.strictEqual(demo.route('N152PA', '', T0).route, null);                      // GA: no route
  const i = demo.info(s.hex, T0); assert.strictEqual(i.aircraft.registration, s.f.reg); assert.strictEqual(i.photo, null);      // the browser fetches the photo itself, from Planespotters, by hex
});

test('airline flights are flown by real, distinct airframes (what lets Planespotters find a photo), GA keeps made-up tails', () => {
  const regs = new Set(), hexes = new Set();
  for (const ln of demo.LANES) for (const f of ln.pool) {
    if (!/^[A-Z]{3}\d/.test(f.cs)) { assert.ok(!f.hex, f.cs + ' (GA) should not claim a real hex'); continue; }
    assert.ok(/^[0-9a-f]{6}$/.test(f.hex), f.cs + ' has no real hex');
    assert.ok(/^([NC]-?[A-Z0-9]{3,5}|[A-Z]{1,2}-?[A-Z0-9]{3,5})$/.test(f.reg), f.cs + ' has a bad registration: ' + f.reg);
    assert.ok(demo.REAL_HEX.has(f.hex), f.cs + ' hex is not marked real');
    assert.ok(!regs.has(f.reg) && !hexes.has(f.hex), 'tail used by two flights: ' + f.reg);
    regs.add(f.reg); hexes.add(f.hex);
    const row = ((demo.FLEET[f.cs.slice(0, 3)] || {})[f.type] || []).find(r => r[0] === f.reg);
    assert.ok(row && row[1].toLowerCase() === f.hex, f.cs + ': registration and hex are not a pair from the fleet table');
  }
  assert.ok(regs.size >= 100, 'only ' + regs.size + ' real airframes');
  for (let t = T0; t < T0 + 3600; t += 60) for (const s of demo.states(t)) if (s.f.hex) assert.strictEqual(s.hex, s.f.hex, 'snapshot hex differs from the airframe hex');
});

test('airline flights use real callsigns on routes they really fly (from the routes data), each once', () => {
  const seen = new Set();
  for (const ln of demo.LANES) for (const f of ln.pool) {
    if (!/^[A-Z]{3}\d/.test(f.cs)) continue;
    assert.ok(!seen.has(f.cs), 'callsign used twice: ' + f.cs); seen.add(f.cs);
    if (!f.orig) continue;                                                          // route-less (NetJets)
    const op = f.cs.slice(0, 3), inRoutes = ((demo.ROUTES[op] || {})[f.orig + '-' + f.dest] || []).some(e => e[0] === f.cs && e[1] === f.num);
    const inFar = Object.values(demo.FAR[op] || {}).some(l => l.some(e => e[0] === f.cs && e[1] === f.orig && e[2] === f.dest && e[3] === f.num));
    assert.ok(inRoutes || inFar, f.cs + ' ' + f.orig + '-' + f.dest + ' is not a real callsign for that route');
  }
  const ba = demo.LANES.flatMap(l => l.pool).find(f => f.cs.startsWith('BAW'));
  assert.deepStrictEqual([ba.cs, ba.num, ba.fn, ba.type], ['BAW5L', 'BA285', 'BA 285', 'A388']);   // BA285 broadcasts BAW5L
});

if (failed) { console.log('\n' + failed + ' test(s) FAILED'); process.exit(1); }
console.log('\nall demo tests passed');
