#!/usr/bin/env python3
"""Unattended place lookup for the GitHub Action.

For every place in inbox/places_todo.json that inbox/osm_candidates.json does not know yet, asks Photon
(komoot's fast OSM reverse geocoder) for ALL named objects within ~150 m, with their OSM category, and
Nominatim (1 request/s, polite) for whatever Photon finds nothing for. The choice of which candidate names a
place is made in build.py, where the usage pattern is known (a weekday-morning drop-off spot picks the
kindergarten over the kiosk next door). Only unknown places are queried, so a monthly run is cheap."""
import json, os, sys, time, math, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "inbox")
TODO = os.path.join(INBOX, "places_todo.json")
OUT = os.path.join(INBOX, "osm_candidates.json")
UA = "tracknfeel/1.0 (personal location archive; github.com/anjpyrka/tracknfeel)"
BUDGET = float(os.environ.get("OSM_BUDGET_SECONDS", "2400"))
RADIUS_M = 150
SKIP_KEYS = {"highway", "place", "boundary", "landuse", "waterway", "natural", "admin_level", "addr:street", "railway"}

def hav(a, b, c, d):
    p = math.pi / 180
    return 12742000 * math.asin(math.sqrt(math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2))

def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def photon(lat, lng):
    """-> list of [name, osm_key, osm_value, distance_m], nearest first; [] when nothing named is near."""
    j = get(f"https://photon.komoot.io/reverse?lat={lat}&lon={lng}&radius={RADIUS_M / 1000:.2f}&limit=12&distance_sort=true")
    out = []
    for f in j.get("features", []):
        p = f.get("properties", {}); name = p.get("name")
        if not name or p.get("osm_key") in SKIP_KEYS:
            continue
        lo, la = f["geometry"]["coordinates"]
        d = hav(lat, lng, la, lo)
        if d <= RADIUS_M:
            out.append([name, p.get("osm_key") or "", p.get("osm_value") or "", int(d)])
    out.sort(key=lambda c: c[3])
    return out

def nominatim(lat, lng):
    j = get(f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lng}&zoom=18&namedetails=1")
    name = (j.get("namedetails") or {}).get("name") or j.get("name")
    if not name or j.get("category") in SKIP_KEYS:
        return []
    d = hav(lat, lng, float(j["lat"]), float(j["lon"]))
    return [[name, j.get("category") or "", j.get("type") or "", int(d)]] if d <= 100 else []

def main():
    if not os.path.exists(TODO):
        print("no places_todo.json, nothing to do"); return
    todo = json.load(open(TODO, encoding="utf-8"))
    cand = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    pending = [t for t in todo if t[0] not in cand]          # todo is ordered by time spent, most important first
    print(f"{len(todo)} places, {len(cand)} already looked up, {len(pending)} to do")
    t0 = time.time(); fails = 0; done = 0; hits = 0; misses = []
    save = lambda: json.dump(cand, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    from concurrent.futures import ThreadPoolExecutor
    def look(t):
        key, lat, lng = t
        try:
            return key, lat, lng, photon(lat, lng), None
        except Exception as e:
            return key, lat, lng, None, e
    with ThreadPoolExecutor(max_workers=2) as ex:
        for i in range(0, len(pending), 40):
            if time.time() - t0 > BUDGET * 0.7:
                print("photon budget reached"); break
            for key, lat, lng, c, err in ex.map(look, pending[i:i + 40]):
                done += 1
                if err is not None:
                    fails += 1
                    if fails <= 5 or fails % 20 == 0: print(f"  photon error #{fails}: {err}")
                    if "429" in str(err) or "503" in str(err): time.sleep(10)
                    continue
                if c:
                    cand[key] = c; hits += 1
                else:
                    misses.append((key, lat, lng))
            if fails > 60 and fails > 0.5 * done:
                print("photon failing repeatedly, stopping"); break
            if done % 200 < 40:
                print(f"  photon {done}/{len(pending)}: {hits} with candidates"); save()
    print(f"photon: {hits} places with candidates, {len(misses)} with nothing named within {RADIUS_M} m")
    n2 = nfails = 0
    for key, lat, lng in misses:
        if time.time() - t0 > BUDGET: print("nominatim budget reached"); break
        try:
            c = nominatim(lat, lng)
        except Exception as e:
            nfails += 1; c = None
            if nfails <= 3: print("  nominatim error:", e)
            if nfails > 30: print("nominatim failing repeatedly, stopping"); break
            time.sleep(5)
        if c is not None:
            cand[key] = c                                    # [] is a valid answer: nothing there, don't ask again
            if c: n2 += 1
        time.sleep(1.05)                                   # Nominatim usage policy: max 1 request/s
    print(f"nominatim: {n2} more named; {len(cand)} places looked up in total")
    save()

if __name__ == "__main__":
    main()
