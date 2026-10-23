-- SQLite schema for the Drone-Based Disaster Communication System
-- One "run" = one NS-3 simulation execution with its own parameters.

CREATE TABLE IF NOT EXISTS runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT UNIQUE NOT NULL,      -- e.g. "baseline_adaptive"
    scenario           TEXT,                      -- flood / earthquake / landslide / fire
    relay_mode         TEXT,                      -- adaptive / static
    drone_range_m      REAL,
    tower_range_m      REAL,
    loss_base          REAL,
    image_bytes        INTEGER,
    seed               INTEGER,
    tower_down_start   REAL,                      -- -1 = no outage
    tower_down_end     REAL,
    captured           INTEGER,
    delivered          INTEGER,
    avg_latency_s      REAL,
    peak_primary_queue INTEGER,
    peak_relay_queue   INTEGER,
    sim_end_s          REAL,
    imported_at        TEXT DEFAULT (datetime('now'))
);

-- Reports that reached the main hub (what the NGO sees)
CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    pkt_id        TEXT NOT NULL,                  -- PKT-001 ...
    finding       TEXT NOT NULL,
    severity      INTEGER NOT NULL,               -- 3 critical, 2 high, 1 moderate
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    captured_s    REAL,
    received_s    REAL,
    latency_s     REAL,
    tx_attempts   INTEGER,                        -- total transmissions over all hops (3 = no retransmission)
    status        TEXT NOT NULL DEFAULT 'new',    -- new / dispatched / resolved
    dispatched_at TEXT
);

-- FlowMonitor statistics, one row per UDP flow
CREATE TABLE IF NOT EXISTS flows (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    flow_id          INTEGER,
    src              TEXT,
    dst              TEXT,
    tx_packets       INTEGER,
    rx_packets       INTEGER,
    lost_packets     INTEGER,
    tx_bytes         INTEGER,
    rx_bytes         INTEGER,
    mean_delay_ms    REAL,
    throughput_kbps  REAL
);

-- Timeline of simulation events (captures, hops, link up/down, outages)
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    time_s  REAL,
    node    TEXT,
    event   TEXT,
    detail  TEXT
);

-- Drone positions, link states and queue sizes every 0.5 s (used by the replay player)
CREATE TABLE IF NOT EXISTS trace (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    time_s    REAL,
    px REAL, py REAL,            -- primary drone position (m)
    rx REAL, ry REAL,            -- relay drone position (m)
    pstate    TEXT,              -- survey / offload / rtl / done
    pq INTEGER, rq INTEGER,      -- queue length at primary / relay
    link_pr   INTEGER, link_rt INTEGER, tower_up INTEGER,
    delivered INTEGER, captured INTEGER
);

-- Hidden findings placed in the disaster zone (found_s = -1 if never found)
CREATE TABLE IF NOT EXISTS pois (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    poi_id   INTEGER, x REAL, y REAL, finding TEXT, severity INTEGER, found_s REAL
);

CREATE INDEX IF NOT EXISTS idx_trace_run ON trace(run_id);
CREATE INDEX IF NOT EXISTS idx_pois_run  ON pois(run_id);
CREATE INDEX IF NOT EXISTS idx_reports_run ON reports(run_id);
CREATE INDEX IF NOT EXISTS idx_flows_run   ON flows(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run  ON events(run_id);
