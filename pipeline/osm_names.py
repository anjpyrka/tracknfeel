#!/usr/bin/env python3
"""Unattended place naming for the GitHub Action: names every place in inbox/places_todo.json that
inbox/osm_places.json does not know yet, using Photon (komoot's fast OSM reverse geocoder) first and
Nominatim (1 request/s, polite) for whatever Photon leaves unnamed. Merges into osm_places.json.
Only places that are still unnamed are queried, so the monthly run only looks up genuinely new places."""
import json, os, sys, time, math, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "inbox")
TODO = os.path.join(INBOX, "places_todo.json")
OUT = os.path.join(INBOX, "osm_places.json")
UA = "tracknfeel/1.0 (personal location archive; github.com/anjpyrka/tracknfeel)"
BUDGET = float(os.environ.get("OSM_BUDGET_SECONDS", "1500"))   # stay well inside the job's time
SKIP_KEYS = {"highway", "place", "boundary", "landuse", "waterway", "natural", "admin_level", "addr:street"}

def hav(a, b, c, d):
    p = math.pi / 180
    return 12742000 * math.asin(math.sqrt(math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2))

def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def photon(lat, lng):
    j = get(f"https://photon.komoot.io/reverse?lat={lat}&lon={lng}&radius=0.08&limit=5&distance_sort=true")
    best = None
    for f in j.get("features", []):
        p = f.get("properties", {}); name = p.get("name")
        if not name or p.get("osm_key") in SKIP_KEYS:
            continue
        lo, la = f["geometry"]["coordinates"]
        d = hav(lat, lng, la, lo)
        if d > 60:
            continue
        pri = 1 if p.get("osm_key") == "building" else 0
        if best is None or d + pri * 40 < best[1]:
            best = (name, d + pri * 40)
    return best[0] if best else None

def nominatim(lat, lng):
    j = get(f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lng}&zoom=18&namedetails=1")
    name = (j.get("namedetails") or {}).get("name") or j.get("name")
    if not name or j.get("category") in SKIP_KEYS:
        return None
    if hav(lat, lng, float(j["lat"]), float(j["lon"])) > 80:
        return None
    return name

def main():
    if not os.path.exists(TODO):
        print("no places_todo.json, nothing to do"); return
    todo = json.load(open(TODO, encoding="utf-8"))
    known = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    pending = [t for t in todo if t[0] not in known]
    print(f"{len(todo)} places, {len(known)} already named, {len(pending)} to look up")
    t0 = time.time(); named = fails = 0; misses = []
    from concurrent.futures import ThreadPoolExecutor
    def look(t):
        key, lat, lng = t
        try:
            return key, lat, lng, photon(lat, lng), None
        except Exception as e:
            return key, lat, lng, None, e
    done = 0
    with ThreadPoolExecutor(max_workers=2) as ex:            # Photon copes fine with a few parallel requests
        for i in range(0, len(pending), 40):
            if time.time() - t0 > BUDGET * 0.7:
                print("photon budget reached"); break
            for key, lat, lng, n, err in ex.map(look, pending[i:i + 40]):
                done += 1
                if err is not None:
                    fails += 1
                    if fails <= 5 or fails % 20 == 0: print(f"  photon error #{fails}: {err}")
                    if "429" in str(err) or "503" in str(err): time.sleep(10)
                elif n:
                    known[key] = n; named += 1
                else:
                    misses.append((key, lat, lng))
            if fails > 60 and fails > 0.5 * done:
                print("photon failing repeatedly, stopping"); break
            if done % 200 < 40:
                print(f"  photon {done}/{len(pending)}: {named} named"); json.dump(known, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"photon: {named} named, {len(misses)} misses")
    n2 = 0
    nfails = 0
    for key, lat, lng in misses:
        if time.time() - t0 > BUDGET: print("nominatim budget reached"); break
        try:
            n = nominatim(lat, lng)
        except Exception as e:
            nfails += 1; n = None
            if nfails <= 3: print("  nominatim error:", e)
            if nfails > 30: print("nominatim failing repeatedly, stopping"); break
            time.sleep(5)
        if n: known[key] = n; n2 += 1
        time.sleep(1.05)                                   # Nominatim usage policy: max 1 request/s
    print(f"nominatim: {n2} more named; total {len(known)} named places")
    json.dump(known, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
