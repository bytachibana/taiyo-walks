#!/usr/bin/env python3
"""
process_photos.py  —  Taiyo's photo gallery
===========================================
Processes new images dropped in photos_incoming/ into web-ready photos:
  - auto-orients (honours EXIF orientation)
  - reads GPS lat/lon + capture date from EXIF (for the map pin + caption)
  - resizes to a display image + a small thumbnail
  - STRIPS EXIF from the saved images (the coordinate lives only in photos.json)
  - updates photos.json (incremental — existing entries are preserved)
  - deletes each processed raw from photos_incoming/

Dependencies: Pillow, pillow-heif (for iPhone HEIC).
"""
import json, os, glob, sys
from PIL import Image, ImageOps, ExifTags

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()            # lets Pillow open iPhone .heic files
except Exception:
    pass

INCOMING     = 'photos_incoming'
OUT_DIR      = 'photos'
THUMB_DIR    = 'photos/thumbs'
MANIFEST     = 'photos.json'
DISPLAY_MAX  = 1600               # px, longest side of the full-view image
THUMB_MAX    = 400                # px, longest side of the thumbnail
EXTS         = ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp')


def gps_to_decimal(coord, ref):
    d, m, s = (float(coord[0]), float(coord[1]), float(coord[2]))
    val = d + m / 60.0 + s / 3600.0
    if str(ref).upper() in ('S', 'W'):
        val = -val
    return round(val, 6)


def extract_meta(img):
    """Return (lat, lon, date) from EXIF; any may be None/''."""
    lat = lon = None
    date = ''
    try:
        exif = img.getexif()
    except Exception:
        return lat, lon, date
    if not exif:
        return lat, lon, date

    # Capture date: EXIF sub-IFD DateTimeOriginal (36867), else main DateTime (306)
    try:
        sub = exif.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        sub = {}
    raw_dt = (sub.get(36867) if sub else None) or exif.get(306)
    if isinstance(raw_dt, str) and len(raw_dt) >= 10:
        date = raw_dt[:10].replace(':', '-')          # "YYYY:MM:DD" -> "YYYY-MM-DD"

    # GPS
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except Exception:
        gps = {}
    if gps and 2 in gps and 4 in gps:
        try:
            lat = gps_to_decimal(gps[2], gps.get(1, 'N'))
            lon = gps_to_decimal(gps[4], gps.get(3, 'E'))
        except Exception:
            lat = lon = None
    return lat, lon, date


def process_one(path):
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]
    out_name = stem + '.jpg'                            # normalise everything to jpg
    with Image.open(path) as im:
        lat, lon, date = extract_meta(im)
        im = ImageOps.exif_transpose(im).convert('RGB')  # auto-rotate, drop alpha

        disp = im.copy(); disp.thumbnail((DISPLAY_MAX, DISPLAY_MAX))
        disp.save(os.path.join(OUT_DIR, out_name), 'JPEG', quality=85)  # EXIF not passed -> stripped

        th = im.copy(); th.thumbnail((THUMB_MAX, THUMB_MAX))
        th.save(os.path.join(THUMB_DIR, out_name), 'JPEG', quality=80)

    entry = {
        'file':  f'{OUT_DIR}/{out_name}',
        'thumb': f'{THUMB_DIR}/{out_name}',
        'date':  date,
        'caption': date or stem,
    }
    if lat is not None and lon is not None:
        entry['lat'] = lat
        entry['lon'] = lon
    return out_name, entry


def main():
    os.makedirs(THUMB_DIR, exist_ok=True)

    # Load existing manifest so old photos' metadata survives (their EXIF is gone).
    by_name = {}
    if os.path.exists(MANIFEST):
        try:
            for e in json.load(open(MANIFEST)).get('photos', []):
                by_name[os.path.basename(e['file'])] = e
        except Exception:
            pass

    incoming = sorted(p for p in glob.glob(os.path.join(INCOMING, '*'))
                      if p.lower().endswith(EXTS))
    if not incoming:
        print('No new photos to process.')
        return

    for path in incoming:
        try:
            out_name, entry = process_one(path)
            by_name[out_name] = entry
            os.remove(path)                            # remove the raw from the repo
            loc = f"{entry.get('lat')},{entry.get('lon')}" if 'lat' in entry else 'no-gps'
            print(f"  processed {os.path.basename(path)} -> {out_name}  ({entry['date'] or 'no-date'}, {loc})")
        except Exception as e:
            print(f"  FAILED {os.path.basename(path)}: {e}")

    # Newest first (by date, then name).
    photos = sorted(by_name.values(),
                    key=lambda e: (e.get('date', ''), e['file']), reverse=True)
    with open(MANIFEST, 'w') as f:
        json.dump({'photos': photos}, f, indent=1)
    print(f"{len(photos)} photo(s) in manifest.")


if __name__ == '__main__':
    main()
