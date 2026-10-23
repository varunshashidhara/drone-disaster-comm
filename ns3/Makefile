# Build the simulator against the ns-3 packages installed with apt (Ubuntu 24.04: libns3-dev 3.41)
#   sudo apt install g++ make pkg-config libns3-dev libsqlite3-dev libgsl-dev
#   make
# NOTE: do NOT add -I/usr/include/ns3 : that folder contains a string.h that shadows the system one.
MODS := ns3-core ns3-network ns3-internet ns3-mobility ns3-wifi ns3-point-to-point \
        ns3-applications ns3-flow-monitor ns3-propagation
LIBS := $(shell pkg-config --libs $(MODS))

drone_disaster_sim: drone_disaster_sim.cc
	g++ -std=c++20 -O2 $< -o $@ $(LIBS)

clean:
	rm -f drone_disaster_sim
