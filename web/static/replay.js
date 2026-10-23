/* Replay player: animates the movement trace recorded by the NS-3 simulation.
   Data comes from /api/runs/<id>/replay (positions every 0.5 s, link states, queue sizes)
   and from the run's event timeline (which hop forwarded which report, and when). */
(() => {
  const $ = id => document.getElementById(id);
  const SEV_COL = { 3: '#C4381F', 2: '#D9820B', 1: '#51636B' };
  const SEV_LABEL = { 3: 'Critical', 2: 'High', 1: 'Moderate' };
  const WORLD = { x0: -40, y0: -40, w: 1440, h: 860 };      // metres shown on screen
  const STATE_TEXT = { survey: 'surveying the zone', offload: 'handing data to relay', rtl: 'returning to base', done: 'landed' };

  const cv = $('rpCanvas'), ctx = cv.getContext('2d');
  const S = { data: null, run: null, reports: [], hops: [], sevOf: {}, dur: 0, t: 0, speed: 10,
              playing: false, last: 0, path: [], inboxKey: '', statusKey: '' };
  let K = 1, OX = 0, OY = 0, W = 0, H = 0, dpr = 1;

  const mmss = s => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  const sx = x => OX + x * K, sy = y => OY + y * K;
  const isDark = () => window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const norm = d => String(d).replace(/PKT-(\d+)/, (m, n) => 'PKT-' + n.padStart(3, '0'));

  /* --------------------------------------------------------------- loading */
  async function load(runId, run, reports, events) {
    stop();
    S.run = run; S.reports = reports; S.t = 0; S.inboxKey = ''; S.statusKey = '';
    S.sevOf = {}; reports.forEach(r => { S.sevOf[r.pkt_id] = r.severity; });
    let res;
    try { res = await (await fetch(`/api/runs/${runId}/replay`)).json(); } catch (e) { res = { trace: [] }; }
    if (!res.trace || !res.trace.length) {
      S.data = null; $('rpNote').textContent = 'This run has no movement data. Run it again with the updated simulator.';
      resize(); draw(); updateUi(); return;
    }
    S.data = res; S.dur = res.trace[res.trace.length - 1][0];
    S.hops = events.filter(e => e.event === 'forwarded')
      .map(e => ({ t: e.time_s, node: e.node, sev: S.sevOf[norm(e.detail)] || 2 })).sort((a, b) => a.t - b.t);
    S.path = [];
    for (let i = 0; i < 7; i++) {                      // same lawnmower path as the simulator
      const y = 50 + i * 100, l = 50, r = res.layout.zone.w - 50;
      if (i % 2 === 0) S.path.push([l, y], [r, y]); else S.path.push([r, y], [l, y]);
    }
    $('rpNote').textContent = 'Drone positions, links and packets come from the NS-3 run';
    $('rpScrub').max = S.dur; $('rpScrub').value = 0;
    resize(); draw(); updateUi();
  }

  /* ---------------------------------------------------------- time helpers */
  function stateAt(t) {
    const tr = S.data.trace;
    let lo = 0, hi = tr.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (tr[mid][0] <= t) lo = mid; else hi = mid - 1; }
    const a = tr[lo], b = tr[Math.min(lo + 1, tr.length - 1)], dt = b[0] - a[0];
    const f = dt > 0 ? Math.min(1, Math.max(0, (t - a[0]) / dt)) : 0;
    return { row: a, p: { x: a[1] + (b[1] - a[1]) * f, y: a[2] + (b[2] - a[2]) * f },
             r: { x: a[3] + (b[3] - a[3]) * f, y: a[4] + (b[4] - a[4]) * f } };
  }
  // packet dots are shown for a time that scales with the speed so they stay visible
  const flight = () => Math.max(0.9, 0.8 * S.speed);

  /* ---------------------------------------------------------------- canvas */
  function resize() {
    dpr = window.devicePixelRatio || 1;
    W = cv.clientWidth || 900; H = Math.round(W * 0.56);
    cv.style.height = H + 'px'; cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    K = Math.min(W / WORLD.w, H / WORLD.h);
    OX = (W - WORLD.w * K) / 2 - WORLD.x0 * K; OY = (H - WORLD.h * K) / 2 - WORLD.y0 * K;
    if (S.data) draw();
  }
  function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

  function drawDrone(p, color, label, queue) {
    const x = sx(p.x), y = sy(p.y), a = S.t * 14;
    ctx.save(); ctx.translate(x, y); ctx.strokeStyle = color; ctx.lineWidth = 2;
    for (const [ox, oy] of [[-9, -9], [9, -9], [-9, 9], [9, 9]]) {
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(ox, oy); ctx.stroke();
      ctx.fillStyle = color + '55'; ctx.beginPath(); ctx.arc(ox, oy, 5.5, 0, 7); ctx.fill();
      ctx.beginPath(); ctx.moveTo(ox + Math.cos(a) * 5.5, oy + Math.sin(a) * 5.5); ctx.lineTo(ox - Math.cos(a) * 5.5, oy - Math.sin(a) * 5.5); ctx.stroke();
    }
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(0, 0, 6, 0, 7); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(0, 0, 2, 0, 7); ctx.fill();
    ctx.fillStyle = css('--ink'); ctx.font = '600 12px "Public Sans", sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(label, 0, 26);
    if (queue > 0) {                                   // queue badge
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(14, -14, 9, 0, 7); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = '700 11px "Public Sans", sans-serif'; ctx.fillText(queue, 14, -10);
    }
    ctx.restore();
  }

  function dashedLine(a, b, color) {
    ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.setLineDash([7, 6]); ctx.lineDashOffset = -S.t * 6 * K;
    ctx.beginPath(); ctx.moveTo(sx(a.x), sy(a.y)); ctx.lineTo(sx(b.x), sy(b.y)); ctx.stroke(); ctx.restore();
  }
  function packet(a, b, f, sev) {
    const x = sx(a.x + (b.x - a.x) * f), y = sy(a.y + (b.y - a.y) * f);
    ctx.fillStyle = SEV_COL[sev]; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.rect(x - 5.5, y - 4.5, 11, 9); ctx.fill(); ctx.stroke();
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = isDark() ? '#101C20' : '#EAEFEA'; ctx.fillRect(0, 0, W, H);
    if (!S.data) return;
    const L = S.data.layout, tower = L.tower, hub = L.hub, base = L.base;
    const st = stateAt(S.t), row = st.row, towerUp = row[10] === 1;

    // grid every 100 m
    ctx.strokeStyle = 'rgba(120,140,140,.16)'; ctx.lineWidth = 1; ctx.beginPath();
    for (let x = 0; x <= 1400; x += 100) { ctx.moveTo(sx(x), sy(-40)); ctx.lineTo(sx(x), sy(820)); }
    for (let y = 0; y <= 800; y += 100) { ctx.moveTo(sx(-40), sy(y)); ctx.lineTo(sx(1400), sy(y)); }
    ctx.stroke();

    // disaster zone
    ctx.fillStyle = 'rgba(196,56,31,.10)'; ctx.fillRect(sx(0), sy(0), L.zone.w * K, L.zone.h * K);
    ctx.save(); ctx.strokeStyle = '#C4381F'; ctx.lineWidth = 2; ctx.setLineDash([8, 6]);
    ctx.strokeRect(sx(0), sy(0), L.zone.w * K, L.zone.h * K); ctx.restore();
    ctx.fillStyle = '#C4381F'; ctx.font = '600 12px "Public Sans", sans-serif'; ctx.textAlign = 'left';
    ctx.fillText('Disaster zone: no mobile network', sx(4), sy(-12));

    // planned survey path
    ctx.save(); ctx.strokeStyle = 'rgba(31,95,191,.30)'; ctx.setLineDash([3, 6]); ctx.beginPath();
    S.path.forEach((p, i) => i ? ctx.lineTo(sx(p[0]), sy(p[1])) : ctx.moveTo(sx(p[0]), sy(p[1]))); ctx.stroke(); ctx.restore();

    // tower coverage
    const tr = (S.run.tower_range_m || 600) * K;
    ctx.fillStyle = towerUp ? 'rgba(30,143,90,.10)' : 'rgba(196,56,31,.10)';
    ctx.strokeStyle = towerUp ? 'rgba(30,143,90,.85)' : 'rgba(196,56,31,.85)'; ctx.lineWidth = 1.5; ctx.setLineDash([6, 5]);
    ctx.beginPath(); ctx.arc(sx(tower.x), sy(tower.y), tr, 0, 7); ctx.fill(); ctx.stroke(); ctx.setLineDash([]);

    // wired backhaul + hub + tower + base
    ctx.strokeStyle = css('--muted'); ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(sx(tower.x), sy(tower.y)); ctx.lineTo(sx(hub.x), sy(hub.y)); ctx.stroke();
    ctx.fillStyle = towerUp ? '#1E8F5A' : '#C4381F';
    ctx.beginPath(); ctx.moveTo(sx(tower.x), sy(tower.y) - 20); ctx.lineTo(sx(tower.x) - 11, sy(tower.y) + 12); ctx.lineTo(sx(tower.x) + 11, sy(tower.y) + 12); ctx.fill();
    ctx.fillStyle = css('--ink'); ctx.font = '600 12px "Public Sans", sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(towerUp ? 'Communication tower' : 'Tower OFFLINE', sx(tower.x), sy(tower.y) + 30);
    ctx.fillRect(sx(hub.x) - 16, sy(hub.y) - 22, 32, 44);
    ctx.fillStyle = isDark() ? '#101C20' : '#EAEFEA';
    for (let r = 0; r < 4; r++) for (let c = 0; c < 2; c++) ctx.fillRect(sx(hub.x) - 10 + c * 12, sy(hub.y) - 16 + r * 10, 7, 6);
    ctx.fillStyle = css('--ink'); ctx.fillText('Main hub (NGO)', sx(hub.x), sy(hub.y) + 38);
    ctx.strokeStyle = css('--muted'); ctx.lineWidth = 2; ctx.strokeRect(sx(base.x) - 10, sy(base.y) - 10, 20, 20);
    ctx.fillStyle = css('--muted'); ctx.fillText('Launch base', sx(base.x) + 58, sy(base.y) + 4);

    // findings: grey until found, then coloured by severity
    for (const p of S.data.pois) {
      const found = p.found_s >= 0 && p.found_s <= S.t;
      ctx.beginPath(); ctx.arc(sx(p.x), sy(p.y), found ? 7 : 3.5, 0, 7);
      ctx.fillStyle = found ? SEV_COL[p.severity] : '#9AA9AE'; ctx.fill();
      if (found) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke(); }
    }

    // radio ranges and camera footprint
    ctx.save(); ctx.strokeStyle = 'rgba(217,130,11,.6)'; ctx.setLineDash([4, 6]); ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(sx(st.r.x), sy(st.r.y), (S.run.drone_range_m || 450) * K, 0, 7); ctx.stroke(); ctx.restore();
    if (row[5] !== 'done') { ctx.fillStyle = 'rgba(31,95,191,.12)'; ctx.beginPath(); ctx.arc(sx(st.p.x), sy(st.p.y), L.cam_r * K, 0, 7); ctx.fill(); }

    // active links
    if (row[8] === 1) dashedLine(st.p, st.r, '#1E8F5A');
    if (row[9] === 1) dashedLine(st.r, tower, '#1E8F5A');

    // packets in flight (hop completes at time h.t)
    const win = flight();
    for (const h of S.hops) {
      if (h.t > S.t + win * 1.7) break;
      if (h.node === 'Primary' && h.t >= S.t && h.t <= S.t + win) packet(st.p, st.r, 1 - (h.t - S.t) / win, h.sev);
      else if (h.node === 'Relay' && h.t >= S.t && h.t <= S.t + win) packet(st.r, tower, 1 - (h.t - S.t) / win, h.sev);
      else if (h.node === 'Tower' && S.t >= h.t && S.t <= h.t + win * 0.6) packet(tower, hub, (S.t - h.t) / (win * 0.6), h.sev);
    }

    drawDrone(st.r, '#D9820B', 'Relay drone', row[7]);
    drawDrone(st.p, '#1F5FBF', 'Primary drone', row[6]);
  }

  /* ----------------------------------------------------- status + hub inbox */
  function arrived() {
    const atEnd = S.t >= S.dur - 0.01, lag = flight() * 0.6;
    return S.reports.filter(r => atEnd || r.received_s + lag <= S.t).sort((a, b) => a.received_s - b.received_s);
  }
  function updateUi() {
    $('rpScrub').value = S.t; $('rpTime').textContent = `${mmss(S.t)} / ${mmss(S.dur)}`;
    $('rpPlay').textContent = S.playing ? 'Pause' : 'Play';
    if (!S.data) { $('rpStatus').innerHTML = ''; $('rpInbox').innerHTML = ''; return; }
    const row = stateAt(S.t).row, got = arrived(), total = S.data.pois.length;
    const chip = (txt, cls) => `<span class="chip ${cls || ''}">${txt}</span>`;
    const prLink = row[8] === 1 ? chip('Primary and relay: connected', 'ok') : chip('Primary and relay: out of range, data waits in buffer', 'bad');
    const rtLink = row[10] === 0 ? chip('Relay and tower: tower offline', 'bad') : (row[9] === 1 ? chip('Relay and tower: connected', 'ok') : chip('Relay and tower: out of range', 'bad'));
    const key = [row[5], row[6], row[7], row[8], row[9], row[10], row[12], got.length].join('|');
    if (key !== S.statusKey) {
      S.statusKey = key;
      $('rpStatus').innerHTML = chip('Primary drone: ' + (STATE_TEXT[row[5]] || row[5])) + chip(`Found ${row[12]} of ${total}`) +
        chip(`Received at hub ${got.length} of ${row[12]} found`) +
        chip(`Primary queue ${row[6]}`) + chip(`Relay queue ${row[7]}`) + prLink + rtLink;
    }
    const ikey = String(got.length);
    if (ikey !== S.inboxKey) {
      S.inboxKey = ikey;
      $('rpInbox').innerHTML = got.length ? got.slice(-5).reverse().map(r =>
        `<div class="rpi s${r.severity}"><i></i><div><b>${esc(r.finding)}</b> (${esc(r.severity_label)}, ${esc(r.pkt_id)}) reached the hub, delay ${Number(r.latency_s).toFixed(1)} s</div><span>${mmss(r.received_s)}</span></div>`).join('')
        : '<span class="mono">Hub inbox is empty. Press Play.</span>';
    }
  }
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* -------------------------------------------------------------- controls */
  function tick(ts) {
    if (!S.playing) return;
    const dt = Math.min(0.1, (ts - S.last) / 1000); S.last = ts;
    S.t = Math.min(S.dur, S.t + dt * S.speed);
    if (S.t >= S.dur) S.playing = false;
    draw(); updateUi();
    if (S.playing) requestAnimationFrame(tick);
  }
  function play() {
    if (!S.data) return;
    if (S.t >= S.dur - 0.01) S.t = 0;
    S.playing = true; S.last = performance.now(); updateUi(); requestAnimationFrame(tick);
  }
  function stop() { S.playing = false; }

  $('rpPlay').onclick = () => { if (S.playing) { stop(); updateUi(); } else play(); };
  $('rpRestart').onclick = () => { S.t = 0; if (S.data) { draw(); updateUi(); } play(); };
  $('rpSpeed').onchange = e => { S.speed = +e.target.value; };
  $('rpScrub').oninput = e => { S.t = +e.target.value; if (S.data) { draw(); updateUi(); } };
  window.addEventListener('resize', resize);
  if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => { draw(); });

  window.Replay = { load, play, stop, resize };
})();
