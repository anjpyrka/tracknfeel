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

## Keeping it current

Day to day nothing is needed: GPSLogger writes a `.gpx` per day into the Google Drive folder "GPSLogger for Android",
and the GitHub Action in `.github/workflows/update.yml` runs on the 1st of every month (or on demand from the Actions tab).
It pulls `My Drive/tracknfeel/inbox` and the GPSLogger folder with rclone, rebuilds the encrypted bundles and pushes
`docs/data/`; GitHub Pages redeploys by itself. So the inbox lives in Google Drive, not on a PC.

One-time setup, in a normal PowerShell window (Start → type "powershell"; no admin needed, accept the UAC
prompt if the installer shows one). rclone builds the Drive folder structure and uploads the inbox itself:

```powershell
winget install Rclone.Rclone
# close and reopen PowerShell so rclone is on the PATH, then:
rclone config create gdrive drive scope drive        # opens the browser once: allow Drive access
rclone copy C:\claude\tracknfeel\inbox gdrive:tracknfeel/inbox --exclude "photos/*.zip" --exclude "places_todo.json" -P
rclone config show gdrive                            # copy the whole {"access_token": ...} value of the token line
```

Then GitHub → repository → Settings → Secrets and variables → Actions → New repository secret, twice:
`RCLONE_GDRIVE_TOKEN` = that token value, `TRACKNFEEL_PASSPHRASE` = the viewer passphrase.
Finally Actions tab → "Rebuild timeline data" → Run workflow, once, to see it go green.
From then on the same `rclone copy` line (or drag and drop in the Drive web UI) is how anything new reaches the inbox.

Google Photos Takeouts are handled the same way: Takeout → "Add to Drive" puts the zips in `My Drive/Takeout`;
Actions tab → "Scan Google Photos Takeout on Drive" → Run workflow reads them there (one at a time, on a throwaway
runner, nothing committed), updates `tracknfeel/inbox/photos/photo_index.json` in Drive and rebuilds. No downloads.

Optional top-ups, whenever you feel like it (every few months is plenty):

- Phone → Settings → Location → Location services → Timeline → Export Timeline data → drop the file into `inbox/`
  (Google's semantic visits are still nicer than raw GPS for "where was I"). Exports overlap; the pipeline de-duplicates.
- Strava → Settings → My Account → Download or Delete Your Account → Request your archive → `export_*.zip` into `inbox/strava/`.
- Then `./update.sh` locally, or just commit the inbox changes you want and let the Action build.

## Place names

A stop is named, in this order: `inbox/labels.json` (yours), `inbox/osm_places.json` (OpenStreetMap), then a role guessed
from how you use the place — airport, train station, overnight stay, drop-off spot, weekly Wednesday spot, regular errand…
(shown in italics). One-off stops are described by time and length ("Lunch stop in Gent", "Evening out in Antwerp").
Real OSM names come from the monthly Action: `pipeline/osm_names.py` looks up only places that are still unnamed
(Photon first, Nominatim at 1 request/s for the rest) and keeps `inbox/osm_places.json` in Drive, so each month only
new places are queried. It sends the coordinates of frequent stops (no times) to those two OSM services.
`pipeline/osm_names.html` does the same by hand in a browser via Overpass, if ever needed.

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
