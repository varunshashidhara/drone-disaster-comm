#!/usr/bin/env python3
"""
Python-based fallback implementation of drone_disaster_sim.
Produces the exact same CSVs and run.json as drone_disaster_sim.cc for Windows / non-WSL environments.
"""
import argparse
import csv
import json
import math
import os
import random
import sys

LAT0 = 13.3700
LON0 = 77.0900
CAM_R = 60.0
P_SPEED = 15.0
R_SPEED = 20.0
CTRL_DT = 0.5
BASE = (60.0, 760.0)
TOWER_POS = (1100.0, 350.0)
HUB_POS = (1300.0, 350.0)
ZONE_W = 600.0
ZONE_H = 700.0

SCEN = {
    "flood": [
        ("Stranded people", 3),
        ("Submerged house", 2),
        ("Washed-out road", 1),
        ("Rising water level", 2),
    ],
    "earthquake": [
        ("Trapped person", 3),
        ("Collapsed building", 3),
        ("Blocked road", 1),
        ("Damaged bridge", 2),
    ],
    "landslide": [
        ("Survivors on slope", 3),
        ("Buried house", 3),
        ("Blocked highway", 2),
        ("Stranded vehicle", 2),
    ],
    "fire": [
        ("Trapped campers", 3),
        ("Burnt structure", 1),
        ("Smoke-blocked road", 2),
        ("Active fire front", 3),
    ],
}


def dist(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def steer(pos, target, speed):
    dx = target[0] - pos[0]
    dy = target[1] - pos[1]
    d = math.hypot(dx, dy)
    if d < 1.0:
        return list(target), True
    v = min(speed, d / CTRL_DT)
    vx = dx / d * v
    vy = dy / d * v
    return [pos[0] + vx * CTRL_DT, pos[1] + vy * CTRL_DT], False


def relay_target(relay_mode, p_pos, tower_range, drone_range):
    if relay_mode == "static":
        return [TOWER_POS[0] - 0.85 * tower_range, TOWER_POS[1]]
    vx = p_pos[0] - TOWER_POS[0]
    vy = p_pos[1] - TOWER_POS[1]
    d = max(1.0, math.hypot(vx, vy))
    want = max(0.0, d - 0.85 * drone_range)
    lim = 0.92 * tower_range
    k = min(want, lim)
    return [TOWER_POS[0] + vx / d * k, TOWER_POS[1] + vy / d * k]


def loss_prob(d, up_range, loss_base):
    if up_range <= 0:
        return 0.0
    r = min(1.0, d / up_range)
    return min(0.95, loss_base + 0.30 * r * r)


def main():
    parser = argparse.ArgumentParser(description="Drone Disaster Sim")
    parser.add_argument("--scenario", default="flood")
    parser.add_argument("--relayMode", default="adaptive")
    parser.add_argument("--droneRange", type=float, default=450.0)
    parser.add_argument("--towerRange", type=float, default=600.0)
    parser.add_argument("--loss", type=float, default=0.10)
    parser.add_argument("--imageBytes", type=int, default=12000)
    parser.add_argument("--numPois", type=int, default=14)
    parser.add_argument("--towerDownStart", type=float, default=-1.0)
    parser.add_argument("--towerDownEnd", type=float, default=-1.0)
    parser.add_argument("--simTime", type=float, default=900.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--outDir", default="output")
    args = parser.parse_args()

    if args.scenario not in SCEN:
        print(f"Unknown scenario: {args.scenario}", file=sys.stderr)
        sys.exit(1)

    rng = random.Random(args.seed)
    os.makedirs(args.outDir, exist_ok=True)

    # Generate POIs
    pois = []
    tries = 0
    while len(pois) < args.numPois and tries < 2000:
        tries += 1
        px = rng.uniform(30.0, ZONE_W - 30.0)
        py = rng.uniform(30.0, ZONE_H - 30.0)
        if all(dist((px, py), q["pos"]) >= 80.0 for q in pois):
            pois.append({
                "id": len(pois) + 1,
                "pos": (px, py),
                "type": rng.randint(0, 3),
                "found": False,
                "tFound": -1.0,
            })

    # Lawnmower survey path (7 passes)
    path = []
    for i in range(7):
        y = 50.0 + i * 100.0
        l, r = 50.0, ZONE_W - 50.0
        if i % 2 == 0:
            path.append((l, y))
            path.append((r, y))
        else:
            path.append((r, y))
            path.append((l, y))

    p_pos = list(BASE)
    r_pos = [TOWER_POS[0] - 0.85 * args.towerRange, TOWER_POS[1]]
    t_pos = list(TOWER_POS)

    p_state = "survey"  # survey, offload, rtl, done
    p_waypoint_idx = 0

    p_queue = []
    r_queue = []
    t_queue = []

    peak_p_q = 0
    peak_r_q = 0

    p_seen = set()
    r_seen = set()
    t_seen = set()
    h_seen = set()

    p_inflight = None
    r_inflight = None
    t_inflight = None

    p_deadline = 0.0
    r_deadline = 0.0
    t_deadline = 0.0

    attempts = {}  # pkt_id -> count of tx attempts
    delivered = []
    captured_count = 0

    events = []

    def log_event(t, node, ev, detail):
        events.append((f"{t:.2f}", node, ev, detail))

    log_event(0.0, "Primary", "launch", f"survey of {len(pois)} hidden findings")

    last_pr = False
    last_rt = False
    tower_up = True
    tower_logged_offline = False
    tower_logged_online = False

    trace = []

    # Flow stats tracking
    # 1: P->R, 2: R->T, 3: R->P (ACK), 4: T->H, 5: T->R (ACK), 6: H->T (ACK)
    flow_stats = {
        1: {"src": "Primary", "dst": "Relay", "tx_p": 0, "rx_p": 0, "lost_p": 0, "tx_b": 0, "rx_b": 0, "delays": []},
        2: {"src": "Relay", "dst": "Tower", "tx_p": 0, "rx_p": 0, "lost_p": 0, "tx_b": 0, "rx_b": 0, "delays": []},
        3: {"src": "Relay", "dst": "Primary", "tx_p": 0, "rx_p": 0, "lost_p": 0, "tx_b": 0, "rx_b": 0, "delays": []},
        4: {"src": "Tower", "dst": "Hub", "tx_p": 0, "rx_p": 0, "lost_p": 0, "tx_b": 0, "rx_b": 0, "delays": []},
        5: {"src": "Tower", "dst": "Relay", "tx_p": 0, "rx_p": 0, "lost_p": 0, "tx_b": 0, "rx_b": 0, "delays": []},
        6: {"src": "Hub", "dst": "Tower", "tx_p": 0, "rx_p": 0, "lost_p": 0, "tx_b": 0, "rx_b": 0, "delays": []},
    }

    t = 0.0
    stopping = False
    stop_at = None

    while t <= args.simTime:
        # Check tower outage
        if args.towerDownStart >= 0:
            if args.towerDownEnd > args.towerDownStart:
                tower_up = not (args.towerDownStart <= t < args.towerDownEnd)
            else:
                tower_up = t < args.towerDownStart
            if not tower_up and not tower_logged_offline and t >= args.towerDownStart:
                log_event(t, "Tower", "offline", "tower outage begins")
                tower_logged_offline = True
            if tower_up and tower_logged_offline and not tower_logged_online and t >= args.towerDownEnd:
                log_event(t, "Tower", "online", "tower restored")
                tower_logged_online = True
        else:
            tower_up = True

        # Steer Primary Drone
        if p_state == "survey":
            p_pos, arrived = steer(p_pos, path[p_waypoint_idx], P_SPEED)
            if arrived:
                p_waypoint_idx += 1
                if p_waypoint_idx >= len(path):
                    p_state = "offload"
                    log_event(t, "Primary", "survey_done", f"flying to relay to offload {len(p_queue)} packets")
        elif p_state == "offload":
            if dist(p_pos, r_pos) > 0.6 * args.droneRange:
                p_pos, _ = steer(p_pos, r_pos, P_SPEED)
            if len(p_queue) == 0:
                p_state = "rtl"
                log_event(t, "Primary", "offload_done", "returning to base")
        elif p_state == "rtl":
            p_pos, arrived = steer(p_pos, BASE, P_SPEED * 1.3)
            if arrived:
                p_state = "done"
                log_event(t, "Primary", "landed", "mission complete")

        # Capture POIs
        if p_state != "done":
            for poi in pois:
                if not poi["found"] and dist(p_pos, poi["pos"]) <= CAM_R:
                    poi["found"] = True
                    poi["tFound"] = t
                    ft_name, ft_sev = SCEN[args.scenario][poi["type"]]
                    captured_count += 1
                    pkt_id = captured_count
                    lat = LAT0 - poi["pos"][1] / 111000.0
                    lon = LON0 + poi["pos"][0] / (111000.0 * math.cos(LAT0 * math.pi / 180.0))
                    payload_bytes = args.imageBytes + 2000 * ft_sev
                    msg = {
                        "id": pkt_id,
                        "findingType": poi["type"],
                        "findingName": ft_name,
                        "severity": ft_sev,
                        "lat": lat,
                        "lon": lon,
                        "tCapture": t,
                        "payloadBytes": payload_bytes,
                    }
                    log_event(t, "Primary", "captured", f"PKT-{pkt_id:03d} {ft_name}")
                    p_queue.append(msg)
                    peak_p_q = max(peak_p_q, len(p_queue))
                    attempts[pkt_id] = 0

        # Steer Relay Drone
        r_target = relay_target(args.relayMode, p_pos, args.towerRange, args.droneRange)
        r_pos, _ = steer(r_pos, r_target, R_SPEED)

        # Link status
        pr_up = dist(p_pos, r_pos) <= args.droneRange
        rt_up = tower_up and (dist(r_pos, t_pos) <= args.towerRange)

        if pr_up != last_pr:
            log_event(t, "Link", "up" if pr_up else "down", "Primary-Relay")
            last_pr = pr_up
        if rt_up != last_rt:
            log_event(t, "Link", "up" if rt_up else "down", "Relay-Tower")
            last_rt = rt_up

        # --- Forwarding hops ---
        # Hop 3: Tower -> Hub (wired)
        if tower_up and t_queue:
            if t_inflight and t >= t_deadline:
                t_inflight = None
            if not t_inflight:
                # pick highest priority
                t_queue.sort(key=lambda m: (-m["severity"], m["tCapture"]))
                pkt = t_queue[0]
                t_inflight = pkt["id"]
                t_deadline = t + 1.0
                attempts[pkt["id"]] += 1
                size = pkt["payloadBytes"]
                flow_stats[4]["tx_p"] += 1
                flow_stats[4]["tx_b"] += size
                # wired backhaul: 0% loss, ~5ms delay
                flow_stats[4]["rx_p"] += 1
                flow_stats[4]["rx_b"] += size
                flow_stats[4]["delays"].append(5.0)

                log_event(t, "Hub", "received", f"PKT-{pkt['id']:03d}")
                if pkt["id"] not in h_seen:
                    h_seen.add(pkt["id"])
                    delivered.append({
                        "id": pkt["id"],
                        "findingType": pkt["findingType"],
                        "findingName": pkt["findingName"],
                        "severity": pkt["severity"],
                        "lat": pkt["lat"],
                        "lon": pkt["lon"],
                        "tCapture": pkt["tCapture"],
                        "tHub": t + 0.015,
                        "attempts": attempts[pkt["id"]],
                    })

                # ACK sent Hub -> Tower
                flow_stats[6]["tx_p"] += 1
                flow_stats[6]["tx_b"] += 76
                flow_stats[6]["rx_p"] += 1
                flow_stats[6]["rx_b"] += 76
                flow_stats[6]["delays"].append(5.0)

                log_event(t, "Tower", "forwarded", f"PKT-{pkt['id']:03d}")
                t_queue.pop(0)
                t_inflight = None

        # Hop 2: Relay -> Tower
        if rt_up and r_queue:
            if r_inflight and t >= r_deadline:
                r_inflight = None
            if not r_inflight:
                r_queue.sort(key=lambda m: (-m["severity"], m["tCapture"]))
                pkt = r_queue[0]
                r_inflight = pkt["id"]
                r_deadline = t + 1.0
                attempts[pkt["id"]] += 1
                size = pkt["payloadBytes"]
                flow_stats[2]["tx_p"] += 1
                flow_stats[2]["tx_b"] += size

                p_loss = loss_prob(dist(r_pos, t_pos), args.towerRange, args.loss)
                if rng.random() < p_loss:
                    flow_stats[2]["lost_p"] += 1
                    log_event(t, "Tower", "rx_lost", f"PKT-{pkt['id']:03d}")
                else:
                    flow_stats[2]["rx_p"] += 1
                    flow_stats[2]["rx_b"] += size
                    flow_stats[2]["delays"].append(72.0)
                    log_event(t, "Tower", "received", f"PKT-{pkt['id']:03d}")
                    if pkt["id"] not in t_seen:
                        t_seen.add(pkt["id"])
                        t_queue.append(pkt)
                        peak_r_q = max(peak_r_q, len(r_queue))

                    # ACK sent Tower -> Relay
                    flow_stats[5]["tx_p"] += 1
                    flow_stats[5]["tx_b"] += 76
                    flow_stats[5]["rx_p"] += 1
                    flow_stats[5]["rx_b"] += 76
                    flow_stats[5]["delays"].append(1.5)

                    log_event(t, "Relay", "forwarded", f"PKT-{pkt['id']:03d}")
                    r_queue.pop(0)
                    r_inflight = None

        # Hop 1: Primary -> Relay
        if pr_up and p_queue:
            if p_inflight and t >= p_deadline:
                p_inflight = None
            if not p_inflight:
                p_queue.sort(key=lambda m: (-m["severity"], m["tCapture"]))
                pkt = p_queue[0]
                p_inflight = pkt["id"]
                p_deadline = t + 1.0
                attempts[pkt["id"]] += 1
                size = pkt["payloadBytes"]
                flow_stats[1]["tx_p"] += 1
                flow_stats[1]["tx_b"] += size

                p_loss = loss_prob(dist(p_pos, r_pos), args.droneRange, args.loss)
                if rng.random() < p_loss:
                    flow_stats[1]["lost_p"] += 1
                    log_event(t, "Relay", "rx_lost", f"PKT-{pkt['id']:03d}")
                else:
                    flow_stats[1]["rx_p"] += 1
                    flow_stats[1]["rx_b"] += size
                    flow_stats[1]["delays"].append(72.0)
                    log_event(t, "Relay", "received", f"PKT-{pkt['id']:03d}")
                    if pkt["id"] not in r_seen:
                        r_seen.add(pkt["id"])
                        r_queue.append(pkt)
                        peak_r_q = max(peak_r_q, len(r_queue))

                    # ACK sent Relay -> Primary
                    flow_stats[3]["tx_p"] += 1
                    flow_stats[3]["tx_b"] += 76
                    flow_stats[3]["rx_p"] += 1
                    flow_stats[3]["rx_b"] += 76
                    flow_stats[3]["delays"].append(72.0)

                    log_event(t, "Primary", "forwarded", f"PKT-{pkt['id']:03d}")
                    p_queue.pop(0)
                    p_inflight = None

        # Trace row
        # time_s,px,py,rx,ry,pstate,pq,rq,link_pr,link_rt,tower_up,delivered,captured
        trace.append((
            f"{t:.2f}",
            f"{p_pos[0]:.1f}",
            f"{p_pos[1]:.1f}",
            f"{r_pos[0]:.1f}",
            f"{r_pos[1]:.1f}",
            p_state,
            len(p_queue),
            len(r_queue),
            1 if pr_up else 0,
            1 if rt_up else 0,
            1 if tower_up else 0,
            len(delivered),
            captured_count,
        ))

        # Check completion
        if p_state == "done" and len(delivered) == captured_count and not stopping:
            stopping = True
            stop_at = t + 3.0

        if stopping and t >= stop_at:
            break

        t = round(t + CTRL_DT, 2)

    end_t = t

    # Write events.csv
    with open(os.path.join(args.outDir, "events.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "node", "event", "detail"])
        for ev in events:
            writer.writerow(ev)

    # Write trace.csv
    with open(os.path.join(args.outDir, "trace.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "px", "py", "rx", "ry", "pstate", "pq", "rq", "link_pr", "link_rt", "tower_up", "delivered", "captured"])
        for tr in trace:
            writer.writerow(tr)

    # Write pois.csv
    with open(os.path.join(args.outDir, "pois.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["poi_id", "x", "y", "finding", "severity", "found_s"])
        for poi in pois:
            ft_name, ft_sev = SCEN[args.scenario][poi["type"]]
            writer.writerow([
                poi["id"],
                f"{poi['pos'][0]:.1f}",
                f"{poi['pos'][1]:.1f}",
                ft_name,
                ft_sev,
                f"{poi['tFound']:.2f}",
            ])

    # Write reports.csv
    delivered.sort(key=lambda d: d["tHub"])
    lat_sum = 0.0
    with open(os.path.join(args.outDir, "reports.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "finding", "severity", "lat", "lon", "captured_s", "received_s", "latency_s", "tx_attempts"])
        for d in delivered:
            lat = d["tHub"] - d["tCapture"]
            lat_sum += lat
            writer.writerow([
                f"PKT-{d['id']:03d}",
                d["findingName"],
                d["severity"],
                f"{d['lat']:.6f}",
                f"{d['lon']:.6f}",
                f"{d['tCapture']:.3f}",
                f"{d['tHub']:.3f}",
                f"{lat:.3f}",
                d["attempts"],
            ])

    # Write flows.csv
    with open(os.path.join(args.outDir, "flows.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["flow_id", "src", "dst", "tx_packets", "rx_packets", "lost_packets", "tx_bytes", "rx_bytes", "mean_delay_ms", "throughput_kbps"])
        for flow_id, st in flow_stats.items():
            mean_delay = (sum(st["delays"]) / len(st["delays"])) if st["delays"] else 0.0
            throughput = (st["rx_b"] * 8.0 / end_t / 1000.0) if end_t > 0 else 0.0
            writer.writerow([
                flow_id,
                st["src"],
                st["dst"],
                st["tx_p"],
                st["rx_p"],
                st["lost_p"],
                st["tx_b"],
                st["rx_b"],
                f"{mean_delay:.3f}",
                f"{throughput:.3f}",
            ])

    avg_latency = (lat_sum / len(delivered)) if delivered else 0.0

    # Write run.json
    run_meta = {
        "scenario": args.scenario,
        "relay_mode": args.relayMode,
        "drone_range_m": args.droneRange,
        "tower_range_m": args.towerRange,
        "loss_base": args.loss,
        "image_bytes": args.imageBytes,
        "seed": args.seed,
        "tower_down_start": args.towerDownStart,
        "tower_down_end": args.towerDownEnd,
        "captured": captured_count,
        "delivered": len(delivered),
        "avg_latency_s": round(avg_latency, 3),
        "peak_primary_queue": peak_p_q,
        "peak_relay_queue": peak_r_q,
        "sim_end_s": round(end_t, 3),
    }
    with open(os.path.join(args.outDir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2)

    print(f"Run finished at t={end_t:.1f} s | captured={captured_count} delivered={len(delivered)} avg latency={avg_latency:.3f} s | output: {args.outDir}")


if __name__ == "__main__":
    main()
