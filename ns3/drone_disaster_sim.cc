/*
 * drone_disaster_sim.cc
 * -----------------------------------------------------------------------------
 * Drone-Based Disaster Communication System - NS-3 simulation
 * SIT Tumakuru, Dept. of ISE, Mini Project 2026-27 (Batch B5)
 *
 * Topology (4 nodes):
 *
 *   [Primary drone] --Wi-Fi/UDP--> [Relay drone] --Wi-Fi/UDP--> [Tower] --P2P/UDP--> [Main hub]
 *      (survey zone)                (backup drone)             (surviving cell tower)   (NGO server)
 *
 * What is simulated
 *   - Primary drone flies a lawnmower survey over the disaster zone, "captures" findings
 *     (image + GPS) and queues them. The zone has no network, so data waits in its buffer.
 *   - Store-and-forward: every node keeps a priority queue and sends the head packet only
 *     when the next hop is reachable. The packet is kept until the next hop sends an ACK.
 *   - Wi-Fi 802.11b ad-hoc links with a hard radio range (RangePropagationLossModel).
 *   - Per-hop range limits (drone-to-drone range, tower coverage) and distance-dependent
 *     packet loss are applied at the application layer so each hop can have its own value.
 *   - Optional tower outage window, adaptive or static relay positioning.
 *   - FlowMonitor collects delay / throughput / loss for every UDP flow.
 *
 * Outputs (in --outDir): reports.csv, flows.csv, events.csv, trace.csv, pois.csv, run.json, flowmon.xml
 * -----------------------------------------------------------------------------
 */
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/ipv4-flow-classifier.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/wifi-module.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <vector>

using namespace ns3;
NS_LOG_COMPONENT_DEFINE("DroneDisasterComm");

// ------------------------------- constants -----------------------------------
static const double   LAT0 = 13.3700, LON0 = 77.0900; // fictional reference origin (top-left)
static const uint16_t PORT = 9000;
static const double   CAM_R = 60.0;                   // camera footprint radius (m)
static const double   P_SPEED = 15.0, R_SPEED = 20.0; // m/s
static const double   CTRL_DT = 0.5;                  // flight-controller step (s)
static const Vector   BASE(60, 760, 0), TOWER_POS(1100, 350, 0), HUB_POS(1300, 350, 0);
static const double   ZONE_W = 600, ZONE_H = 700;     // disaster zone: x in [0,600], y in [0,700]

struct Config {
    std::string scenario = "flood", relayMode = "adaptive", outDir = "output";
    double droneRange = 450, towerRange = 600, loss = 0.10, simTime = 900, ackTimeout = 1.0;
    double towerDownStart = -1, towerDownEnd = -1;
    uint32_t imageBytes = 12000, seed = 1, numPois = 14;
} g;

struct FindingType { const char* name; uint32_t sev; };
static const std::map<std::string, std::vector<FindingType>> SCEN = {
    {"flood",      {{"Stranded people", 3}, {"Submerged house", 2}, {"Washed-out road", 1}, {"Rising water level", 2}}},
    {"earthquake", {{"Trapped person", 3}, {"Collapsed building", 3}, {"Blocked road", 1}, {"Damaged bridge", 2}}},
    {"landslide",  {{"Survivors on slope", 3}, {"Buried house", 3}, {"Blocked highway", 2}, {"Stranded vehicle", 2}}},
    {"fire",       {{"Trapped campers", 3}, {"Burnt structure", 1}, {"Smoke-blocked road", 2}, {"Active fire front", 3}}}};

// ------------------------------- message format ------------------------------
enum MsgType : uint32_t { MSG_DATA = 1, MSG_ACK = 2 };
struct ReportMsg {
    uint32_t type;
    uint32_t id;
    uint32_t severity;
    uint32_t findingType;
    double   lat, lon;
    double   tCapture;
    uint32_t payloadBytes;
    uint32_t pad;
};

// ------------------------------- global state --------------------------------
struct Delivered { uint32_t id, findingType, severity; double lat, lon, tCapture, tHub; uint32_t attempts; };
static std::vector<Delivered>        g_delivered;
static std::map<uint32_t, uint32_t>  g_attempts;   // total transmissions per report (all hops)
static uint32_t                      g_captured = 0;
static std::ofstream                 g_events;
static bool                          g_towerUp = true;
static Ptr<UniformRandomVariable>    g_rng;
static Ptr<ConstantVelocityMobilityModel> g_pMob, g_rMob;
static Ptr<MobilityModel>            g_tMob;

static std::ofstream                 g_trace;

static std::string PktName(uint32_t id)
{
    char b[16];
    std::snprintf(b, sizeof(b), "PKT-%03u", id);
    return b;
}

static void LogEvent(const std::string& node, const std::string& ev, const std::string& detail)
{
    g_events << std::fixed << std::setprecision(2) << Simulator::Now().GetSeconds() << ',' << node << ',' << ev << ',' << detail << '\n';
}

// ------------------------------- store-and-forward node ----------------------
class HopNode : public Application
{
  public:
    enum Role { PRIMARY, RELAY, TOWER, HUB };

    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("HopNode").SetParent<Application>().SetGroupName("Applications").AddConstructor<HopNode>();
        return tid;
    }

    void Configure(const std::string& name, Role role, Ipv4Address next, std::function<bool()> linkCheck)
    {
        m_name = name; m_role = role; m_next = next; m_linkCheck = linkCheck;
    }
    void SetUpstream(Ptr<Node> up, double rangeM) { m_upNode = up; m_upRange = rangeM; }
    void SetUp(bool up) { m_up = up; }
    bool HasWork() const { return !m_queue.empty(); }
    size_t QueueSize() const { return m_queue.size(); }
    size_t PeakQueue() const { return m_peak; }

    void Enqueue(const ReportMsg& m)
    {
        m_queue.push_back(m);
        m_peak = std::max(m_peak, m_queue.size());
        TrySend();
    }

  private:
    void StartApplication() override
    {
        m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
        m_socket->Bind(InetSocketAddress(Ipv4Address::GetAny(), PORT));
        m_socket->SetRecvCallback(MakeCallback(&HopNode::HandleRead, this));
        m_tick = Simulator::Schedule(Seconds(CTRL_DT), &HopNode::Tick, this);
    }
    void StopApplication() override
    {
        Simulator::Cancel(m_tick);
        if (m_socket) { m_socket->Close(); m_socket = nullptr; }
    }

    void Tick()
    {
        TrySend(); // handles ACK time-outs and link-came-back events
        m_tick = Simulator::Schedule(Seconds(CTRL_DT), &HopNode::Tick, this);
    }

    // Send the highest-priority waiting report if the next hop is reachable.
    void TrySend()
    {
        if (m_role == HUB || m_queue.empty() || !m_up) return;
        if (m_inflight && Simulator::Now() < m_deadline) return; // still waiting for ACK
        m_inflight = false;
        if (!m_linkCheck || !m_linkCheck()) return;              // next hop out of range / offline

        size_t best = 0;
        for (size_t i = 1; i < m_queue.size(); ++i)
            if (m_queue[i].severity > m_queue[best].severity ||
                (m_queue[i].severity == m_queue[best].severity && m_queue[i].tCapture < m_queue[best].tCapture))
                best = i;
        const ReportMsg& m = m_queue[best];

        uint32_t size = std::max<uint32_t>(sizeof(ReportMsg), m.payloadBytes);
        std::vector<uint8_t> buf(size, 0);
        std::memcpy(buf.data(), &m, sizeof(ReportMsg));
        m_socket->SendTo(Create<Packet>(buf.data(), size), 0, InetSocketAddress(m_next, PORT));

        g_attempts[m.id]++;
        m_inflight = true; m_inflightId = m.id; m_deadline = Simulator::Now() + Seconds(g.ackTimeout);
    }

    void OnAck(uint32_t id)
    {
        for (size_t i = 0; i < m_queue.size(); ++i)
            if (m_queue[i].id == id)
            {
                LogEvent(m_name, "forwarded", PktName(id));
                m_queue.erase(m_queue.begin() + i);
                break;
            }
        if (m_inflight && m_inflightId == id) m_inflight = false;
        TrySend();
    }

    // Distance-dependent loss on the incoming wireless hop (wired hop: 0).
    double LossProb() const
    {
        if (!m_upNode || m_upRange <= 0) return 0.0;
        double d = m_upNode->GetObject<MobilityModel>()->GetDistanceFrom(GetNode()->GetObject<MobilityModel>());
        double r = std::min(1.0, d / m_upRange);
        return std::min(0.95, g.loss + 0.30 * r * r);
    }

    void HandleRead(Ptr<Socket> s)
    {
        Address from;
        while (Ptr<Packet> p = s->RecvFrom(from))
        {
            if (p->GetSize() < sizeof(ReportMsg)) continue;
            std::vector<uint8_t> b(p->GetSize());
            p->CopyData(b.data(), b.size());
            ReportMsg m;
            std::memcpy(&m, b.data(), sizeof(ReportMsg));

            if (m.type == MSG_ACK) { OnAck(m.id); continue; }
            if (m.type != MSG_DATA || !m_up) continue;               // offline tower stays silent
            if (g_rng->GetValue(0.0, 1.0) < LossProb()) { LogEvent(m_name, "rx_lost", PktName(m.id)); continue; }

            if (m_seen.insert(m.id).second) Accept(m);               // new packet
            ReportMsg ack{};                                         // always ACK (also duplicates)
            ack.type = MSG_ACK; ack.id = m.id;
            s->SendTo(Create<Packet>(reinterpret_cast<uint8_t*>(&ack), sizeof(ack)), 0, from);
        }
    }

    void Accept(const ReportMsg& m)
    {
        LogEvent(m_name, "received", PktName(m.id));
        if (m_role == HUB)
        {
            g_delivered.push_back({m.id, m.findingType, m.severity, m.lat, m.lon, m.tCapture, Simulator::Now().GetSeconds(), g_attempts[m.id]});
        }
        else
        {
            Enqueue(m);
        }
    }

    std::string m_name; Role m_role = PRIMARY; Ipv4Address m_next; std::function<bool()> m_linkCheck;
    Ptr<Socket> m_socket; EventId m_tick; bool m_up = true;
    std::vector<ReportMsg> m_queue; size_t m_peak = 0; std::set<uint32_t> m_seen;
    bool m_inflight = false; uint32_t m_inflightId = 0; Time m_deadline;
    Ptr<Node> m_upNode; double m_upRange = 0;
};

// ------------------------------- flight controller ---------------------------
struct Poi { uint32_t id; Vector pos; uint32_t type; bool found; double tFound = -1; };

static Ptr<HopNode> g_relayApp; // relay application (for queue length in the trace)

class Controller
{
  public:
    enum State { SURVEY, OFFLOAD, RTL, DONE };

    Controller(Ptr<HopNode> primaryApp) : m_app(primaryApp)
    {
        // hidden findings (same idea as the web simulator: random, well separated)
        uint32_t tries = 0;
        while (m_pois.size() < g.numPois && tries++ < 2000)
        {
            Vector p(g_rng->GetValue(30, ZONE_W - 30), g_rng->GetValue(30, ZONE_H - 30), 0);
            bool ok = true;
            for (auto& q : m_pois) if (CalculateDistance(p, q.pos) < 80) { ok = false; break; }
            if (ok) m_pois.push_back({(uint32_t)m_pois.size() + 1, p, (uint32_t)g_rng->GetInteger(0, 3), false});
        }
        // lawnmower path: 7 rows, 100 m apart
        for (int i = 0; i < 7; ++i)
        {
            double y = 50 + i * 100, l = 50, r = ZONE_W - 50;
            if (i % 2 == 0) { m_path.push_back(Vector(l, y, 0)); m_path.push_back(Vector(r, y, 0)); }
            else            { m_path.push_back(Vector(r, y, 0)); m_path.push_back(Vector(l, y, 0)); }
        }
        LogEvent("Primary", "launch", "survey of " + std::to_string(m_pois.size()) + " hidden findings");
        Simulator::Schedule(Seconds(CTRL_DT), &Controller::Tick, this);
    }

    State GetState() const { return m_state; }

    void WritePois(const std::string& path) const
    {
        std::ofstream f(path);
        f << "poi_id,x,y,finding,severity,found_s\n";
        for (const auto& poi : m_pois)
        {
            const FindingType& ft = SCEN.at(g.scenario)[poi.type];
            f << poi.id << ',' << std::fixed << std::setprecision(1) << poi.pos.x << ',' << poi.pos.y << ',' << ft.name << ',' << ft.sev << ','
              << std::setprecision(2) << poi.tFound << '\n';
        }
    }

  private:
    // set velocity so that the drone heads to target; returns true on arrival
    static bool Steer(Ptr<ConstantVelocityMobilityModel> m, Vector target, double speed)
    {
        Vector p = m->GetPosition();
        double dx = target.x - p.x, dy = target.y - p.y, d = std::hypot(dx, dy);
        if (d < 1.0) { m->SetVelocity(Vector(0, 0, 0)); return true; }
        double v = std::min(speed, d / CTRL_DT);
        m->SetVelocity(Vector(dx / d * v, dy / d * v, 0));
        return false;
    }

    Vector RelayTarget()
    {
        if (g.relayMode == "static") return Vector(TOWER_POS.x - 0.85 * g.towerRange, TOWER_POS.y, 0);
        // adaptive: on the line tower -> primary, as close to the primary as the tower link allows
        Vector p = g_pMob->GetPosition();
        double vx = p.x - TOWER_POS.x, vy = p.y - TOWER_POS.y, d = std::max(1.0, std::hypot(vx, vy));
        double want = std::max(0.0, d - 0.85 * g.droneRange), lim = 0.92 * g.towerRange, k = std::min(want, lim);
        return Vector(TOWER_POS.x + vx / d * k, TOWER_POS.y + vy / d * k, 0);
    }

    void Capture()
    {
        Vector pp = g_pMob->GetPosition();
        for (auto& poi : m_pois)
        {
            if (poi.found || CalculateDistance(pp, poi.pos) > CAM_R) continue;
            poi.found = true;
            poi.tFound = Simulator::Now().GetSeconds();
            const FindingType& ft = SCEN.at(g.scenario)[poi.type];
            ReportMsg m{};
            m.type = MSG_DATA; m.id = ++g_captured; m.severity = ft.sev; m.findingType = poi.type;
            m.lat = LAT0 - poi.pos.y / 111000.0;
            m.lon = LON0 + poi.pos.x / (111000.0 * std::cos(LAT0 * M_PI / 180.0));
            m.tCapture = Simulator::Now().GetSeconds();
            m.payloadBytes = g.imageBytes + 2000 * ft.sev;
            LogEvent("Primary", "captured", PktName(m.id) + " " + ft.name);
            m_app->Enqueue(m);
        }
    }

    void Tick()
    {
        // ---- primary drone state machine ----
        if (m_state == SURVEY)
        {
            if (Steer(g_pMob, m_path[m_wi], P_SPEED) && ++m_wi >= m_path.size())
            {
                m_state = OFFLOAD;
                LogEvent("Primary", "survey_done", "flying to relay to offload " + std::to_string(m_app->QueueSize()) + " packets");
            }
        }
        else if (m_state == OFFLOAD)
        {
            // fly toward the relay until in range, then hover until the buffer is empty
            if (g_pMob->GetDistanceFrom(g_rMob) > 0.6 * g.droneRange) Steer(g_pMob, g_rMob->GetPosition(), P_SPEED);
            else g_pMob->SetVelocity(Vector(0, 0, 0));
            if (!m_app->HasWork()) { m_state = RTL; LogEvent("Primary", "offload_done", "returning to base"); }
        }
        else if (m_state == RTL)
        {
            if (Steer(g_pMob, BASE, P_SPEED * 1.3)) { m_state = DONE; LogEvent("Primary", "landed", "mission complete"); }
        }
        if (m_state != DONE) Capture();

        // ---- relay drone ----
        Steer(g_rMob, RelayTarget(), R_SPEED);

        // ---- log link changes for the dashboard timeline ----
        bool pr = g_pMob->GetDistanceFrom(g_rMob) <= g.droneRange;
        bool rt = g_towerUp && g_rMob->GetDistanceFrom(g_tMob) <= g.towerRange;
        if (pr != m_lastPR) { LogEvent("Link", pr ? "up" : "down", "Primary-Relay"); m_lastPR = pr; }
        if (rt != m_lastRT) { LogEvent("Link", rt ? "up" : "down", "Relay-Tower"); m_lastRT = rt; }

        // ---- movement / state trace for the dashboard replay ----
        {
            static const char* names[] = {"survey", "offload", "rtl", "done"};
            Vector pp = g_pMob->GetPosition(), rp = g_rMob->GetPosition();
            g_trace << std::fixed << std::setprecision(2) << Simulator::Now().GetSeconds() << ',' << std::setprecision(1) << pp.x << ',' << pp.y
                    << ',' << rp.x << ',' << rp.y << ',' << names[m_state] << ',' << m_app->QueueSize() << ','
                    << (g_relayApp ? g_relayApp->QueueSize() : 0) << ',' << (pr ? 1 : 0) << ',' << (rt ? 1 : 0) << ',' << (g_towerUp ? 1 : 0)
                    << ',' << g_delivered.size() << ',' << g_captured << '\n';
        }

        // ---- finish early once everything has been delivered ----
        if (m_state == DONE && g_delivered.size() == g_captured && !m_stopping)
        {
            m_stopping = true;
            Simulator::Schedule(Seconds(3), []() { Simulator::Stop(); });
        }
        Simulator::Schedule(Seconds(CTRL_DT), &Controller::Tick, this);
    }

    Ptr<HopNode> m_app; std::vector<Poi> m_pois; std::vector<Vector> m_path; size_t m_wi = 0;
    State m_state = SURVEY; bool m_lastPR = false, m_lastRT = false, m_stopping = false;
};

// ------------------------------- helpers -------------------------------------
static std::string NodeName(Ipv4Address a)
{
    std::ostringstream s; a.Print(s);
    std::string ip = s.str();
    if (ip == "10.1.1.1") return "Primary";
    if (ip == "10.1.1.2") return "Relay";
    if (ip == "10.1.1.3" || ip == "10.1.2.1") return "Tower";
    if (ip == "10.1.2.2") return "Hub";
    return ip;
}

// ------------------------------- main ----------------------------------------
int main(int argc, char* argv[])
{
    CommandLine cmd(__FILE__);
    cmd.AddValue("scenario", "flood | earthquake | landslide | fire", g.scenario);
    cmd.AddValue("relayMode", "adaptive | static", g.relayMode);
    cmd.AddValue("droneRange", "Drone-to-drone range (m)", g.droneRange);
    cmd.AddValue("towerRange", "Tower coverage radius (m)", g.towerRange);
    cmd.AddValue("loss", "Base packet loss probability (0..1)", g.loss);
    cmd.AddValue("imageBytes", "Base size of one report (image) in bytes", g.imageBytes);
    cmd.AddValue("numPois", "Number of hidden findings", g.numPois);
    cmd.AddValue("towerDownStart", "Tower outage start (s), -1 = never", g.towerDownStart);
    cmd.AddValue("towerDownEnd", "Tower outage end (s)", g.towerDownEnd);
    cmd.AddValue("simTime", "Maximum simulation time (s)", g.simTime);
    cmd.AddValue("seed", "RNG seed", g.seed);
    cmd.AddValue("outDir", "Output directory", g.outDir);
    cmd.Parse(argc, argv);
    if (!SCEN.count(g.scenario)) { std::cerr << "Unknown scenario\n"; return 1; }

    RngSeedManager::SetSeed(g.seed);
    g_rng = CreateObject<UniformRandomVariable>();
    std::filesystem::create_directories(g.outDir);
    g_events.open(g.outDir + "/events.csv");
    g_events << "time_s,node,event,detail\n";
    g_trace.open(g.outDir + "/trace.csv");
    g_trace << "time_s,px,py,rx,ry,pstate,pq,rq,link_pr,link_rt,tower_up,delivered,captured\n";

    // ---- nodes: 0 primary, 1 relay, 2 tower, 3 hub ----
    NodeContainer nodes; nodes.Create(4);
    Ptr<Node> P = nodes.Get(0), R = nodes.Get(1), T = nodes.Get(2), H = nodes.Get(3);

    MobilityHelper mh;
    mh.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
    mh.Install(NodeContainer(P, R));
    mh.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mh.Install(NodeContainer(T, H));
    g_pMob = P->GetObject<ConstantVelocityMobilityModel>();
    g_rMob = R->GetObject<ConstantVelocityMobilityModel>();
    g_tMob = T->GetObject<MobilityModel>();
    g_pMob->SetPosition(BASE);
    g_rMob->SetPosition(Vector(TOWER_POS.x - 0.85 * g.towerRange, TOWER_POS.y, 0));
    g_tMob->SetPosition(TOWER_POS);
    H->GetObject<MobilityModel>()->SetPosition(HUB_POS);

    // ---- Wi-Fi ad-hoc among primary, relay and tower ----
    double maxRange = std::max(g.droneRange, g.towerRange) + 100;
    YansWifiChannelHelper chan;
    chan.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    chan.AddPropagationLoss("ns3::RangePropagationLossModel", "MaxRange", DoubleValue(maxRange));
    YansWifiPhyHelper phy;
    phy.SetChannel(chan.Create());
    WifiMacHelper mac;
    mac.SetType("ns3::AdhocWifiMac");
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211b);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager", "DataMode", StringValue("DsssRate2Mbps"),
                                 "ControlMode", StringValue("DsssRate1Mbps"));
    NetDeviceContainer wifiDev = wifi.Install(phy, mac, NodeContainer(P, R, T));

    // ---- wired backhaul tower -> hub ----
    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2p.SetChannelAttribute("Delay", StringValue("5ms"));
    NetDeviceContainer p2pDev = p2p.Install(NodeContainer(T, H));

    InternetStackHelper inet;
    inet.Install(nodes);
    Ipv4AddressHelper ip;
    ip.SetBase("10.1.1.0", "255.255.255.0");
    Ipv4InterfaceContainer wifiIf = ip.Assign(wifiDev);
    ip.SetBase("10.1.2.0", "255.255.255.0");
    Ipv4InterfaceContainer p2pIf = ip.Assign(p2pDev);
    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // ---- store-and-forward applications ----
    Ptr<HopNode> aP = CreateObject<HopNode>(), aR = CreateObject<HopNode>(), aT = CreateObject<HopNode>(), aH = CreateObject<HopNode>();
    aP->Configure("Primary", HopNode::PRIMARY, wifiIf.GetAddress(1),
                  []() { return g_pMob->GetDistanceFrom(g_rMob) <= g.droneRange; });
    aR->Configure("Relay", HopNode::RELAY, wifiIf.GetAddress(2),
                  []() { return g_towerUp && g_rMob->GetDistanceFrom(g_tMob) <= g.towerRange; });
    aT->Configure("Tower", HopNode::TOWER, p2pIf.GetAddress(1), []() { return g_towerUp; });
    aH->Configure("Hub", HopNode::HUB, Ipv4Address::GetAny(), nullptr);
    aR->SetUpstream(P, g.droneRange);
    aT->SetUpstream(R, g.towerRange);
    P->AddApplication(aP); R->AddApplication(aR); T->AddApplication(aT); H->AddApplication(aH);
    for (auto a : {aP, aR, aT, aH}) { a->SetStartTime(Seconds(0)); a->SetStopTime(Seconds(g.simTime)); }

    g_relayApp = aR;
    Controller ctrl(aP);

    // ---- optional tower outage ----
    if (g.towerDownStart >= 0)
    {
        Simulator::Schedule(Seconds(g.towerDownStart), [aT]() { g_towerUp = false; aT->SetUp(false); LogEvent("Tower", "offline", "tower outage begins"); });
        if (g.towerDownEnd > g.towerDownStart)
            Simulator::Schedule(Seconds(g.towerDownEnd), [aT]() { g_towerUp = true; aT->SetUp(true); LogEvent("Tower", "online", "tower restored"); });
    }

    // ---- FlowMonitor ----
    FlowMonitorHelper fmh;
    Ptr<FlowMonitor> monitor = fmh.InstallAll();

    Simulator::Stop(Seconds(g.simTime));
    Simulator::Run();
    double endT = Simulator::Now().GetSeconds();

    // ============================ write results ============================
    monitor->CheckForLostPackets();
    monitor->SerializeToXmlFile(g.outDir + "/flowmon.xml", true, true);

    std::ofstream fr(g.outDir + "/reports.csv");
    fr << "id,finding,severity,lat,lon,captured_s,received_s,latency_s,tx_attempts\n";
    double latSum = 0;
    std::sort(g_delivered.begin(), g_delivered.end(), [](const Delivered& a, const Delivered& b) { return a.tHub < b.tHub; });
    for (auto& d : g_delivered)
    {
        latSum += d.tHub - d.tCapture;
        fr << "PKT-" << std::setw(3) << std::setfill('0') << d.id << std::setfill(' ') << ',' << SCEN.at(g.scenario)[d.findingType].name << ','
           << d.severity << ',' << std::fixed << std::setprecision(6) << d.lat << ',' << d.lon << ',' << std::setprecision(3) << d.tCapture
           << ',' << d.tHub << ',' << (d.tHub - d.tCapture) << ',' << d.attempts << '\n';
    }
    fr.close();

    std::ofstream ff(g.outDir + "/flows.csv");
    ff << "flow_id,src,dst,tx_packets,rx_packets,lost_packets,tx_bytes,rx_bytes,mean_delay_ms,throughput_kbps\n";
    Ptr<Ipv4FlowClassifier> cls = DynamicCast<Ipv4FlowClassifier>(fmh.GetClassifier());
    for (auto& kv : monitor->GetFlowStats())
    {
        Ipv4FlowClassifier::FiveTuple t = cls->FindFlow(kv.first);
        const FlowMonitor::FlowStats& s = kv.second;
        double delayMs = s.rxPackets ? s.delaySum.GetSeconds() * 1000.0 / s.rxPackets : 0.0;
        double span = (s.timeLastRxPacket - s.timeFirstTxPacket).GetSeconds();
        double thr = span > 0 ? s.rxBytes * 8.0 / span / 1000.0 : 0.0;
        ff << kv.first << ',' << NodeName(t.sourceAddress) << ',' << NodeName(t.destinationAddress) << ',' << s.txPackets << ',' << s.rxPackets
           << ',' << s.lostPackets << ',' << s.txBytes << ',' << s.rxBytes << ',' << std::fixed << std::setprecision(3) << delayMs << ',' << thr << '\n';
    }
    ff.close();

    std::ofstream fj(g.outDir + "/run.json");
    fj << "{\n"
       << "  \"scenario\": \"" << g.scenario << "\",\n"
       << "  \"relay_mode\": \"" << g.relayMode << "\",\n"
       << "  \"drone_range_m\": " << g.droneRange << ",\n"
       << "  \"tower_range_m\": " << g.towerRange << ",\n"
       << "  \"loss_base\": " << g.loss << ",\n"
       << "  \"image_bytes\": " << g.imageBytes << ",\n"
       << "  \"seed\": " << g.seed << ",\n"
       << "  \"tower_down_start\": " << g.towerDownStart << ",\n"
       << "  \"tower_down_end\": " << g.towerDownEnd << ",\n"
       << "  \"captured\": " << g_captured << ",\n"
       << "  \"delivered\": " << g_delivered.size() << ",\n"
       << "  \"avg_latency_s\": " << std::fixed << std::setprecision(3) << (g_delivered.empty() ? 0.0 : latSum / g_delivered.size()) << ",\n"
       << "  \"peak_primary_queue\": " << aP->PeakQueue() << ",\n"
       << "  \"peak_relay_queue\": " << aR->PeakQueue() << ",\n"
       << "  \"sim_end_s\": " << endT << "\n}\n";
    fj.close();
    g_events.close();
    g_trace.close();
    ctrl.WritePois(g.outDir + "/pois.csv");

    std::cout << "Run finished at t=" << endT << " s | captured=" << g_captured << " delivered=" << g_delivered.size()
              << " avg latency=" << (g_delivered.empty() ? 0.0 : latSum / g_delivered.size()) << " s | output: " << g.outDir << "\n";
    Simulator::Destroy();
    return 0;
}
