# Project notes (for the report and the viva)

## 1. How the simulation works

**Nodes.** Primary drone, relay drone, tower, hub. The first three share an 802.11b ad-hoc Wi-Fi channel. The tower and hub are joined by a 100 Mbps point-to-point link with 5 ms delay.

**Flight.** The primary drone flies a lawnmower path (7 rows, 100 m apart) at 15 m/s over a 600 m by 700 m zone. When a hidden finding comes within 60 m (camera footprint), it becomes a report: finding type, severity, latitude, longitude, capture time and a payload of 14-18 KB standing in for the image.

**Store-and-forward.** Every node keeps a queue. It sends only when the next hop is reachable (in range, and for the tower link, tower online). A report stays in the queue until the next hop returns an ACK. This is why data survives gaps in connectivity.

**Priority.** The queue sends the most severe report first, oldest first among equals.

**Reliability.** Each hop uses UDP plus an application-level ACK and a 1 s timeout (stop-and-wait ARQ). Packet loss is drawn at the receiver with probability `loss + 0.30 * (distance / range)^2`, so links are worse near their edge. Duplicates are re-ACKed but not forwarded twice.

**Relay position.**
- *Static*: hovers at a fixed point between the zone and the tower.
- *Adaptive*: moves along the line from the tower toward the primary drone, as close as the tower link allows, so it stays within drone range for as long as possible.

**Primary drone state machine.** Survey, then offload (fly toward the relay and hover until the buffer is empty), then return to base.

**Metrics.** FlowMonitor gives packets sent, received and lost, mean delay and throughput for every UDP flow. The simulator also computes end-to-end latency per report (capture time to hub receive time) and counts retransmissions (total sends minus 3, the minimum one per hop).

## 2. Results from the included runs

All runs: flood scenario, 14 findings, seed 1, base loss 10%, unless noted. Every run delivered 14 of 14 reports.

| Run | Relay | Drone range | Average delay | Slowest report |
|---|---|---|---|---|
| sweep_static_r200 | static | 200 m | 45.1 s | 95.2 s |
| sweep_adaptive_r200 | adaptive | 200 m | 13.2 s | 47.2 s |
| sweep_static_r300 | static | 300 m | 10.8 s | 41.6 s |
| sweep_adaptive_r300 | adaptive | 300 m | 6.9 s | 38.1 s |
| sweep_static_r450 | static | 450 m | 0.96 s | 3.1 s |
| sweep_adaptive_r450 | adaptive | 450 m | 1.03 s | 3.1 s |
| sweep_static_r600 | static | 600 m | 0.82 s | 2.6 s |
| sweep_adaptive_r600 | adaptive | 600 m | 0.71 s | 3.1 s |

Other runs:

| Run | What changed | Average delay | Slowest | Retransmissions |
|---|---|---|---|---|
| baseline_adaptive | defaults | 1.03 s | 3.1 s | 11 |
| lossy_40pct | base loss 40% | 2.12 s | 7.6 s | 25 |
| tower_outage_60_200s | tower offline 60 s to 200 s | 47.2 s | 137.4 s | 11 |
| earthquake_adaptive | earthquake, seed 7 (different layout, so not comparable with the flood runs) | 7.78 s | 45.2 s | 15 |

**What these show**
- With a short drone range, delay is dominated by waiting for the primary drone to come back into range. The adaptive relay cuts that wait (about 3.4 times lower at 200 m, 1.6 times lower at 300 m).
- Once the range is large enough that the primary drone is almost always connected, adaptive and static are about the same.
- A tower outage does not lose data. The relay holds the reports and sends them when the tower returns, at the cost of delay.
- At 40% base loss, retransmissions rise from 11 to 25 but all reports still arrive, at roughly twice the delay.

Each configuration was run once with a single seed. For stronger claims, repeat each configuration over several seeds (`--seed=1..10`) and report the mean and spread.

## 3. Likely viva questions

**Why drones instead of just waiting for the network to be restored?** Restoring towers can take days. Drones can reach cut-off areas within minutes and carry information out.

**Why a relay drone? Can't the primary drone fly to the tower?** It can, but then it stops surveying. The relay lets the primary keep covering the area while data flows out continuously.

**Why NS-3?** It models real protocol stacks (Wi-Fi, UDP, IP), mobility and timing, and FlowMonitor measures delay and loss. Testing with real drones would be costly and risky.

**Why UDP and not TCP?** UDP has no connection setup, which suits links that appear and disappear. We add our own ACK and retransmission, so reliability is under our control.

**What is store-and-forward?** Each node stores data until the next hop is reachable, then forwards it. It is the idea behind delay-tolerant networking.

**What does FlowMonitor measure?** For each flow: packets and bytes sent and received, lost packets, delay sum (mean delay) and first and last packet times (throughput).

**Why are "lost packets" non-zero when all reports arrived?** The sender retransmits until it gets an ACK, so a lost packet is recovered. FlowMonitor counts the loss at each attempt.

**Why SQLite?** No server to install, one file to carry, built into Python, and enough for this data volume.

**Why does the NGO website not talk to NS-3 directly?** NS-3 runs offline in batch. Writing results to files, importing them into a database and serving them through an API keeps the website simple and lets us compare many runs.

**What are the limitations?** 2D movement, simplified radio (hard range plus distance-dependent loss), no real images, one seed per configuration, no battery or backup handover in the NS-3 code.

**What would you add next?** Battery model and backup drone handover in NS-3, multiple primary drones, a realistic propagation model, real image compression sizes, and multi-seed statistics.

## 4. Suggested report structure

1. Introduction and problem statement (from the synopsis)
2. Literature survey (UAV relay networks, delay-tolerant networking, disaster communication)
3. System architecture (diagram in README)
4. Simulation design (section 1 above)
5. Web application design (API endpoints, database tables)
6. Results and analysis (section 2 above, with the screenshots of the dashboard and the comparison chart)
7. Conclusion and future work
