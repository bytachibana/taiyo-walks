"""
Danville, CA Road Network Analysis
===================================
Calculates total miles of roads in Danville using OpenStreetMap data.

Install dependencies first:
    pip install osmnx geopandas

Run:
    python danville_road_analysis.py
"""

import osmnx as ox
import geopandas as gpd

PLACE = "Danville, California, USA"

# ── Drivable roads ──────────────────────────────────────────────────────────
print("Fetching drivable road network from OpenStreetMap...")
G_drive = ox.graph_from_place(PLACE, network_type="drive")
edges_drive = ox.graph_to_gdfs(G_drive, nodes=False)
edges_drive['miles'] = edges_drive['length'] / 1609.34

# Explode list-type highway tags (OSM sometimes returns lists)
edges_drive = edges_drive.explode('highway')

total_drive = edges_drive['miles'].sum()
by_type_drive = edges_drive.groupby('highway')['miles'].sum().sort_values(ascending=False)

print(f"\n{'='*55}")
print(f"  DANVILLE, CA — DRIVABLE ROAD NETWORK")
print(f"{'='*55}")
print(f"  Total drivable miles:  {total_drive:.1f} mi")
print(f"  Total road segments:   {len(edges_drive):,}")
print(f"\n  By Road Type:")
print(f"  {'Type':<30} {'Miles':>8}")
print(f"  {'-'*40}")
for hw_type, miles in by_type_drive.items():
    pct = 100 * miles / total_drive
    print(f"  {str(hw_type):<30} {miles:>6.1f} mi  ({pct:.0f}%)")

# ── Walkable network (includes trails, paths, service roads) ─────────────────
print(f"\n\nFetching walkable network...")
G_walk = ox.graph_from_place(PLACE, network_type="walk")
edges_walk = ox.graph_to_gdfs(G_walk, nodes=False)
edges_walk['miles'] = edges_walk['length'] / 1609.34
total_walk = edges_walk['miles'].sum()

print(f"\n{'='*55}")
print(f"  DANVILLE, CA — WALKABLE NETWORK")
print(f"{'='*55}")
print(f"  Total walkable miles:  {total_walk:.1f} mi")
print(f"  (includes trails, paths, pedestrian ways)")

# ── Road types to include/exclude for the "walk every road" goal ─────────────
# These are sensible defaults — adjust to your preference
WALKABLE_ROAD_TYPES = [
    'residential', 'living_street', 'unclassified',
    'tertiary', 'tertiary_link',
    'secondary', 'secondary_link',
    'primary', 'primary_link',
    'service',         # parking lots, driveways — you may want to exclude
    'road',
    'footway', 'path', 'pedestrian', 'track',
]

EXCLUDE_TYPES = [
    'motorway', 'motorway_link',   # freeways — not walkable
    'trunk', 'trunk_link',         # arterials without sidewalks
]

drive_filtered = edges_drive[edges_drive['highway'].isin(WALKABLE_ROAD_TYPES)]
total_goal = drive_filtered['miles'].sum()

print(f"\n{'='*55}")
print(f"  YOUR GOAL: Walk Every Road in Danville")
print(f"{'='*55}")
print(f"  Recommended target (excl. freeways): {total_goal:.1f} mi")
print(f"  (excludes motorways and trunk roads)")
print()
print(f"  At 2 miles/walk, 5x per week:")
print(f"    = {total_goal / (2 * 5 * 52):.1f} years to complete")
print(f"  At 3 miles/walk, 5x per week:")
print(f"    = {total_goal / (3 * 5 * 52):.1f} years to complete")
print()

# ── Export GeoJSON for the website ──────────────────────────────────────────
print("Exporting road network as GeoJSON for the website...")
edges_drive_export = edges_drive[['geometry', 'highway', 'name', 'miles', 'length']].copy()
edges_drive_export['walked'] = False  # placeholder — your app will update this
edges_drive_export['segment_id'] = range(len(edges_drive_export))
edges_drive_export.to_file("danville_roads.geojson", driver="GeoJSON")
print(f"Saved: danville_roads.geojson ({len(edges_drive_export):,} road segments)")
print("Load this file into the website map as the base layer.")
