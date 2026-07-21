"""
Danville, CA Road + Trail Network Generator
===========================================
Produces roads.geojson — the static base network for the walk tracker.

Run:
    pip install osmnx geopandas
    python danville_road_analysis.py

TWO THINGS THIS SCRIPT GETS RIGHT (both were bugs in the original version):

1. UNDIRECTED graph. osmnx's routing graphs are directed: every two-way street
   is stored as TWO edges (one per travel direction). Summing those double-counts
   the network (~1.8x — the old file showed 350.9 mi instead of ~194 mi).
   `to_undirected()` collapses reciprocal pairs so each street is counted once.

2. TRAILS included. network_type="drive" excludes all paths, so the Iron Horse
   Regional Trail was missing entirely. We now query roads + cycleway + path.

FILTER RATIONALE (validated against real OSM tags in Danville, not assumed):
  - cycleway  -> KEEP ALL. These are the paved multi-use trails here; the Iron
                 Horse Trail is tagged highway=cycleway (47 edges / 4.2 mi).
                 Its `surface` tag is inconsistent (paved / asphalt / missing),
                 so filtering cycleway by surface would silently drop parts of it.
  - path      -> KEEP only if paved AND >= MIN_TRAIL_M (drops dirt hiking
                 tracks and tiny connectors).
  - footway   -> EXCLUDED. In this suburb footway is overwhelmingly sidewalks
                 (92.5 mi across 4,570 edges). Including them would balloon the
                 total to ~300 mi and bury the map in sidewalk clutter — and you
                 already cover a street's sidewalk by walking the street.
  - motorway  -> not requested (freeways aren't walkable). process_walks.py also
                 excludes them via EXCLUDE_HW.

Expected result: ~205-210 walkable miles.
"""

import osmnx as ox

PLACE      = "Danville, California, USA"
OUT_FILE   = "roads.geojson"
MIN_TRAIL_M = 50          # drop trail stubs shorter than this (metres)

ROAD_TYPES = {
    'residential', 'living_street', 'unclassified',
    'tertiary', 'tertiary_link',
    'secondary', 'secondary_link',
    'primary', 'primary_link',
}

# Roads + trail candidates in one query. footway is deliberately NOT requested.
CUSTOM_FILTER = (
    '["highway"~"residential|living_street|unclassified|'
    'tertiary|tertiary_link|secondary|secondary_link|'
    'primary|primary_link|cycleway|path"]'
)


def first(x):
    """OSM tags are sometimes lists — take the first value, always return str."""
    if isinstance(x, list):
        x = x[0] if x else ''
    return '' if x is None else str(x)


def classify_highway(hw):
    """Collapse a possibly list-valued highway tag to ONE type, roads first.

    osmnx merges consecutive segments when it simplifies the graph, so an edge
    can carry several highway values, e.g. ['path', 'residential'] for a court
    that joins a footpath. Naively taking element [0] classified such edges as
    'path', and the paved-surface rule for paths then DROPPED them — which is
    how Cannes Ct / Murcia Ct went missing.

    Precedence: any road type > cycleway > whatever's left. A merged edge that
    contains a road segment is a road we must keep.
    """
    vals = hw if isinstance(hw, list) else [hw]
    vals = [str(v) for v in vals if v]
    for v in vals:
        if v in ROAD_TYPES:
            return v
    for v in vals:
        if v == 'cycleway':
            return v
    return vals[0] if vals else ''


def is_paved(surface):
    if isinstance(surface, list):
        surface = ' '.join(str(s) for s in surface)
    s = str(surface).lower()
    return any(k in s for k in ('paved', 'asphalt', 'concrete'))


def main():
    ox.settings.useful_tags_way = list(set(ox.settings.useful_tags_way + ['surface']))

    print(f"Fetching roads + trails for {PLACE} ...")
    G = ox.graph_from_place(PLACE, custom_filter=CUSTOM_FILTER, retain_all=True)

    # Collapse reciprocal edge pairs — THE mileage fix.
    try:
        G = ox.convert.to_undirected(G)     # osmnx >= 2.0
    except AttributeError:
        G = ox.get_undirected(G)            # osmnx 1.x
    print(f"Undirected graph: {G.number_of_edges():,} edges")

    e = ox.graph_to_gdfs(G, nodes=False).reset_index()
    e['miles'] = e['length'] / 1609.34
    e['hw']    = e['highway'].apply(classify_highway)
    e['nm']    = e['name'].apply(first) if 'name' in e.columns else ''
    surf       = e['surface'] if 'surface' in e.columns else None

    is_road    = e['hw'].isin(ROAD_TYPES)
    is_cycle   = e['hw'] == 'cycleway'
    paved_mask = surf.apply(is_paved) if surf is not None else False
    is_path    = (e['hw'] == 'path') & paved_mask & (e['length'] >= MIN_TRAIL_M)

    keep = e[is_road | is_cycle | is_path].copy()

    # Fresh per-feature fields for the exported baseline
    out = keep[['geometry', 'miles', 'length']].copy()
    out['highway']    = keep['hw']
    out['name']       = keep['nm']
    out['walked']     = False
    out['segment_id'] = range(len(out))

    out.to_file(OUT_FILE, driver="GeoJSON")

    # ── Report ────────────────────────────────────────────────────────────────
    total = out['miles'].sum()
    print(f"\n{'='*52}")
    print(f"  Saved {OUT_FILE}: {len(out):,} segments, {total:.1f} mi")
    print(f"{'='*52}")
    print("  Miles by type:")
    for hw, mi in out.groupby('highway')['miles'].sum().sort_values(ascending=False).items():
        print(f"    {hw:<18} {mi:>7.1f} mi")
    ih = out[out['name'].str.contains('Iron Horse', case=False, na=False)]
    print(f"\n  Iron Horse Trail: {len(ih)} segments, {ih['miles'].sum():.1f} mi")
    if total > 260:
        print("\n  WARNING: total looks high — sidewalks/paths may have leaked in.")
    if len(ih) == 0:
        print("\n  WARNING: Iron Horse Trail missing — check the filter.")


if __name__ == '__main__':
    main()
