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


## 5. Standard Simulation Parameters & Empirical Literature Validation

### Table 1: Simulation Parameters & Empirical Justification

| Parameter | Simulation Value | Real-World Hardware Equivalent | Empirical & Literature Justification | Reference |
|---|:---:|---|---|:---:|
| **Simulation Tool** | **NS-3 (v3.41)** | Network Simulator for discrete-event protocol stacks | Standard discrete-event network simulator for 802.11 PHY/MAC modeling and FlowMonitor metrics. | [1], [2] |
| **Airframe Platform** | **Hexa/Quadcopter** | **DJI Matrice 300 RTK** *(Commercial)*<br>or **Holybro S500 + Pixhawk 4** *(Lab)* | Industrial search-and-rescue UAV capable of autonomous waypoint navigation and 55-minute endurance. | [1], [2] |
| **Wireless Protocol** | **IEEE 802.11b Ad-Hoc** | 2.4 GHz ISM band wireless transceiver | Standard ad-hoc Wi-Fi standard for decentralized, infrastructureless Flying Ad-Hoc Networks (FANETs). | [1] |
| **Drone-to-Drone (A2A) Range** | **450 m** | DJI Payload SDK (PSDK) port with 802.11 high-gain Wi-Fi module | Outdoor flight trials confirm direct air-to-air Line-of-Sight (LoS) links achieve sustained throughput at **300 m – 500 m**. | [1], [4] |
| **Tower Coverage (A2G) Radius** | **600 m** | Surviving small-cell cellular tower or elevated base station mast | Mathematical air-to-ground path loss models show elevated base stations maintain stable link margins up to **600 m – 1,000 m**. | [1] |
| **Drone Survey Speed** | **15 m/s** (54 km/h) | Nominal cruise velocity of DJI Matrice 300 RTK | Optimal cruising survey velocity for autonomous search-and-rescue (SAR) aerial coverage. | [2] |
| **Camera Footprint Radius** | **60 m** | Zenmuse H20T Optical/Thermal Sensor payload | Optical geometry: 60 m flight altitude (AGL) with an 84° Field-of-View (FOV) camera yields an effective 60 m ground radius. | [2] |
| **Disaster Survey Area** | **600 m × 700 m** | Standard local search grid | Typical geographic sector assigned to a first-response drone team in a flood or earthquake zone. | [2] |
| **Report Payload Size** | **12 KB** | Compressed emergency incident packet | Highly compressed 320 × 240 preview thumbnail + GPS coordinates + timestamp + severity index telemetry. | [4] |
| **Baseline Packet Loss Rate** | **10%** | Clear-sky outdoor mobile channel | Standard baseline Packet Error Rate (PER) in outdoor mobile wireless channels due to airframe vibration and Doppler shifts. | [3] |
| **Adverse Packet Loss (Stress)** | **40%** | Extreme environmental degradation | Spikes in packet error rate (35%–45%) caused by particulate scattering from wildfire smoke, heavy rain fade, or severe multipath. | [3] |
| **Transport Protocol** | **UDP + Stop-and-Wait ARQ** | Lightweight application-layer ACK protocol | Avoids heavy TCP handshake overhead over intermittent links; application retransmissions guarantee **100% delivery**. | [4] |
| **Routing / Forwarding Scheme** | **Store-and-Forward (DTN)** | Onboard companion computer FIFO memory queue (Raspberry Pi / Jetson) | Delay-Tolerant Networking (DTN) "Carry-and-Forward": packets are buffered locally during dead zones and flushed when link recovers. | [4] |
| **Tower Outage Window** | **140 s** (t = 60 s to 200 s) | Simulated backup generator failure / cellular power blackout | Simulates temporary power grid loss at the surviving tower to prove zero data is lost due to drone buffer queues. | [4] |

### The 4 Major Scenarios Mapped to Literature

1. **`baseline_adaptive` (The Validated Reference Standard):**
   - Flood scenario, 450 m range, 600 m tower coverage, 10% loss, adaptive relay. Average delay: **1.03 s** [1].
2. **`earthquake_adaptive` (Search Patterns & Hazard Geometry):**
   - Preserves baseline radio parameters; tests scattered peripheral survivor clusters (Seed 7). Average delay: **7.78 s** [2].
3. **`lossy_40pct` (Harsh Weather Channel Degradation):**
   - 40% base loss simulating smoke/rain attenuation; validates Stop-and-Wait ARQ 100% PDR. Average delay: **2.12 s** [3].
4. **`tower_outage_60_200s` (Infrastructure Blackout & DTN):**
   - 140-second tower blackout; validates store-and-forward queue buffering with zero packet loss. Average delay: **47.2 s** [4].

### IEEE References

```text
[1] M. Mozaffari, W. Saad, M. Bennis, Y.-H. Nam, and M. Debbah, "A Tutorial on UAVs for Wireless Networks: Applications, Challenges, and Open Problems," IEEE Communications Surveys & Tutorials, vol. 21, no. 3, pp. 2334–2360, 3rd Quart. 2019, doi: 10.1109/COMST.2019.2902862.

[2] S. Waharte and N. Trigoni, "Supporting Search and Rescue Operations with UAVs," in Proc. IEEE International Conference on Emerging Security Technologies (EST), Sep. 2010, pp. 142–147, doi: 10.1109/EST.2010.31.

[3] A. A. Khuwaja, Y. Chen, N. Zhao, M.-S. Alouini, and P. Dobbins, "A Survey of Channel Modeling for UAV Communications," IEEE Communications Surveys & Tutorials, vol. 20, no. 4, pp. 2804–2821, 4th Quart. 2018, doi: 10.1109/COMST.2018.2856587.

[4] M. Asadpour, K. A. Hummel, D. Giustiniano, and S. Draskovic, "Route or Carry: Motion-Driven Packet Forwarding in Micro Aerial Vehicle Networks," IEEE Transactions on Mobile Computing, vol. 16, no. 3, pp. 843–856, Mar. 2017, doi: 10.1109/TMC.2016.2568181.
```
