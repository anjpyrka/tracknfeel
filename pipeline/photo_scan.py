#!/usr/bin/env python3
"""Scan Google Photos Takeout zips (optionally wrapped in a Drive download zip) WITHOUT extracting.
Reads JSON sidecars and the EXIF header of every JPEG, writes inbox/photos/photo_index.json:
one row per image with taken time, GPS (EXIF first, sidecar second), camera make/model, filename.
Nothing else is extracted; videos are skipped (no cheap EXIF)."""
import zipfile, json, glob, io, os, sys, time
from PIL import Image, ExifTags
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inbox", "photos")
IDX = os.path.join(ROOT, "photo_index.json")
rows = {r["file"]: r for r in json.load(open(IDX))} if os.path.exists(IDX) else {}
log = open(os.path.join(ROOT, "photo_scan.log"), "a")
def say(*a): print(time.strftime("%H:%M:%S"), *a, file=log, flush=True)
def dms(v, ref):
    try:
        d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
        return -d if ref in ("S", "W") else d
    except Exception: return None
def exif_of(b):
    try:
        im = Image.open(io.BytesIO(b)); ex = im.getexif()
    except Exception: return {}
    out = {"make": str(ex.get(271, "")).strip("\x00 "), "model": str(ex.get(272, "")).strip("\x00 ")}
    try:
        sub = ex.get_ifd(0x8769); out["dt"] = sub.get(36867) or ex.get(306)
    except Exception: out["dt"] = ex.get(306)
    try:
        g = ex.get_ifd(0x8825)
        if g and 2 in g and 4 in g:
            out["lat"] = dms(g[2], g.get(1, "N")); out["lng"] = dms(g[4], g.get(3, "E"))
    except Exception: pass
    return out
def scan_inner(zi, src):
    for m in zi.infolist():
        name = m.filename; base = name.split("/")[-1]
        if name.endswith(".json"):
            try: d = json.loads(zi.read(m))
            except Exception: continue
            if "title" in d:
                r = rows.setdefault(d["title"], {"file": d["title"]})
                r["ts"] = int(d.get("photoTakenTime", {}).get("timestamp", 0) or 0)
                r["slat"] = d.get("geoData", {}).get("latitude"); r["slng"] = d.get("geoData", {}).get("longitude")
                r["origin"] = json.dumps(d.get("googlePhotosOrigin", {}))[:60]
        elif base.lower().endswith((".jpg", ".jpeg", ".heic")) and m.file_size > 20000:
            with zi.open(m) as f: head = f.read(262144)
            r = rows.setdefault(base, {"file": base}); r.update(exif_of(head)); r["size"] = m.file_size
    say("done", src, "rows", len(rows))
for outer in (sys.argv[1:] or sorted(glob.glob(os.path.join(ROOT, "*.zip")))):
    zo = zipfile.ZipFile(outer)
    inner = [i for i in zo.infolist() if i.filename.endswith(".zip")]
    if not inner:
        scan_inner(zo, os.path.basename(outer)); continue
    for i in inner:
        with zo.open(i) as f:
            try: zi = zipfile.ZipFile(f)
            except Exception as e: say("inner failed", outer, i.filename, e); continue
            scan_inner(zi, i.filename)
    json.dump(list(rows.values()), open(os.path.join(ROOT, "photo_index.json"), "w"), ensure_ascii=False, indent=0)
json.dump(list(rows.values()), open(os.path.join(ROOT, "photo_index.json"), "w"), ensure_ascii=False, indent=0)
say("ALL DONE", len(rows))
