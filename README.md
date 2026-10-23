# Drone-Based Disaster Communication System

Mini project, Dept. of Information Science and Engineering, SIT Tumakuru, 2026-27 (Batch B5).

When a disaster knocks out mobile networks, a **primary drone** surveys the affected area and captures images and GPS coordinates. It hands the data to a **relay (backup) drone**, which carries it to a **communication tower** that still works. The tower forwards it over a wired link to the **main hub**, where NGOs and rescue teams see it on a website. Everything is simulated, with no hardware.

```
 Disaster zone (no network)                       Working network
┌────────────────────────┐      Wi-Fi/UDP      ┌───────┐  Wi-Fi/UDP  ┌───────┐  wired/UDP  ┌──────────┐
│ Primary drone          │ ──────────────────▶ │ Relay │ ──────────▶ │ Tower │ ──────────▶ │ Main hub │
│ survey, capture, queue │   store-and-forward │ drone │             │       │             │  (NGO)   │
└────────────────────────┘                     └───────┘             └───────┘             └────┬─────┘
                                                                                                │
   NS-3 writes reports.csv, flows.csv, events.csv, run.json                                     ▼
   importer.py ─▶ SQLite ─▶ Flask REST API ─▶ NGO website (map, priority queue, charts)
```

## What is in the folder

```
ns3/            drone_disaster_sim.cc   NS-3 simulation (C++)
                Makefile                build against apt-installed ns-3.41
                run_experiments.sh      batch of 13 runs (each run also writes trace.csv and pois.csv for the replay)
backend/        schema.sql, db.py       SQLite schema (runs, reports, flows, events)
                importer.py             NS-3 output  ->  SQLite
                app.py                  Flask API + serves the website
web/            index.html, static/     NGO dashboard (HTML, CSS, JavaScript)
                static/replay.js        replay player (canvas animation of the NS-3 trace)
data/sample_runs/   13 real NS-3 runs, ready to import
demo/           drone-disaster-comm-sim.html   standalone browser animation of the idea
docs/           SETUP_WSL2.md           step-by-step install and run
                PROJECT_NOTES.md        explanation, results, viva questions
```

## Fastest way to see it working (Python only, 2 minutes)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python importer.py --all ../data/sample_runs --reset
python app.py
```

Open http://localhost:5000. Press **Play** in the replay panel to watch the real NS-3 run: drones moving, links connecting and dropping, reports reaching the hub. Click findings on the map, press "Dispatch team", and compare runs in the bar chart.

## Run a simulation from the website

The page has a **Run a new simulation** form. Choose scenario, relay behaviour, ranges, packet loss and an optional tower outage, then press *Run simulation*. Flask starts the NS-3 program, imports the result and the replay plays it straight away. This needs the simulator to be built first (`cd ns3 && make`). If your binary is somewhere else, start the website with `SIM_BIN=/path/to/binary python app.py`.

## Full pipeline

1. Build and run NS-3 (see `docs/SETUP_WSL2.md`): `cd ns3 && make && bash run_experiments.sh`
2. Import: `cd ../backend && python importer.py --all ../ns3_output --reset`
3. Start the website: `python app.py`

## Simulator options

```
./drone_disaster_sim --scenario=flood|earthquake|landslide|fire
                     --relayMode=adaptive|static
                     --droneRange=450        drone-to-drone range, metres
                     --towerRange=600        tower coverage radius, metres
                     --loss=0.10             base packet loss probability
                     --imageBytes=12000      size of one report
                     --numPois=14            hidden findings in the zone
                     --towerDownStart=60 --towerDownEnd=200   tower outage window (s)
                     --seed=1  --simTime=900  --outDir=output
```

## How it maps to the synopsis objectives

| Objective | Where it is in the project |
|---|---|
| 1-2. Detect and monitor, capture image and lat/long | Primary drone lawnmower survey, camera footprint check, report with GPS (`Controller::Capture`) |
| 3. Drone-to-drone communication without a mobile network | 802.11b ad-hoc Wi-Fi, UDP, primary to relay |
| 4. Backup drone as relay | Relay node with a store-and-forward buffer, adaptive or static position |
| 5. Send to hub via a nearby tower | Relay to tower (Wi-Fi), tower to hub (point-to-point link) |
| 6. Real-time information for NGOs and rescue teams | Flask API and the dashboard: live replay, map, priority queue, dispatch, run form |
| 7. Reduce delay and improve information sharing | FlowMonitor delay, loss and throughput; run comparison chart |

## What is modelled, and what is simplified

- Radio range limits, per-hop packet loss that grows with distance, ACKs and retransmission are implemented at the application layer on top of NS-3 UDP sockets. The Wi-Fi channel itself uses a hard maximum range.
- The replay is not a live stream. NS-3 finishes first, then the dashboard plays back the recorded positions, link states and packet hops. Replay speed can be changed, but the simulation itself is already complete.
- Positions are 2D (no altitude). Drones fly at constant speed along a lawnmower path.
- An "image" is a UDP payload of the chosen size. No real pictures are sent. The dashboard shows text, GPS and severity.
- Battery and backup-drone handover are in the browser demo (`demo/`) but not in the NS-3 code. Adding them is a good extension.
- The NS-3 code was compiled and run against ns-3.41 (Ubuntu 24.04 package). Other versions may need small API changes.
