# Setup guide (Windows 11 + WSL2 + Ubuntu 24.04)

The project has two halves that you can set up independently:

| Half | Needs | Time |
|---|---|---|
| NGO website + Flask + SQLite (works with the included sample data) | Python 3.10+ | 5 min |
| NS-3 simulation (C++) | WSL2 Ubuntu 24.04 | 10 min (Option A) or 1 hour (Option B) |

Tip: do the website half first. It already works with the sample runs in `data/sample_runs/`, so you can see the whole dashboard before touching NS-3.

---

## 1. Install WSL2 and Ubuntu 24.04

Open **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart when asked, then open "Ubuntu 24.04" from the Start menu and create a username and password.

Keep the project inside the Linux file system (for example `~/drone-disaster-comm`), not under `/mnt/c/...`. It is much faster and avoids permission problems. To copy the project in, unzip it on Windows, open `\\wsl$\Ubuntu-24.04\home\<your-user>` in File Explorer and paste the folder there.

---

## 2A. NS-3 the quick way (recommended, tested)

Ubuntu 24.04 ships ns-3.41 as a package, so you do not have to compile ns-3 yourself.

```bash
sudo apt update
sudo apt install -y g++ make pkg-config libns3-dev libsqlite3-dev libgsl-dev python3-venv

cd ~/drone-disaster-comm/ns3
make                                   # builds ./drone_disaster_sim
./drone_disaster_sim                   # one run with default settings, output in ./output
```

You should see a line like:

```
Run finished at t=361.5 s | captured=14 delivered=14 avg latency=1.02984 s | output: output
```

Run the full batch of experiments (13 runs, takes about a minute):

```bash
bash run_experiments.sh                # writes to ../ns3_output/<run name>/
```

## 2B. NS-3 from the official source (if your guide wants the ns-3 tree)

```bash
sudo apt install -y g++ python3 cmake ninja-build git wget bzip2 libsqlite3-dev libgsl-dev
cd ~
wget https://www.nsnam.org/releases/ns-allinone-3.41.tar.bz2
tar xf ns-allinone-3.41.tar.bz2 && cd ns-allinone-3.41/ns-3.41

cp ~/drone-disaster-comm/ns3/drone_disaster_sim.cc scratch/
./ns3 configure --build-profile=optimized
./ns3 build                            # 30-60 minutes the first time

./ns3 run "scratch/drone_disaster_sim --outDir=$HOME/drone-disaster-comm/ns3_output/my_run"
```

To run the whole batch with this build, point the script at the binary:

```bash
ls build/scratch/                      # find the executable name, e.g. ns3.41-drone_disaster_sim-optimized
cd ~/drone-disaster-comm/ns3
SIM=$HOME/ns-allinone-3.41/ns-3.41/build/scratch/ns3.41-drone_disaster_sim-optimized bash run_experiments.sh
```

Other ns-3 versions (3.38 to 3.42) should work with small or no changes. The code was compiled and run only against 3.41.

---

## 3. Website and database

```bash
cd ~/drone-disaster-comm/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# load the sample runs that ship with the project
python importer.py --all ../data/sample_runs --reset

python app.py
```

Open **http://localhost:5000** in your Windows browser (WSL2 forwards localhost automatically).

To show your own simulation results instead of the samples:

```bash
python importer.py --all ../ns3_output --reset
```

The **Run a new simulation** form on the page starts `ns3/drone_disaster_sim` for you, so run `make` in `ns3/` first. With the Option 2B build, start the website like this:

```bash
SIM_BIN=$HOME/ns-allinone-3.41/ns-3.41/build/scratch/ns3.41-drone_disaster_sim-optimized python app.py
```

The dashboard loads Leaflet, Chart.js and map tiles from the internet, so the computer needs to be online for the map and charts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `'memchr' has not been declared in '::'` while compiling | You added `-I/usr/include/ns3`. Remove it. That folder contains a `string.h` that hides the system one. Use the Makefile as it is. |
| `cannot find -lsqlite3` or `-lgsl` | `sudo apt install libsqlite3-dev libgsl-dev` |
| `Permission denied` when running a binary | Move the project into the Linux home folder (`~/`), not `/mnt/c/...` |
| `libns3-dev` has no CMake or pkg-config files, or a very old version | You are on Ubuntu 22.04. Use Ubuntu 24.04, or Option 2B |
| Dashboard says "No simulation data yet" | Run the importer (step 3) and reload |
| Map is blank but list works | No internet for the map library or tiles |
| Form says "Simulator not built" | `cd ~/drone-disaster-comm/ns3 && make`, or set `SIM_BIN` as above |
| Replay panel says "no movement data" | The run was imported from an older simulator. Re-run the experiments, then re-import with `--reset` |
| Port 5000 busy | `PORT=5050 python app.py` |
