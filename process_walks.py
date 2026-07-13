#!/usr/bin/env python3
"""
process_walks.py  —  Taiyo's Danville Walk Tracker
====================================================
Drop new GPX files into the walks/ folder, then run:
    python process_walks.py

Outputs progress.json which the map reads automatically.

Dependencies:
    pip install shapely
    (no osmnx needed after first setup — roads.geojson is static)
"""

import json, math, os, re, sys, glob, xml.etree.ElementTree as ET
from shapely.geometry import LineString
from shapely.ops import unary_union

# ── Config ────────────────────────────────────────────────────────────────────
ROADS_FILE   = 'roads.geojson'   # static, generated once by danville_road_analysis.py
GPX_DIR      = 'walks'           # drop new .gpx files here
OUTPUT_FILE  = 'progress.json'   # read by the map
BUFFER_M     = 15                # meters from GPS track to count road as walked
EXCLUDE_HW   = {'motorway', 'motorway_link'}
CENTER_LAT   = 37.82
# ─────────────────────────────────────────────────────────────────────────────

ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
METERS_PER_DEG = 111320
buf_deg = BUFFER_M / (METERS_PER_DEG * math.cos(math.radians(CENTER_LAT)))


def haversine(a, b):
    R = 3958.8
    dlat = math.radians(b[1] - a[1])
    dlon = math.radians(b[0] - a[0])
    x = math.sin(dlat/2)**2 + math.cos(math.radians(a[1])) * math.cos(math.radians(b[1])) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(x))


def load_gpx(path):
    tree = ET.parse(path)
    root = tree.getroot()
    name_el = root.find('.//gpx:name', ns)
    time_el = root.find('.//gpx:time', ns)
    pts     = root.findall('.//gpx:trkpt', ns)
    coords  = [(float(p.attrib['lon']), float(p.attrib['lat'])) for p in pts]
    if len(coords) < 2:
        return None
    dist = sum(haversine(coords[i], coords[i+1]) for i in range(len(coords) - 1))
    # Display label: strip "Walking " prefix and any trailing clock time
    # (e.g. "6/22/26 7:48 am" -> "6/22/26") so the public map shows day only.
    label = (name_el.text if name_el is not None else os.path.basename(path)).replace('Walking ', '')
    label = re.sub(r'\s+\d{1,2}:\d{2}\s*[ap]m$', '', label, flags=re.I)
    # Date comes from the filename (local date, YYYY-MM-DD), NOT the GPX <time>:
    # that timestamp is UTC and rolls to the next day for evening Pacific walks,
    # which made the gray subdate disagree with the walk's local-date label.
    fname_match = re.match(r'(\d{4}-\d{2}-\d{2})', os.path.basename(path))
    date = fname_match.group(1) if fname_match else (time_el.text[:10] if time_el is not None else '')
    return {
        'name'  : label,
        'date'  : date,
        'miles' : round(dist, 2),
        'coords': [[round(c[0], 5), round(c[1], 5)] for c in coords],
        '_line' : LineString(coords),
    }


def extract_linestrings(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == 'LineString':
        return [list(geom.coords)]
    if geom.geom_type in ('MultiLineString', 'GeometryCollection'):
        out = []
        for g in geom.geoms:
            out.extend(extract_linestrings(g))
        return out
    return []


def line_miles(coords):
    return sum(haversine(coords[i], coords[i+1]) for i in range(len(coords) - 1))


def main():
    # ── Load GPX files ────────────────────────────────────────────────────────
    gpx_paths = sorted(glob.glob(os.path.join(GPX_DIR, '*.gpx')))
    if not gpx_paths:
        print(f"No GPX files found in ./{GPX_DIR}/  — add some and re-run.")
        sys.exit(1)

    print(f"Found {len(gpx_paths)} GPX file(s)...")
    walks = []
    buffers = []
    for path in gpx_paths:
        w = load_gpx(path)
        if w:
            buffers.append(w['_line'].buffer(buf_deg))
            meta = {k: v for k, v in w.items() if k != '_line'}
            walks.append(meta)
            print(f"  {w['name']}  {w['date']}  {w['miles']} mi")

    print(f"\nBuilding walked-area union...")
    walked_area = unary_union(buffers)

    # ── Load roads ────────────────────────────────────────────────────────────
    if not os.path.exists(ROADS_FILE):
        print(f"ERROR: {ROADS_FILE} not found. Run danville_road_analysis.py first.")
        sys.exit(1)

    print(f"Loading {ROADS_FILE}...")
    with open(ROADS_FILE) as f:
        roads = json.load(f)

    # ── Clip each road segment ────────────────────────────────────────────────
    print(f"Clipping {len(roads['features'])} segments against walked area...")
    walked_feats   = []
    unwalked_feats = []
    total_mi = 0.0
    walked_mi = 0.0

    for ft in roads['features']:
        hw = ft['properties'].get('highway', '')
        if hw in EXCLUDE_HW:
            continue
        raw = ft['geometry']['coordinates']
        if len(raw) < 2:
            continue

        road_line = LineString(raw)
        name  = ft['properties'].get('name', '')
        seg_id = ft['properties'].get('segment_id', 0)
        props = {'n': name, 'h': hw, 'id': seg_id}
        seg_mi = ft['properties'].get('miles', line_miles(raw))
        total_mi += seg_mi

        for coords in extract_linestrings(road_line.intersection(walked_area)):
            if len(coords) < 2:
                continue
            mi = line_miles(coords)
            walked_mi += mi
            walked_feats.append({
                'type': 'Feature',
                'geometry': {'type': 'LineString', 'coordinates': [[round(c[0],5), round(c[1],5)] for c in coords]},
                'properties': {**props, 'w': 1, 'mi': round(mi, 4)}
            })

        for coords in extract_linestrings(road_line.difference(walked_area)):
            if len(coords) < 2:
                continue
            mi = line_miles(coords)
            unwalked_feats.append({
                'type': 'Feature',
                'geometry': {'type': 'LineString', 'coordinates': [[round(c[0],5), round(c[1],5)] for c in coords]},
                'properties': {**props, 'w': 0, 'mi': round(mi, 4)}
            })

    pct = round(100 * walked_mi / total_mi, 1) if total_mi > 0 else 0

    # ── Write output ──────────────────────────────────────────────────────────
    output = {
        'walked_features'  : walked_feats,
        'unwalked_features': unwalked_feats,
        'walks'            : walks,
        'stats': {
            'walked_miles': round(walked_mi, 2),
            'total_miles' : round(total_mi, 1),
            'pct'         : pct,
            'miles_left'  : round(total_mi - walked_mi, 1),
            'walk_count'  : len(walks),
        }
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, separators=(',', ':'))

    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\n{'='*48}")
    print(f"  Walks processed : {len(walks)}")
    print(f"  Total road miles: {total_mi:.1f}")
    print(f"  Miles walked    : {walked_mi:.2f}  ({pct}%)")
    print(f"  Miles remaining : {total_mi - walked_mi:.1f}")
    print(f"  Output          : {OUTPUT_FILE}  ({size_kb:.0f} KB)")
    print(f"{'='*48}")
    print(f"\nDone! Open index.html in Chrome to see updated progress.")


if __name__ == '__main__':
    main()
