#!/usr/bin/env python3
"""
Preprocess Toei Bus GTFS data:
1. Filters trips to standard regular services defined in calendar.txt (170: Weekday, 160: Saturday, 100: Holiday).
   This eliminates duplicate departures caused by calendar date exceptions (171, 172, 179, etc.).
2. Applies Google Maps drop-off/pickup rules:
   - Excludes terminal stop of each trip from departure timetables (last stop is drop-off only).
   - Excludes pickup_type == '1' (no pickup / drop-off only).
   - Deduplicates multiple records for the same parent stop and same trip.
3. Splits into 23 Wards and Tama (Ome) areas:
   - data/metadata_23.json
   - data/metadata_tama.json
4. Generates area route zip archives containing each route's JSON:
   - data/routes_23.zip (contains {route_id}.json for 23 Wards)
   - data/routes_tama.zip (contains {route_id}.json for Tama area)
   This allows extracting route JSON on demand via JSZip while keeping repository file counts minimal.
"""

import os
import zipfile
import csv
import io
import json
from collections import defaultdict

GTFS_ZIP = 'ToeiBus-GTFS.zip'
DATA_DIR = 'data'

TAMA_ROUTE_IDS = {'280', '281', '282', '283', '285', '286', '287', '288', '289', '291'}

def main():
    print("Opening GTFS archive...")
    with zipfile.ZipFile(GTFS_ZIP) as z:
        print("Reading calendar.txt...")
        cal_reader = csv.DictReader(io.StringIO(z.read('calendar.txt').decode('utf-8')))
        # Valid standard services
        standard_services = {}
        for row in cal_reader:
            sid = row['service_id']
            if sid.endswith('-170'):
                standard_services[sid] = 0  # Weekday
            elif sid.endswith('-160'):
                standard_services[sid] = 1  # Saturday
            elif sid.endswith('-100'):
                standard_services[sid] = 2  # Holiday
        print(f"Loaded {len(standard_services)} standard regular service IDs")

        print("Reading routes.txt...")
        routes_raw = list(csv.DictReader(io.StringIO(z.read('routes.txt').decode('utf-8'))))
        routes_map = {}
        for r in routes_raw:
            rid = r['route_id']
            color = ('#' + r['route_color']) if r.get('route_color') else '#3182ce'
            text_color = ('#' + r['route_text_color']) if r.get('route_text_color') else '#ffffff'
            routes_map[rid] = {
                'id': rid,
                'n': r['route_short_name'] or r['route_long_name'],
                'c': color,
                'tc': text_color,
                'sh': set(),
                'st': set()
            }
        print(f"Loaded {len(routes_map)} routes")

        print("Reading stops.txt...")
        stops_raw = list(csv.DictReader(io.StringIO(z.read('stops.txt').decode('utf-8'))))
        stops_meta = {}      # parent_id -> {n, lat, lon, r: set()}
        child_to_parent = {} # child_stop_id -> parent_id
        parent_stops_raw = {}
        child_stops_raw = []

        for s in stops_raw:
            sid = s['stop_id']
            loc_type = s.get('location_type', '').strip()
            parent = s.get('parent_station', '').strip()
            if loc_type == '1' or not parent:
                parent_stops_raw[sid] = s
            else:
                child_stops_raw.append(s)

        for s in parent_stops_raw.values():
            sid = s['stop_id']
            stops_meta[sid] = {
                'n': s['stop_name'],
                'lat': round(float(s['stop_lat']), 5),
                'lon': round(float(s['stop_lon']), 5),
                'r': set()
            }
            child_to_parent[sid] = sid

        for s in child_stops_raw:
            sid = s['stop_id']
            parent = s.get('parent_station', '').strip()
            if parent in stops_meta:
                child_to_parent[sid] = parent
            else:
                # Standalone child stop
                stops_meta[sid] = {
                    'n': s['stop_name'],
                    'lat': round(float(s['stop_lat']), 5),
                    'lon': round(float(s['stop_lon']), 5),
                    'r': set()
                }
                child_to_parent[sid] = sid
        print(f"Loaded {len(stops_meta)} parent stops and mapped {len(child_to_parent)} stop IDs")

        print("Reading shapes.txt...")
        shapes_raw = csv.DictReader(io.StringIO(z.read('shapes.txt').decode('utf-8')))
        shapes_map = defaultdict(list)
        for row in shapes_raw:
            shapes_map[row['shape_id']].append((
                int(row['shape_pt_sequence']),
                round(float(row['shape_pt_lat']), 5),
                round(float(row['shape_pt_lon']), 5)
            ))
        # Sort shapes by sequence
        shapes_clean = {}
        for sh_id, pts in shapes_map.items():
            pts.sort(key=lambda x: x[0])
            shapes_clean[sh_id] = [[p[1], p[2]] for p in pts]
        print(f"Loaded {len(shapes_clean)} shapes")

        print("Reading trips.txt...")
        trips_raw = list(csv.DictReader(io.StringIO(z.read('trips.txt').decode('utf-8'))))
        valid_trips = {}
        for t in trips_raw:
            sid = t['service_id']
            if sid in standard_services:
                valid_trips[t['trip_id']] = t
        print(f"Filtered {len(valid_trips)} standard trips (from {len(trips_raw)} total trips)")

        print("Reading stop_times.txt...")
        st_reader = csv.DictReader(io.StringIO(z.read('stop_times.txt').decode('utf-8')))
        trip_stop_times = defaultdict(list)
        for row in st_reader:
            tid = row['trip_id']
            if tid in valid_trips:
                trip_stop_times[tid].append(row)
        print(f"Grouped stop_times for {len(trip_stop_times)} trips")

    # Sort stop times for each trip
    for tid, st_list in trip_stop_times.items():
        st_list.sort(key=lambda x: int(x['stop_sequence']))

    # Prepare data per route
    routes_output = {rid: {'t': {}, 'tb': defaultdict(lambda: {0: [], 1: [], 2: []})} for rid in routes_map}

    print("Processing trips and timetables...")
    for tid, t in valid_trips.items():
        rid = t['route_id']
        st_list = trip_stop_times.get(tid)
        if not st_list:
            continue

        day_type = standard_services[t['service_id']]
        headsign = t['trip_headsign']
        shape_id = t['shape_id']

        # Record route shapes
        if shape_id and shape_id in shapes_clean:
            routes_map[rid]['sh'].add(shape_id)

        # Build full trip timeline
        trip_timeline = []
        for r in st_list:
            stop_id = r['stop_id']
            pid = child_to_parent.get(stop_id, stop_id)
            stop_name = stops_meta[pid]['n'] if pid in stops_meta else ''
            arr = r['arrival_time'][:5]
            dep = r['departure_time'][:5]
            trip_timeline.append([stop_name, arr, dep, pid])
            # Link stop with route
            if pid in stops_meta:
                stops_meta[pid]['r'].add(rid)
                routes_map[rid]['st'].add(pid)

        routes_output[rid]['t'][tid] = [day_type, headsign, shape_id, trip_timeline]

        # Build stop departure timetables (tb)
        # Google Maps specifications:
        # - Exclude the terminal stop (last stop, drop-off only)
        # - Exclude stops with pickup_type == '1' (drop-off only)
        # - Exclude multiple departure entries for the same parent stop on the same trip
        n_stops = len(st_list)
        visited_pids_this_trip = set()

        for idx, r in enumerate(st_list):
            if idx == n_stops - 1:
                # Terminal stop is drop-off only
                continue
            if r.get('pickup_type') == '1':
                # No boarding allowed
                continue

            stop_id = r['stop_id']
            pid = child_to_parent.get(stop_id, stop_id)
            if pid in visited_pids_this_trip:
                # Deduplicate same parent stop on the same trip
                continue
            visited_pids_this_trip.add(pid)

            dep = r['departure_time'][:5]
            routes_output[rid]['tb'][pid][day_type].append([dep, tid, headsign])

    # Build route JSON contents
    print("Serializing route JSON contents...")
    route_json_strings = {}
    for rid, rdata in routes_output.items():
        tb_dict = {}
        for pid, days in rdata['tb'].items():
            tb_dict[pid] = {}
            for d in [0, 1, 2]:
                rows = days[d]
                rows.sort(key=lambda x: x[0])
                tb_dict[pid][str(d)] = rows

        route_file_data = {
            't': rdata['t'],
            'tb': tb_dict
        }
        route_json_strings[rid] = json.dumps(route_file_data, ensure_ascii=False, separators=(',', ':'))

    # Save to data/routes_23.zip and data/routes_tama.zip
    print("Writing routes_23.zip and routes_tama.zip...")
    zip_23_path = os.path.join(DATA_DIR, 'routes_23.zip')
    zip_tama_path = os.path.join(DATA_DIR, 'routes_tama.zip')

    with zipfile.ZipFile(zip_23_path, 'w', compression=zipfile.ZIP_DEFLATED) as z23, \
         zipfile.ZipFile(zip_tama_path, 'w', compression=zipfile.ZIP_DEFLATED) as z_tama:
        for rid, content in route_json_strings.items():
            fname = f"{rid}.json"
            if rid in TAMA_ROUTE_IDS:
                z_tama.writestr(fname, content)
            else:
                z23.writestr(fname, content)

    print(f"Generated {zip_23_path} ({os.path.getsize(zip_23_path)/1024:.1f} KB)")
    print(f"Generated {zip_tama_path} ({os.path.getsize(zip_tama_path)/1024:.1f} KB)")

    # Separate metadata into 23 Wards vs Tama
    print("Partitioning metadata into 23 Wards and Tama areas...")
    meta_23 = {'stops': {}, 'routes': {}, 'shapes': {}}
    meta_tama = {'stops': {}, 'routes': {}, 'shapes': {}}

    for rid, rinfo in routes_map.items():
        is_tama = (rid in TAMA_ROUTE_IDS)
        target = meta_tama if is_tama else meta_23

        target['routes'][rid] = {
            'id': rinfo['id'],
            'n': rinfo['n'],
            'c': rinfo['c'],
            'tc': rinfo['tc'],
            'sh': sorted(list(rinfo['sh'])),
            'st': sorted(list(rinfo['st']))
        }

        # Include shapes
        for sh_id in rinfo['sh']:
            if sh_id in shapes_clean:
                target['shapes'][sh_id] = shapes_clean[sh_id]

    for pid, sinfo in stops_meta.items():
        r_list = sorted(list(sinfo['r']))
        if not r_list:
            continue
        is_tama = any(rid in TAMA_ROUTE_IDS for rid in r_list)
        target = meta_tama if is_tama else meta_23
        target['stops'][pid] = {
            'n': sinfo['n'],
            'lat': sinfo['lat'],
            'lon': sinfo['lon'],
            'r': r_list
        }

    meta_23_path = os.path.join(DATA_DIR, 'metadata_23.json')
    meta_tama_path = os.path.join(DATA_DIR, 'metadata_tama.json')

    with open(meta_23_path, 'w', encoding='utf-8') as f:
        json.dump(meta_23, f, ensure_ascii=False, separators=(',', ':'))

    with open(meta_tama_path, 'w', encoding='utf-8') as f:
        json.dump(meta_tama, f, ensure_ascii=False, separators=(',', ':'))

    print(f"Generated {meta_23_path}: {len(meta_23['stops'])} stops, {len(meta_23['routes'])} routes, {len(meta_23['shapes'])} shapes, size: {os.path.getsize(meta_23_path)/1024:.1f} KB")
    print(f"Generated {meta_tama_path}: {len(meta_tama['stops'])} stops, {len(meta_tama['routes'])} routes, {len(meta_tama['shapes'])} shapes, size: {os.path.getsize(meta_tama_path)/1024:.1f} KB")

if __name__ == '__main__':
    main()
