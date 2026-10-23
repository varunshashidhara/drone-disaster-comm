#!/usr/bin/env bash
# Run a batch of NS-3 experiments and store each run in its own folder.
#
#   ./run_experiments.sh                      # uses ./drone_disaster_sim, writes to ../ns3_output
#   SIM=/path/to/binary OUT=/some/dir ./run_experiments.sh
#
# Binary inside an ns-3 source tree (Option B in docs/SETUP_WSL2.md), e.g.:
#   SIM=~/ns-3.41/build/scratch/ns3.41-drone_disaster_sim-default ./run_experiments.sh
set -euo pipefail
SIM="${SIM:-./drone_disaster_sim}"
OUT="${OUT:-../ns3_output}"
mkdir -p "$OUT"

run() {   # run <name> <args...>
  local name="$1"; shift
  echo ">> $name  ($*)"
  "$SIM" --outDir="$OUT/$name" "$@" | tail -1
}

# --- core scenarios -----------------------------------------------------------
run baseline_adaptive
run static_range300         --relayMode=static --droneRange=300
run tower_outage_60_200s    --towerDownStart=60 --towerDownEnd=200
run lossy_40pct             --loss=0.4
run earthquake_adaptive     --scenario=earthquake --seed=7

# --- sweep: relay behaviour x drone-to-drone range -------------------------------
for mode in static adaptive; do
  for r in 200 300 450 600; do
    run "sweep_${mode}_r${r}" --relayMode=$mode --droneRange=$r
  done
done

echo
echo "Done. Import into the database with:"
echo "  cd ../backend && python importer.py --all $OUT --reset"
