"""
importer.py - load NS-3 simulation output into the SQLite database.

Each NS-3 run writes a folder containing:
    run.json      parameters + summary
    reports.csv   reports delivered to the hub
    flows.csv     FlowMonitor statistics per UDP flow
    events.csv    event timeline
    trace.csv     drone positions / link states every 0.5 s (replay)
    pois.csv      hidden findings in the zone (replay)

Usage:
    python importer.py ../data/sample_runs/baseline_adaptive          # one run
    python importer.py --all ../data/sample_runs                      # every sub-folder
    python importer.py --all ../ns3_output --reset                    # wipe DB first

Re-importing a run with the same name replaces the old copy.
"""
import argparse
import csv
import json
import os
import sys

import db


def _num(v, cast=float, default=None):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def import_run(conn, folder, name=None):
    run_json = os.path.join(folder, "run.json")
    if not os.path.isfile(run_json):
        raise FileNotFoundError(f"{run_json} not found - is this an NS-3 output folder?")
    with open(run_json, encoding="utf-8") as f:
        meta = json.load(f)
    name = name or os.path.basename(os.path.normpath(folder))

    conn.execute("DELETE FROM runs WHERE name = ?", (name,))   # cascades to child tables
    cur = conn.execute(
        """INSERT INTO runs (name, scenario, relay_mode, drone_range_m, tower_range_m, loss_base,
                             image_bytes, seed, tower_down_start, tower_down_end, captured, delivered,
                             avg_latency_s, peak_primary_queue, peak_relay_queue, sim_end_s)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (name, meta.get("scenario"), meta.get("relay_mode"), meta.get("drone_range_m"),
         meta.get("tower_range_m"), meta.get("loss_base"), meta.get("image_bytes"), meta.get("seed"),
         meta.get("tower_down_start"), meta.get("tower_down_end"), meta.get("captured"),
         meta.get("delivered"), meta.get("avg_latency_s"), meta.get("peak_primary_queue"),
         meta.get("peak_relay_queue"), meta.get("sim_end_s")))
    run_id = cur.lastrowid

    counts = {"reports": 0, "flows": 0, "events": 0, "trace": 0}

    path = os.path.join(folder, "reports.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                conn.execute(
                    """INSERT INTO reports (run_id, pkt_id, finding, severity, lat, lon, captured_s,
                                            received_s, latency_s, tx_attempts)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, r["id"], r["finding"], _num(r["severity"], int), _num(r["lat"]),
                     _num(r["lon"]), _num(r["captured_s"]), _num(r["received_s"]),
                     _num(r["latency_s"]), _num(r["tx_attempts"], int)))
                counts["reports"] += 1

    path = os.path.join(folder, "flows.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                conn.execute(
                    """INSERT INTO flows (run_id, flow_id, src, dst, tx_packets, rx_packets, lost_packets,
                                          tx_bytes, rx_bytes, mean_delay_ms, throughput_kbps)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, _num(r["flow_id"], int), r["src"], r["dst"], _num(r["tx_packets"], int),
                     _num(r["rx_packets"], int), _num(r["lost_packets"], int), _num(r["tx_bytes"], int),
                     _num(r["rx_bytes"], int), _num(r["mean_delay_ms"]), _num(r["throughput_kbps"])))
                counts["flows"] += 1

    path = os.path.join(folder, "events.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = [(run_id, _num(r["time_s"]), r["node"], r["event"], r["detail"])
                    for r in csv.DictReader(f)]
        conn.executemany("INSERT INTO events (run_id, time_s, node, event, detail) VALUES (?,?,?,?,?)", rows)
        counts["events"] = len(rows)

    path = os.path.join(folder, "trace.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = [(run_id, _num(r["time_s"]), _num(r["px"]), _num(r["py"]), _num(r["rx"]), _num(r["ry"]),
                     r["pstate"], _num(r["pq"], int), _num(r["rq"], int), _num(r["link_pr"], int),
                     _num(r["link_rt"], int), _num(r["tower_up"], int), _num(r["delivered"], int),
                     _num(r["captured"], int)) for r in csv.DictReader(f)]
        conn.executemany("""INSERT INTO trace (run_id, time_s, px, py, rx, ry, pstate, pq, rq, link_pr, link_rt,
                                               tower_up, delivered, captured)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        counts["trace"] = len(rows)

    path = os.path.join(folder, "pois.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = [(run_id, _num(r["poi_id"], int), _num(r["x"]), _num(r["y"]), r["finding"],
                     _num(r["severity"], int), _num(r["found_s"])) for r in csv.DictReader(f)]
        conn.executemany("INSERT INTO pois (run_id, poi_id, x, y, finding, severity, found_s) VALUES (?,?,?,?,?,?,?)", rows)

    conn.commit()
    return name, counts


def main():
    ap = argparse.ArgumentParser(description="Import NS-3 output into SQLite")
    ap.add_argument("path", help="run folder, or parent folder when --all is used")
    ap.add_argument("--all", action="store_true", help="import every sub-folder that contains run.json")
    ap.add_argument("--name", help="run name (single-run import only; default = folder name)")
    ap.add_argument("--reset", action="store_true", help="delete the database before importing")
    args = ap.parse_args()

    if args.reset and os.path.isfile(db.DB_PATH):
        os.remove(db.DB_PATH)
    conn = db.connect()
    db.init_db(conn)

    folders = []
    if args.all:
        for entry in sorted(os.listdir(args.path)):
            p = os.path.join(args.path, entry)
            if os.path.isfile(os.path.join(p, "run.json")):
                folders.append(p)
    else:
        folders = [args.path]
    if not folders:
        sys.exit(f"No run folders (with run.json) found in {args.path}")

    for p in folders:
        name, c = import_run(conn, p, None if args.all else args.name)
        print(f"imported {name}: {c['reports']} reports, {c['flows']} flows, {c['events']} events, {c['trace']} trace rows")
    print(f"database: {db.DB_PATH}")


if __name__ == "__main__":
    main()
