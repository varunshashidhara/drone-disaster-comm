"""
app.py - Flask backend for the NGO / rescue-team dashboard.

Run:   python app.py        then open http://127.0.0.1:5000

The API only reads from SQLite (filled by importer.py) and lets the NGO change a
report's status (new -> dispatched -> resolved).
"""
import os
import sys
import re
import subprocess
import threading
import time

from flask import Flask, abort, g, jsonify, request, send_from_directory

import db
import importer

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))
app = Flask(__name__, static_folder=os.path.join(WEB_DIR, "static"), static_url_path="/static")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SIM_BIN = os.environ.get("SIM_BIN", os.path.join(ROOT_DIR, "ns3", "drone_disaster_sim"))
PY_SIM = os.path.join(ROOT_DIR, "ns3", "drone_disaster_sim.py")
OUT_ROOT = os.environ.get("NS3_OUTPUT", os.path.join(ROOT_DIR, "ns3_output"))
_sim_lock = threading.Lock()

# Geometry used by the NS-3 simulator (metres). Keep in sync with drone_disaster_sim.cc.
LAYOUT = {"zone": {"w": 600, "h": 700}, "base": {"x": 60, "y": 760},
          "tower": {"x": 1100, "y": 350}, "hub": {"x": 1300, "y": 350}, "cam_r": 60}

SEVERITY = {3: "Critical", 2: "High", 1: "Moderate"}
STATUSES = {"new", "dispatched", "resolved"}


# ----------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = db.connect()
        db.init_db(g.db)          # harmless if the tables already exist
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def report_dict(r):
    d = dict(r)
    d["severity_label"] = SEVERITY.get(d["severity"], "Unknown")
    d["retransmissions"] = max(0, (d.get("tx_attempts") or 3) - 3)
    return d


# ---------------------------------------------------------------------- pages
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/demo")
def demo():
    return send_from_directory(os.path.join(ROOT_DIR, "demo"), "drone-disaster-comm-sim.html")


# ------------------------------------------------------------------------ API
@app.route("/api/runs")
def api_runs():
    cur = get_db().execute("SELECT * FROM runs ORDER BY id")
    return jsonify(rows(cur))


@app.route("/api/runs/<int:run_id>/summary")
def api_summary(run_id):
    conn = get_db()
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        abort(404)
    agg = conn.execute(
        """SELECT COUNT(*) AS delivered,
                  AVG(latency_s) AS avg_latency_s, MIN(latency_s) AS min_latency_s,
                  MAX(latency_s) AS max_latency_s,
                  SUM(CASE WHEN tx_attempts > 3 THEN tx_attempts - 3 ELSE 0 END) AS retransmissions,
                  SUM(status = 'dispatched') AS dispatched, SUM(status = 'resolved') AS resolved
           FROM reports WHERE run_id = ?""", (run_id,)).fetchone()
    by_sev = rows(conn.execute(
        """SELECT severity, COUNT(*) AS n, AVG(latency_s) AS avg_latency_s
           FROM reports WHERE run_id = ? GROUP BY severity ORDER BY severity DESC""", (run_id,)))
    for s in by_sev:
        s["label"] = SEVERITY.get(s["severity"], "Unknown")
    out = dict(run)
    out.update({k: (agg[k] or 0) for k in agg.keys()})
    out["captured"] = run["captured"]
    out["delivery_ratio"] = (agg["delivered"] / run["captured"]) if run["captured"] else 0
    out["by_severity"] = by_sev
    return jsonify(out)


@app.route("/api/runs/<int:run_id>/reports")
def api_reports(run_id):
    sql, args = "SELECT * FROM reports WHERE run_id = ?", [run_id]
    if request.args.get("severity"):
        sql += " AND severity = ?"
        args.append(int(request.args["severity"]))
    if request.args.get("status") in STATUSES:
        sql += " AND status = ?"
        args.append(request.args["status"])
    sql += " ORDER BY severity DESC, received_s ASC"
    cur = get_db().execute(sql, args)
    return jsonify([report_dict(r) for r in cur.fetchall()])


@app.route("/api/reports/<int:report_id>/status", methods=["POST"])
def api_set_status(report_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in STATUSES:
        abort(400, description="status must be one of: new, dispatched, resolved")
    conn = get_db()
    conn.execute(
        "UPDATE reports SET status = ?, dispatched_at = CASE WHEN ? = 'new' THEN NULL "
        "ELSE COALESCE(dispatched_at, datetime('now')) END WHERE id = ?",
        (status, status, report_id))
    conn.commit()
    r = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if r is None:
        abort(404)
    return jsonify(report_dict(r))


@app.route("/api/runs/<int:run_id>/flows")
def api_flows(run_id):
    out = []
    for r in get_db().execute("SELECT * FROM flows WHERE run_id = ? ORDER BY flow_id", (run_id,)):
        d = dict(r)
        d["kind"] = "data" if d["tx_packets"] and d["tx_bytes"] / d["tx_packets"] > 200 else "ack"
        d["loss_pct"] = round(100.0 * d["lost_packets"] / d["tx_packets"], 1) if d["tx_packets"] else 0.0
        out.append(d)
    return jsonify(out)


@app.route("/api/runs/<int:run_id>/events")
def api_events(run_id):
    cur = get_db().execute("SELECT * FROM events WHERE run_id = ? ORDER BY time_s, id", (run_id,))
    return jsonify(rows(cur))


@app.route("/api/runs/<int:run_id>/replay")
def api_replay(run_id):
    """Movement trace + hidden findings for the replay player (compact array format)."""
    conn = get_db()
    if conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone() is None:
        abort(404)
    trace = [list(r) for r in conn.execute(
        """SELECT time_s, px, py, rx, ry, pstate, pq, rq, link_pr, link_rt, tower_up, delivered, captured
           FROM trace WHERE run_id = ? ORDER BY time_s, id""", (run_id,))]
    pois = rows(conn.execute(
        "SELECT poi_id, x, y, finding, severity, found_s FROM pois WHERE run_id = ? ORDER BY poi_id", (run_id,)))
    return jsonify({"layout": LAYOUT, "trace": trace, "pois": pois})


def _param(data, key, lo, hi, default, cast=float):
    """Read a numeric parameter from the request and check its range."""
    raw = data.get(key)
    if raw in (None, ""):
        return default
    try:
        val = cast(raw)
    except (TypeError, ValueError):
        abort(400, description=f"{key} must be a number")
    if not (lo <= val <= hi):
        abort(400, description=f"{key} must be between {lo} and {hi}")
    return val


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    """Run the NS-3 simulator with the given parameters, import the result, return the new run id."""
    data = request.get_json(silent=True) or {}
    scenario = data.get("scenario", "flood")
    relay = data.get("relayMode", "adaptive")
    if scenario not in ("flood", "earthquake", "landslide", "fire"):
        abort(400, description="unknown scenario")
    if relay not in ("adaptive", "static"):
        abort(400, description="relayMode must be adaptive or static")
    drone_range = _param(data, "droneRange", 100, 800, 450)
    tower_range = _param(data, "towerRange", 100, 800, 600)
    loss = _param(data, "loss", 0, 0.8, 0.10)
    seed = _param(data, "seed", 1, 999999, 1, int)
    down_start = _param(data, "towerDownStart", 0, 900, -1)
    down_end = _param(data, "towerDownEnd", 0, 900, -1)
    if down_start >= 0 and down_end <= down_start:
        abort(400, description="tower outage end must be later than its start")

    name = (data.get("name") or "").strip() or f"web_{time.strftime('%H%M%S')}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        abort(400, description="run name: letters, digits, _ and - only (max 40)")
    if os.path.isfile(SIM_BIN):
        sim_cmd = [SIM_BIN]
    elif os.path.isfile(PY_SIM):
        sim_cmd = [sys.executable, PY_SIM]
    else:
        abort(400, description="Simulator not built. In a terminal run:  cd ns3 && make   (or set SIM_BIN)")
    if not _sim_lock.acquire(blocking=False):
        return jsonify({"error": "another simulation is already running"}), 409
    try:
        out_dir = os.path.join(OUT_ROOT, name)
        cmd = sim_cmd + [f"--outDir={out_dir}", f"--scenario={scenario}", f"--relayMode={relay}",
               f"--droneRange={drone_range}", f"--towerRange={tower_range}", f"--loss={loss}", f"--seed={seed}",
               f"--towerDownStart={down_start}", f"--towerDownEnd={down_end}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return jsonify({"error": "simulation took longer than 180 s and was stopped"}), 500
        if proc.returncode != 0:
            return jsonify({"error": "simulator failed: " + (proc.stderr or proc.stdout)[-300:]}), 500
        conn = get_db()
        importer.import_run(conn, out_dir, name)
        run_id = conn.execute("SELECT id FROM runs WHERE name = ?", (name,)).fetchone()["id"]
        return jsonify({"run_id": run_id, "name": name, "summary": (proc.stdout.strip().splitlines() or [""])[-1]})
    finally:
        _sim_lock.release()


@app.route("/api/compare")
def api_compare():
    """One row per run, used for the run-comparison chart."""
    cur = get_db().execute(
        """SELECT r.id, r.name, r.scenario, r.relay_mode, r.drone_range_m, r.loss_base,
                  r.tower_down_start, r.tower_down_end, r.captured, r.delivered,
                  AVG(p.latency_s) AS avg_latency_s, MAX(p.latency_s) AS max_latency_s
           FROM runs r LEFT JOIN reports p ON p.run_id = r.id
           GROUP BY r.id ORDER BY r.id""")
    return jsonify(rows(cur))


@app.errorhandler(400)
@app.errorhandler(404)
def json_error(e):
    return jsonify({"error": e.description}), e.code


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
