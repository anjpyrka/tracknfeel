# tracknfeel

My own location history, every day since September 2012, as a private static site.
No server, no database, no subscription: a Python script turns raw exports into small
encrypted bundles, GitHub Pages serves them, and the browser decrypts them with a passphrase.

## How it fits together

```
phone ──Timeline export──▶ inbox/*.json ─┐
phone ──GPSLogger GPX ───▶ inbox/*.gpx ──┼─▶ pipeline/build.py ─▶ docs/data/*.bin ─▶ GitHub Pages ─▶ browser (decrypts)
calendar, mail, photos ─▶ inbox/reconstructed.json ┘
```

* `inbox/` holds the raw sources and is git-ignored. Nothing unencrypted ever leaves this folder.
* `pipeline/build.py` merges, de-duplicates and thins everything, resolves each day to a city and
  country (GeoNames, offline), and writes one encrypted gzip bundle per year plus `index.bin`.
* `docs/` is the viewer: `index.html`, `style.css`, `app.js`, a service worker for offline use,
  and a web app manifest so it installs on the phone.
* `.secret` (git-ignored) holds the passphrase used for encryption. The site asks for the same
  passphrase once and can keep the derived key on the device.

## Monthly ritual (about two minutes)

1. On the phone: Settings → Location → Location services → Timeline → Export Timeline data.
   Put the file into `inbox/` (any name ending in `.json`).
2. Copy new GPSLogger `.gpx` files from the Google Drive folder "GPSLogger for Android" into `inbox/gpx/`.
3. Run `./update.sh` (or `python3 pipeline/build.py` and push `docs/` yourself).

Timeline exports overlap; the pipeline de-duplicates, so old exports can stay in the inbox.

## Reconstructed days

`inbox/reconstructed.json` lists days that no device recorded, with the best evidence available:

```json
[{"date": "2025-02-03", "lat": 52.5334, "lng": 13.3734, "label": "Berlin, home", "source": "calendar", "confidence": "high"}]
```

They are drawn in amber and labelled in the day view so they are never mistaken for measured data.

## Photo evidence

For days no device recorded, geotagged photos fill in. `pipeline/photo_scan.py <takeout.zip ...>` reads the
JSON sidecars and the EXIF header of every JPEG straight out of a Google Photos Takeout (no extraction) and
writes `inbox/photos/photo_index.json`. `inbox/cameras.json` lists the EXIF make/model substrings of my own
phones; photos from other devices that were shared into the library are ignored. The build then turns
consecutive photos within 400 m into one "photo taken here" stop, only on days without Timeline data.

## Labels

`inbox/labels.json` maps a Google place id (or `"lat,lng"` with 4 decimals) to a display name.
The Places view lists the keys of the most-visited spots.

## Privacy model

The repository is public because GitHub Pages on a free account requires it; the data files are
AES-256-GCM encrypted with a key derived from the passphrase (PBKDF2, 200k rounds). The viewer
runs entirely in the browser; the map tiles come from OpenFreeMap, which sees tile requests but no data.
