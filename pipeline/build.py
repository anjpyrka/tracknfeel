#!/usr/bin/env python3
"""
tracknfeel build pipeline
-------------------------
Reads every location source in ../inbox, merges and thins it, derives a
per-day summary (city, country, km, sources) and writes encrypted,
gzip-compressed per-year bundles plus a small index to ../site/data.

Inputs (all optional, all read recursively from ../inbox):
  *.json           Google Maps Timeline on-device exports (semanticSegments)
  *.gpx            GPSLogger daily tracks
  reconstructed.json  hand/AI reconstructed days: [{date, lat, lng, label, source, confidence}]
  labels.json      {placeId_or_"lat,lng": "Display name"} for frequent places

Secret: the passphrase is read from ../.secret (one line) or the
TRACKNFEEL_PASSPHRASE environment variable. Never commit .secret.

Run:  python3 pipeline/build.py            (from the repo root or anywhere)
"""
import csv, glob, gzip, hashlib, io, json, math, os, re, sys, time, datetime
from collections import defaultdict, Counter
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "inbox")
OUT = os.path.join(ROOT, "docs", "data")
PIPE = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------- helpers
def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def parse_latlng(s):
    # "41.9181651°, 12.4842993°"
    a = s.replace("°", "").split(",")
    return float(a[0]), float(a[1])

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$")

def parse_ts(s):
    """Return (local_date 'YYYY-MM-DD', minutes_from_local_midnight (float), utc_epoch_seconds, offset_minutes)."""
    m = TS_RE.match(s)
    if not m:
        raise ValueError(s)
    y, mo, d, hh, mm, ss, tz = m.groups()
    off = 0 if tz == "Z" else (int(tz[1:3]) * 60 + int(tz[4:6])) * (1 if tz[0] == "+" else -1)
    local = datetime.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss))
    utc = local - datetime.timedelta(minutes=off)
    epoch = utc.replace(tzinfo=datetime.timezone.utc).timestamp()
    return local.strftime("%Y-%m-%d"), local.hour * 60 + local.minute + local.second / 60.0, epoch, off

def encode_polyline(points, precision=5):
    """Google encoded polyline. points: list of (lat, lng)."""
    out = []
    factor = 10 ** precision
    prev_lat = prev_lng = 0
    for lat, lng in points:
        ilat, ilng = int(round(lat * factor)), int(round(lng * factor))
        for v in (ilat - prev_lat, ilng - prev_lng):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)

def simplify(points, tol_m=25.0):
    """Douglas-Peucker on (lat,lng[,t]) tuples using metres; keeps endpoints."""
    if len(points) <= 2:
        return points
    def perp(p, a, b):
        # approximate planar distance in metres
        ky = 111320.0
        kx = 111320.0 * math.cos(math.radians(a[0]))
        ax, ay = a[1] * kx, a[0] * ky
        bx, by = b[1] * kx, b[0] * ky
        px, py = p[1] * kx, p[0] * ky
        dx, dy = bx - ax, by - ay
        if dx == dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
    stack = [(0, len(points) - 1)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        dmax, idx = 0, -1
        for k in range(i + 1, j):
            d = perp(points[k], points[i], points[j])
            if d > dmax:
                dmax, idx = d, k
        if dmax > tol_m:
            keep[idx] = True
            stack.append((i, idx)); stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]

# -------------------------------------------------------------- geocoding
COUNTRY_NAMES = {}  # cc -> name, loaded from countries.json in pipeline dir

class CityIndex:
    """Nearest-city lookup over GeoNames cities15000 (name, admin1, cc, population).
    Pure python grid search. A city's effective distance is shrunk by its population, so a
    point in a suburb maps to the metropolis (Berlin, not Tiergarten; Rome, not Vatican City)."""
    def __init__(self, path):
        self.cells = defaultdict(list)
        self.cities = []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for row in csv.reader(f):
                lat, lon = float(row[0]), float(row[1])
                pop = int(row[5]) if len(row) > 5 and row[5].isdigit() else 15000
                self.cities.append((lat, lon, row[2], row[3], row[4], pop))
                self.cells[(int(lat // 0.5), int(lon // 0.5))].append(len(self.cities) - 1)
        self.cache = {}
    @staticmethod
    def score(d, pop):
        # metres, discounted by population: a 3M city "reaches" ~4x further than a 15k town
        return d / (1.0 + 0.6 * math.log10(max(pop, 15000) / 15000.0))
    def nearest(self, lat, lon):
        key = (round(lat, 2), round(lon, 2))
        if key in self.cache:
            return self.cache[key]
        best, bests = None, 1e18
        ci, cj = int(lat // 0.5), int(lon // 0.5)
        for ring in range(0, 12):  # up to ~6 degrees
            for i in range(ci - ring, ci + ring + 1):
                for j in range(cj - ring, cj + ring + 1):
                    if max(abs(i - ci), abs(j - cj)) != ring:
                        continue
                    for idx in self.cells.get((i, j), ()):
                        c = self.cities[idx]
                        s = self.score(haversine(lat, lon, c[0], c[1]), c[5])
                        if s < bests:
                            best, bests = idx, s
            if best is not None and ring >= 2 and bests < ring * 25000:
                break
        best = self.parent(best)
        self.cache[key] = best
        return best
    def parent(self, idx):
        """Absorb districts/suburbs into their metropolis: a city with >=500k within 12 km,
        or >=100k within 6 km, that is at least 3x larger. Memoised."""
        if idx is None:
            return None
        if not hasattr(self, "_parent"):
            self._parent = {}
        if idx in self._parent:
            return self._parent[idx]
        c = self.cities[idx]
        best, bestpop = idx, c[5]
        ci, cj = int(c[0] // 0.5), int(c[1] // 0.5)
        for i in range(ci - 1, ci + 2):
            for j in range(cj - 1, cj + 2):
                for k in self.cells.get((i, j), ()):
                    o = self.cities[k]
                    if o[5] < 3 * c[5] or o[5] <= bestpop or o[4] != c[4]:
                        continue
                    d = haversine(c[0], c[1], o[0], o[1])
                    if (o[5] >= 500000 and d < 12000) or (o[5] >= 100000 and d < 6000):
                        best, bestpop = k, o[5]
        self._parent[idx] = best
        return best

# ------------------------------------------------------------ data model
class Day:
    __slots__ = ("date", "visits", "moves", "tracks", "sources", "notes")
    def __init__(self, date):
        self.date = date
        self.visits = []   # [startMin, endMin, lat, lng, placeKey, semType, source]
        self.moves = []    # [startMin, endMin, type, distanceM, source]
        self.tracks = []   # [startMin, endMin, [(lat,lng,min)], source]
        self.sources = set()
        self.notes = []    # reconstructed labels / confidence

days = defaultdict(lambda: None)
def day(date):
    if days[date] is None:
        days[date] = Day(date)
    return days[date]

place_meta = {}  # placeKey -> {"lat","lng","sem": Counter, "n":int, "min":float}

def place_key(place_id, lat, lng):
    if place_id:
        return place_id
    return f"{lat:.4f},{lng:.4f}"

def note_place(key, lat, lng, sem, minutes):
    m = place_meta.setdefault(key, {"lat": lat, "lng": lng, "sem": Counter(), "n": 0, "min": 0.0})
    m["n"] += 1; m["min"] += minutes
    if sem and sem not in ("UNKNOWN",):
        m["sem"][sem] += 1

def split_across_midnight(d0, m0, d1, m1):
    """Yield (date, startMin, endMin) pieces for an interval that may cross midnight (local)."""
    if d0 == d1:
        yield d0, m0, m1; return
    cur = datetime.date.fromisoformat(d0)
    end = datetime.date.fromisoformat(d1)
    s = m0
    while cur < end:
        yield cur.isoformat(), s, 1440.0
        cur += datetime.timedelta(days=1); s = 0.0
        if (end - cur).days > 14:  # guard against absurd segments
            return
    yield d1, 0.0, m1

# ----------------------------------------------------------- Timeline.json
def ingest_timeline(path):
    log("timeline:", os.path.basename(path))
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "semanticSegments" not in data:
        log("  skipped (not a Timeline export)"); return
    segs = data.get("semanticSegments", [])
    n_v = n_a = n_p = 0
    for s in segs:
        try:
            d0, m0, e0, _ = parse_ts(s["startTime"]); d1, m1, e1, _ = parse_ts(s["endTime"])
        except Exception:
            continue
        if "visit" in s:
            tc = s["visit"].get("topCandidate", {})
            loc = tc.get("placeLocation", {}).get("latLng")
            if not loc:
                continue
            lat, lng = parse_latlng(loc)
            sem = tc.get("semanticType", "UNKNOWN")
            key = place_key(tc.get("placeId"), lat, lng)
            for dd, a, b in split_across_midnight(d0, m0, d1, m1):
                if b - a < 0.5:
                    continue
                D = day(dd); D.visits.append([a, b, lat, lng, key, sem, "T"]); D.sources.add("T")
                note_place(key, lat, lng, sem, b - a)
            n_v += 1
        elif "activity" in s:
            act = s["activity"]
            typ = act.get("topCandidate", {}).get("type", "UNKNOWN_ACTIVITY_TYPE")
            dist = float(act.get("distanceMeters", 0) or 0)
            total = max(1.0, (e1 - e0) / 60.0)
            for dd, a, b in split_across_midnight(d0, m0, d1, m1):
                frac = (b - a) / total
                D = day(dd); D.moves.append([a, b, typ, dist * min(1.0, frac), "T"]); D.sources.add("T")
            # make sure start/end of an activity are present as track anchors even if no timelinePath
            st, en = act.get("start", {}).get("latLng"), act.get("end", {}).get("latLng")
            if st and en and d0 == d1:
                p0, p1 = parse_latlng(st), parse_latlng(en)
                day(d0).tracks.append([m0, m1, [(p0[0], p0[1], m0), (p1[0], p1[1], m1)], "A"])
            n_a += 1
        elif "timelinePath" in s:
            pts = []
            for p in s["timelinePath"]:
                try:
                    lat, lng = parse_latlng(p["point"]); dd, mm, ep, _ = parse_ts(p["time"])
                except Exception:
                    continue
                pts.append((dd, lat, lng, mm))
            # split by local date
            bydate = defaultdict(list)
            for dd, lat, lng, mm in pts:
                bydate[dd].append((lat, lng, mm))
            for dd, lst in bydate.items():
                if len(lst) < 2:
                    continue
                D = day(dd); D.tracks.append([lst[0][2], lst[-1][2], lst, "T"]); D.sources.add("T")
            n_p += 1
    log(f"  visits {n_v}, activities {n_a}, paths {n_p}")
    prof = data.get("userLocationProfile", {})
    for fp in prof.get("frequentPlaces", []):
        try:
            lat, lng = parse_latlng(fp["placeLocation"])
        except Exception:
            continue
        key = fp.get("placeId")
        # frequentPlaces use masked placeIds; match on coordinates instead
        for k, m in place_meta.items():
            if abs(m["lat"] - lat) < 1e-4 and abs(m["lng"] - lng) < 1e-4 and fp.get("label"):
                m["sem"][fp["label"]] += 1000

# ------------------------------------------------------------------ GPX
def ingest_gpx(path, tz_offset_guess):
    ns = {"g": "http://www.topografix.com/GPX/1/0"}
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        log("  gpx parse error", path); return
    root = tree.getroot()
    tag_ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
    pts = []
    for trkpt in root.iter(tag_ns + "trkpt"):
        t = trkpt.find(tag_ns + "time")
        if t is None or not t.text:
            continue
        try:
            dd, mm, ep, off = parse_ts(t.text.strip())
        except Exception:
            continue
        lat, lon = float(trkpt.get("lat")), float(trkpt.get("lon"))
        pts.append((ep, lat, lon))
    if not pts:
        return
    pts.sort()
    # local day: GPSLogger writes UTC; shift by the day's offset guess (from Timeline data for same UTC date, else longitude)
    bydate = defaultdict(list)
    for ep, lat, lon in pts:
        utc_date = datetime.datetime.utcfromtimestamp(ep).strftime("%Y-%m-%d")
        off = tz_offset_guess.get(utc_date)
        if off is None:
            off = int(round(lon / 15.0)) * 60
        local = datetime.datetime.utcfromtimestamp(ep) + datetime.timedelta(minutes=off)
        bydate[local.strftime("%Y-%m-%d")].append((lat, lon, local.hour * 60 + local.minute + local.second / 60.0))
    for dd, lst in bydate.items():
        # break into tracks on gaps > 20 min
        cur = [lst[0]]
        for p in lst[1:]:
            if p[2] - cur[-1][2] > 20:
                if len(cur) >= 2:
                    day(dd).tracks.append([cur[0][2], cur[-1][2], cur, "G"])
                cur = [p]
            else:
                cur.append(p)
        if len(cur) >= 2:
            day(dd).tracks.append([cur[0][2], cur[-1][2], cur, "G"])
        day(dd).sources.add("G")
        # stationary clusters as visits: points within 60 m for >= 15 min
        i = 0
        while i < len(lst):
            j = i
            while j + 1 < len(lst) and haversine(lst[i][0], lst[i][1], lst[j + 1][0], lst[j + 1][1]) < 60:
                j += 1
            if lst[j][2] - lst[i][2] >= 15:
                lat = sum(p[0] for p in lst[i:j + 1]) / (j - i + 1)
                lng = sum(p[1] for p in lst[i:j + 1]) / (j - i + 1)
                key = place_key(None, lat, lng)
                day(dd).visits.append([lst[i][2], lst[j][2], lat, lng, key, "UNKNOWN", "G"])
                note_place(key, lat, lng, None, lst[j][2] - lst[i][2])
            i = j + 1

# -------------------------------------------------------- reconstructed
def ingest_reconstructed(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    n = 0
    for it in items:
        dd = it["date"]; lat, lng = float(it["lat"]), float(it["lng"])
        D = day(dd)
        a, b = it.get("startMin", 0.0), it.get("endMin", 1440.0)
        key = place_key(it.get("placeId"), lat, lng)
        D.visits.append([a, b, lat, lng, key, it.get("semanticType", "UNKNOWN"), "R"])
        D.sources.add("R")
        D.notes.append({"label": it.get("label", ""), "source": it.get("source", ""), "confidence": it.get("confidence", "medium")})
        note_place(key, lat, lng, None, b - a)
        n += 1
    log("  reconstructed entries:", n)

# ---------------------------------------------------------------- photos
def ingest_photos(index_path, cameras):
    """Geotagged photos as evidence for days no device recorded. inbox/photos/photo_index.json comes from
    pipeline/photo_scan.py; inbox/cameras.json lists substrings of EXIF make/model that are MY phones, so
    photos from other people's devices that were shared into the library are ignored."""
    rows = json.load(open(index_path, encoding="utf-8"))
    n = used = 0
    bydate = defaultdict(list)
    for r in rows:
        cam = f"{r.get('make','')} {r.get('model','')}".strip()
        if not cam or not any(c.lower() in cam.lower() for c in cameras):
            continue
        lat, lng = r.get("lat"), r.get("lng")
        if (lat is None or lng is None) and r.get("slat"):
            lat, lng = r["slat"], r["slng"]
        if not lat or not lng:
            continue
        dt = r.get("dt")  # EXIF local time "YYYY:MM:DD HH:MM:SS" is the camera's local clock
        if dt and len(dt) >= 19:
            dd = dt[:10].replace(":", "-"); mins = int(dt[11:13]) * 60 + int(dt[14:16])
        elif r.get("ts"):
            local = datetime.datetime.utcfromtimestamp(r["ts"]) + datetime.timedelta(minutes=int(round(lng / 15.0)) * 60)
            dd = local.strftime("%Y-%m-%d"); mins = local.hour * 60 + local.minute
        else:
            continue
        n += 1
        bydate[dd].append((mins, float(lat), float(lng), cam, r.get("file", "")))
    for dd, lst in bydate.items():
        D = day(dd)
        if "T" in D.sources:
            continue  # the phone recorded this day; photos add nothing but clutter
        lst.sort()
        # cluster consecutive photos within 400 m into one stop (first to last shutter time, at least 5 min)
        clusters = []
        for mins, lat, lng, cam, fn in lst:
            c = clusters[-1] if clusters else None
            if c and haversine(c["lat"], c["lng"], lat, lng) < 400:
                c["end"] = mins; c["n"] += 1
                c["lat"] = (c["lat"] * (c["n"] - 1) + lat) / c["n"]; c["lng"] = (c["lng"] * (c["n"] - 1) + lng) / c["n"]
            else:
                clusters.append({"start": mins, "end": mins, "lat": lat, "lng": lng, "n": 1})
        for c in clusters:
            key = place_key(None, c["lat"], c["lng"])
            end = min(1440.0, max(c["end"], c["start"] + 5))
            D.visits.append([c["start"], end, c["lat"], c["lng"], key, "PHOTO", "P"])
            note_place(key, c["lat"], c["lng"], None, end - c["start"])
            used += 1
        D.sources.add("P")
        D.notes.append({"label": f"{len(lst)} geotagged photo{'s' if len(lst) > 1 else ''} from my phone in {len(clusters)} place{'s' if len(clusters) > 1 else ''}", "source": "Google Photos", "confidence": "high"})
    log(f"  photos: {n} geotagged from my cameras, {used} photo stops on {sum(1 for d in bydate if 'P' in days[d].sources)} undocumented days")

# ------------------------------------------------------------ encryption
def encrypt(passphrase, salt, plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 200_000, 32)
    iv = os.urandom(12)
    return b"TNF1" + iv + AESGCM(key).encrypt(iv, plaintext, None)

def pack(obj):
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw, 9), len(raw)

# ------------------------------------------------------------------ main
ACT_SHORT = {
    "IN_PASSENGER_VEHICLE": "car", "IN_VEHICLE": "car", "WALKING": "walk", "RUNNING": "run", "CYCLING": "bike",
    "IN_BUS": "bus", "IN_TRAIN": "train", "IN_SUBWAY": "metro", "IN_TRAM": "tram", "FLYING": "flight",
    "MOTORCYCLING": "moto", "IN_FERRY": "ferry", "SAILING": "boat", "SKIING": "ski", "STILL": "still",
    "UNKNOWN_ACTIVITY_TYPE": "?", "IN_TAXI": "taxi", "HIKING": "hike", "BOATING": "boat", "KAYAKING": "boat",
}

def main():
    t0 = time.time()
    passphrase = os.environ.get("TRACKNFEEL_PASSPHRASE")
    secret_path = os.path.join(ROOT, ".secret")
    if not passphrase and os.path.exists(secret_path):
        passphrase = open(secret_path, encoding="utf-8").read().strip()
    if not passphrase:
        sys.exit("No passphrase: create .secret in the repo root or set TRACKNFEEL_PASSPHRASE")
    global COUNTRY_NAMES
    COUNTRY_NAMES = json.load(open(os.path.join(PIPE, "countries.json"), encoding="utf-8"))
    labels = {}
    lp = os.path.join(INBOX, "labels.json")
    if os.path.exists(lp):
        labels = json.load(open(lp, encoding="utf-8"))

    # 1. ingest
    tl_files = sorted(glob.glob(os.path.join(INBOX, "**", "*.json"), recursive=True))
    tl_files = [p for p in tl_files if os.path.basename(p) not in ("reconstructed.json", "labels.json", "cameras.json", "photo_index.json")
                and os.sep + "photos" + os.sep not in p]
    for p in tl_files:
        ingest_timeline(p)
    # offset guess per UTC date from timeline visits (approximate: use the local date)
    tz_guess = {}
    for dd, D in days.items():
        if D is None: continue
    # build a date->offset map by re-reading offsets is expensive; approximate with longitude of day centroid later
    gpx_files = sorted(glob.glob(os.path.join(INBOX, "**", "*.gpx"), recursive=True))
    for p in gpx_files:
        ingest_gpx(p, tz_guess)
    log(f"  gpx files: {len(gpx_files)}")
    pi = os.path.join(INBOX, "photos", "photo_index.json"); cp = os.path.join(INBOX, "cameras.json")
    if os.path.exists(pi):
        cams = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else []
        if cams:
            ingest_photos(pi, cams)
        else:
            log("  photos: inbox/cameras.json missing, photo evidence skipped")
    rp = os.path.join(INBOX, "reconstructed.json")
    if os.path.exists(rp):
        ingest_reconstructed(rp)

    # 2. derive
    log("geocoding")
    cities = CityIndex(os.path.join(PIPE, "cities.csv.gz"))
    city_ids = {}      # geonames idx -> compact idx
    city_table = []    # [name, admin1, cc, lat, lon]
    def city_ref(lat, lng):
        gi = cities.nearest(lat, lng)
        if gi is None:
            return -1
        if gi not in city_ids:
            c = cities.cities[gi]
            city_ids[gi] = len(city_table); city_table.append([c[2], c[3], c[4], c[0], c[1], c[5]])
        return city_ids[gi]

    years = defaultdict(dict)
    index_days = {}
    heat = Counter()
    all_dates = sorted(d for d, D in days.items() if D is not None)
    for dd in all_dates:
        D = days[dd]
        D.visits.sort(); D.moves.sort(); D.tracks.sort(key=lambda t: t[0])
        # dedupe visits: identical place & overlapping time from different sources -> keep Timeline one
        vis = []
        for v in D.visits:
            dup = False
            for w in vis:
                if w[4] == v[4] and not (v[1] <= w[0] or v[0] >= w[1]):
                    if v[6] == "T" and w[6] != "T":
                        vis.remove(w); break
                    dup = True; break
            if not dup:
                vis.append(v)
        D.visits = vis
        # dominant city/country by visit minutes, else by track points
        wt = Counter()
        for v in D.visits:
            wt[(round(v[2], 3), round(v[3], 3))] += v[1] - v[0]
        if not wt:
            for t in D.tracks:
                for p in t[2][::max(1, len(t[2]) // 20)]:
                    wt[(round(p[0], 3), round(p[1], 3))] += 1
        if not wt:
            continue
        (clat, clng), _ = wt.most_common(1)[0]
        cref = city_ref(clat, clng)
        cc = city_table[cref][2] if cref >= 0 else "??"
        # all distinct cities touched that day (visits)
        touched = []
        for (la, ln), mins in wt.most_common():
            r = city_ref(la, ln)
            if r >= 0 and r not in touched:
                touched.append(r)
        # km
        km = sum(m[3] for m in D.moves) / 1000.0
        if km == 0 and D.tracks:
            for t in D.tracks:
                pts = t[2]
                km += sum(haversine(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)) / 1000.0
        # tracks -> simplified polylines
        tracks_out = []
        for t in D.tracks:
            pts = simplify(t[2], 20.0 if t[3] != "A" else 0)
            if len(pts) > 1500:
                step = len(pts) / 1500.0
                pts = [pts[int(i * step)] for i in range(1500)] + [pts[-1]]
            tracks_out.append([round(t[0], 1), round(t[1], 1), encode_polyline([(p[0], p[1]) for p in pts]), t[3]])
            for p in pts[::3]:
                heat[(round(p[0], 3), round(p[1], 3))] += 1
        for v in D.visits:
            heat[(round(v[2], 3), round(v[3], 3))] += max(1, int((v[1] - v[0]) / 15))
        src = "".join(sorted(D.sources))
        years[dd[:4]][dd] = {
            "v": [[round(v[0], 1), round(v[1], 1), round(v[2], 5), round(v[3], 5), v[4], v[5], v[6]] for v in D.visits],
            "m": [[round(m[0], 1), round(m[1], 1), ACT_SHORT.get(m[2], m[2].lower()), int(m[3]), m[4]] for m in D.moves],
            "t": tracks_out,
            "c": cref, "cc": cc, "ct": touched[:6], "km": round(km, 1), "s": src,
            **({"n": D.notes} if D.notes else {}),
        }
        index_days[dd] = [cref, round(km), src, touched[:3]]

    # places table (top by minutes) with labels
    top_places = sorted(place_meta.items(), key=lambda kv: -kv[1]["min"])[:3000]
    places = {}
    for key, m in top_places:
        sem = m["sem"].most_common(1)[0][0] if m["sem"] else ""
        label = labels.get(key) or labels.get(f"{m['lat']:.4f},{m['lng']:.4f}") or ""
        places[key] = [round(m["lat"], 5), round(m["lng"], 5), int(m["min"]), m["n"], sem, label, city_ref(m["lat"], m["lng"])]

    # 3. write
    os.makedirs(OUT, exist_ok=True)
    for old in glob.glob(os.path.join(OUT, "*.bin")):
        if os.path.basename(old) != "index.bin" and not os.path.basename(old)[:4].isdigit():
            continue
        try:
            os.remove(old)       # stale bundles; files are overwritten anyway, so a refusal is harmless
        except OSError:
            pass
    salt = hashlib.sha256(b"tracknfeel-salt-v1").digest()[:16]  # fixed salt: one key for all files (passphrase strength matters, not salt secrecy)
    manifest = {"v": 1, "salt": salt.hex(), "iter": 200000, "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": []}
    total = 0
    for y in sorted(years):
        blob, rawlen = pack({"year": int(y), "days": years[y]})
        enc = encrypt(passphrase, salt, blob)
        fn = f"{y}.bin"
        open(os.path.join(OUT, fn), "wb").write(enc)
        manifest["files"].append({"name": fn, "year": int(y), "bytes": len(enc), "days": len(years[y])})
        total += len(enc)
        log(f"  {fn}: {len(years[y])} days, json {rawlen/1e6:.1f} MB -> {len(enc)/1e6:.2f} MB")
    # heat: keep the strongest 120k cells
    heat_pts = [[k[0], k[1], min(v, 500)] for k, v in heat.most_common(120000)]
    index = {
        "built": manifest["built"],
        "first": all_dates[0], "last": all_dates[-1],
        "cities": city_table, "countries": COUNTRY_NAMES,
        "days": index_days, "places": places, "heat": heat_pts,
        "gaps": find_gaps(all_dates),
    }
    blob, rawlen = pack(index)
    enc = encrypt(passphrase, salt, blob)
    open(os.path.join(OUT, "index.bin"), "wb").write(enc)
    manifest["index"] = {"name": "index.bin", "bytes": len(enc)}
    manifest["check"] = encrypt(passphrase, salt, b"tracknfeel-ok").hex()
    json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
    log(f"  index.bin: json {rawlen/1e6:.1f} MB -> {len(enc)/1e6:.2f} MB; total site data {(total+len(enc))/1e6:.1f} MB")
    log(f"done in {time.time()-t0:.0f}s: {len(all_dates)} days, {len(city_table)} cities, {len(places)} places")

def find_gaps(dates, min_days=3):
    gaps = []
    prev = None
    for d in dates:
        cur = datetime.date.fromisoformat(d)
        if prev and (cur - prev).days > min_days:
            gaps.append([(prev + datetime.timedelta(days=1)).isoformat(), (cur - datetime.timedelta(days=1)).isoformat(), (cur - prev).days - 1])
        prev = cur
    return gaps

if __name__ == "__main__":
    main()
