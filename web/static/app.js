/* NGO dashboard: talks to the Flask API in backend/app.py */
(() => {
  const SEV = { 3: 'Critical', 2: 'High', 1: 'Moderate' };
  const SEV_COL = { 3: '#FB7185', 2: '#FBBF24', 1: '#94A3B8' };
  const $ = id => document.getElementById(id);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (v, d = 1) => (v === null || v === undefined || isNaN(v)) ? '-' : Number(v).toFixed(d);
  const mmss = s => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  async function api(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(url + ' returned ' + r.status);
    return r.json();
  }

  // same fictional geo origin as the NS-3 simulation (1 unit = 1 metre there)
  const LAT0 = 13.3700, LON0 = 77.0900;
  const toLatLon = (x, y) => [LAT0 - y / 111000, LON0 + x / (111000 * Math.cos(LAT0 * Math.PI / 180))];

  const state = { runs: [], runId: null, reports: [], flows: [], events: [], compare: [], summary: null,
                  map: null, layer: null, markers: {}, chLat: null, chCmp: null };

  /* ------------------------------------------------------------ start-up */
  async function init() {
    try {
      state.runs = await api('/api/runs');
    } catch (e) {
      $('empty').hidden = false; $('empty').querySelector('p').textContent = 'Could not reach the API: ' + e.message; return;
    }
    if (!state.runs.length) { $('empty').hidden = false; return; }
    $('app').hidden = false;
    rebuildRunSelect();
    $('runSelect').onchange = e => loadRun(+e.target.value);
    initTabs();
    initToggleRunner();
    initRunner();
    $('fSev').onchange = $('fStatus').onchange = () => { renderList(); renderMarkers(false); };
    $('evAll').onchange = renderEvents;
    $('reportList').addEventListener('click', onListClick);
    initMap();
    await loadRun(state.runs[0].id);
  }

  function rebuildRunSelect() {
    $('runSelect').innerHTML = state.runs.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
  }

  /* ------------------------------------------------- tabs & runner drawer */
  function initTabs() {
    const tabs = document.querySelectorAll('.tab-btn');
    tabs.forEach(btn => {
      btn.addEventListener('click', () => {
        tabs.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const target = btn.dataset.tab;
        document.querySelectorAll('.tab-panel').forEach(p => {
          p.hidden = (target !== 'tab-all' && p.id !== target);
        });
        setTimeout(() => {
          if (state.map) state.map.invalidateSize();
          if (window.Replay) window.Replay.resize();
          if (state.chLat) state.chLat.resize();
          if (state.chCmp) state.chCmp.resize();
        }, 50);
      });
    });
  }

  function initToggleRunner() {
    const btn = $('btnToggleRunner');
    const runner = $('runner');
    if (!btn || !runner) return;
    btn.addEventListener('click', () => {
      runner.open = !runner.open;
      if (runner.open) {
        runner.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  }

  /* ------------------------------------------------- run a new simulation */
  function initRunner() {
    const link = (id, out, f) => { const el = $(id), o = $(out); const upd = () => { o.textContent = f(+el.value); }; el.oninput = upd; upd(); };
    link('rRange', 'rRangeV', v => v + ' m'); link('rTower', 'rTowerV', v => v + ' m'); link('rLoss', 'rLossV', v => v + '%');
    $('runForm').addEventListener('submit', async e => {
      e.preventDefault();
      const msg = $('rMsg'), btn = $('rGo');
      const body = { scenario: $('rScen').value, relayMode: $('rMode').value, droneRange: +$('rRange').value,
                     towerRange: +$('rTower').value, loss: +$('rLoss').value / 100, seed: +$('rSeed').value || 1,
                     name: $('rName').value.trim() };
      const d1 = $('rDown1').value, d2 = $('rDown2').value;
      if (d1 !== '' || d2 !== '') { body.towerDownStart = d1 === '' ? 0 : +d1; body.towerDownEnd = d2 === '' ? 900 : +d2; }
      btn.disabled = true; msg.className = 'hint'; msg.textContent = 'Running simulation ...';
      try {
        const r = await fetch('/api/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        state.runs = await api('/api/runs'); rebuildRunSelect();
        await loadRun(j.run_id);
        msg.textContent = j.summary;
        
        // Switch to replay tab so user immediately sees results
        const replayTabBtn = document.querySelector('.tab-btn[data-tab="tab-replay"]');
        if (replayTabBtn && !replayTabBtn.classList.contains('active')) replayTabBtn.click();
        if ($('runner')) $('runner').open = false;
        if ($('replay').scrollIntoView) $('replay').scrollIntoView({ behavior: 'smooth', block: 'start' });
        if (window.Replay) Replay.play();
      } catch (err) { msg.className = 'hint err'; msg.textContent = err.message; }
      finally { btn.disabled = false; }
    });
  }

  async function loadRun(id) {
    state.runId = id; $('runSelect').value = id;
    const [summary, reports, flows, events, compare] = await Promise.all([
      api(`/api/runs/${id}/summary`), api(`/api/runs/${id}/reports`), api(`/api/runs/${id}/flows`),
      api(`/api/runs/${id}/events`), api('/api/compare')]);
    Object.assign(state, { summary, reports, flows, events, compare });
    renderSummary(); renderList(); renderMarkers(true); renderLatencyChart(); renderCompareChart(); renderFlows(); renderEvents();
    if (window.Replay) Replay.load(id, summary, reports, events).catch(console.error);
  }

  /* ------------------------------------------------------------- summary */
  function renderSummary() {
    const s = state.summary;
    $('sDelivered').textContent = `${s.delivered} of ${s.captured}`;
    $('sRatio').textContent = `${Math.round(s.delivery_ratio * 100)}% reached the hub`;
    $('sAvg').textContent = fmt(s.avg_latency_s) + ' s';
    $('sRange').textContent = `fastest ${fmt(s.min_latency_s)} s`;
    $('sMax').textContent = fmt(s.max_latency_s) + ' s';
    $('sRetx').textContent = s.retransmissions;
    $('sDisp').textContent = (s.dispatched || 0) + (s.resolved || 0);
    $('sResolved').textContent = `${s.resolved || 0} resolved`;
    const chips = [
      `Scenario: ${s.scenario}`, `Relay: ${s.relay_mode}`, `Drone Range: ${s.drone_range_m} m`,
      `Tower Radius: ${s.tower_range_m} m`, `Base Loss: ${Math.round(s.loss_base * 100)}%`,
      `Payload: ${(s.image_bytes / 1000).toFixed(0)} KB`, `Seed: ${s.seed}`];
    let html = chips.map(c => `<span class="chip">${esc(c)}</span>`).join('');
    if (s.tower_down_start >= 0) html += `<span class="chip warn">Tower outage: ${s.tower_down_start}s to ${s.tower_down_end}s</span>`;
    $('params').innerHTML = html;
    const sev = s.by_severity.map(x => `<span class="chip">${esc(x.label)}: average <b>${fmt(x.avg_latency_s)} s</b> (${x.n} report${x.n === 1 ? '' : 's'})</span>`).join('');
    $('sevRow').innerHTML = sev;
  }

  /* ---------------------------------------------------------- report list */
  function filtered() {
    const sev = $('fSev').value, st = $('fStatus').value;
    return state.reports.filter(r => (!sev || r.severity === +sev) && (!st || r.status === st));
  }
  function actionButtons(r) {
    if (r.status === 'new') return `<button class="act" data-act="dispatched" data-id="${r.id}">Dispatch team</button>`;
    if (r.status === 'dispatched') return `<button class="act" data-act="resolved" data-id="${r.id}">Mark resolved</button>`;
    return `<button class="act ghost" data-act="new" data-id="${r.id}">Reopen</button>`;
  }
  function renderList() {
    const list = filtered();
    if (!list.length) { $('reportList').innerHTML = '<div class="none">No reports match these filters.</div>'; return; }
    $('reportList').innerHTML = list.map(r => `
      <div class="item s${r.severity}" data-focus="${r.id}">
        <div class="bar"></div>
        <div>
          <div class="row1"><b>${esc(r.finding)}</b><span class="badge s${r.severity}">${esc(r.severity_label)}</span></div>
          <div class="meta">${esc(r.pkt_id)} • ${fmt(r.lat, 5)}, ${fmt(r.lon, 5)}</div>
          <div class="meta">Delivery Delay: ${fmt(r.latency_s)} s${r.retransmissions ? ` • Resent ${r.retransmissions}x` : ''}</div>
          <div class="acts">${actionButtons(r)}<span class="st ${r.status}">${esc(r.status)}</span></div>
        </div>
      </div>`).join('');
  }
  function onListClick(e) {
    const btn = e.target.closest('button[data-act]');
    if (btn) { setStatus(+btn.dataset.id, btn.dataset.act); return; }
    const item = e.target.closest('[data-focus]');
    if (item && state.map) {
      const m = state.markers[item.dataset.focus];
      if (m) { state.map.flyTo(m.getLatLng(), Math.max(state.map.getZoom(), 16), { duration: .6 }); m.openPopup(); }
    }
  }
  async function setStatus(id, status) {
    try {
      const upd = await api(`/api/reports/${id}/status`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
      const i = state.reports.findIndex(r => r.id === id);
      if (i >= 0) state.reports[i] = upd;
      state.summary = await api(`/api/runs/${state.runId}/summary`);
      if (state.map) state.map.closePopup();
      renderSummary(); renderList(); renderMarkers(false);
    } catch (e) { alert('Could not update the report: ' + e.message); }
  }

  /* ------------------------------------------------------------------ map */
  function initMap() {
    if (typeof L === 'undefined') { $('mapNote').textContent = 'Map library could not load (no internet?). The list and charts still work.'; return; }
    state.map = L.map('map').setView([13.366, 77.093], 15);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(state.map);
    state.layer = L.layerGroup().addTo(state.map);
    state.map.on('popupopen', e => {
      const b = e.popup.getElement().querySelector('button[data-act]');
      if (b) b.onclick = () => setStatus(+b.dataset.id, b.dataset.act);
    });
    // context: simulated disaster zone, tower and hub
    const a = toLatLon(0, 0), b = toLatLon(600, 700);
    L.rectangle([a, b], { color: '#FB7185', weight: 2, dashArray: '6 5', fillOpacity: .08 }).addTo(state.map).bindTooltip('Disaster zone (no cellular network)', { sticky: true });
    L.circleMarker(toLatLon(1100, 350), { radius: 7, color: '#34D399', fillColor: '#34D399', fillOpacity: 1 }).addTo(state.map).bindTooltip('Communication tower');
    L.circleMarker(toLatLon(1300, 350), { radius: 7, color: '#6366F1', fillColor: '#6366F1', fillOpacity: 1 }).addTo(state.map).bindTooltip('Main Relief Hub');
    $('mapNote').textContent = 'Circle size and color correspond to finding severity';
  }
  function popupHtml(r) {
    return `<div class="pop"><b>${esc(r.finding)}</b>
      <span class="m">${esc(r.severity_label)} • ${esc(r.pkt_id)}</span><br>
      <span class="m">${fmt(r.lat, 5)}, ${fmt(r.lon, 5)}</span><br>
      <span class="m">Delay ${fmt(r.latency_s)} s • Status: ${esc(r.status)}</span><br>
      <div style="margin-top:8px">${actionButtons(r)}</div></div>`;
  }
  function renderMarkers(fit) {
    if (!state.map) return;
    state.layer.clearLayers(); state.markers = {};
    const pts = [];
    filtered().forEach(r => {
      const color = r.status === 'resolved' ? '#34D399' : SEV_COL[r.severity];
      const m = L.circleMarker([r.lat, r.lon], { radius: 5 + r.severity * 2, color: '#0F172A', weight: 2, fillColor: color, fillOpacity: r.status === 'resolved' ? .7 : 1 })
        .bindPopup(popupHtml(r)).addTo(state.layer);
      state.markers[r.id] = m; pts.push([r.lat, r.lon]);
    });
    if (fit && pts.length) state.map.fitBounds(pts, { padding: [40, 40], maxZoom: 16 });
    setTimeout(() => state.map.invalidateSize(), 50);
  }

  /* --------------------------------------------------------------- charts */
  function chartDefaults() {
    Chart.defaults.color = '#94A3B8';
    Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.08)';
    Chart.defaults.font.family = css('--font');
  }
  function renderLatencyChart() {
    if (typeof Chart === 'undefined') return;
    chartDefaults();
    const rows = state.reports.slice().sort((a, b) => a.received_s - b.received_s);
    if (state.chLat) state.chLat.destroy();
    state.chLat = new Chart($('chLatency'), {
      type: 'bar',
      data: { labels: rows.map(r => r.pkt_id), datasets: [{ data: rows.map(r => r.latency_s), backgroundColor: rows.map(r => SEV_COL[r.severity]), borderRadius: 4 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false },
        tooltip: { callbacks: { label: c => `${fmt(c.parsed.y)} s`, afterLabel: c => { const r = rows[c.dataIndex]; return `${r.finding} (${r.severity_label}), resent ${r.retransmissions}x`; } } } },
        scales: { y: { beginAtZero: true, grid: { color: 'rgba(255, 255, 255, 0.06)' }, title: { display: true, text: 'Seconds' } }, x: { grid: { display: false }, ticks: { maxRotation: 60, font: { size: 10 } } } } }
    });
  }
  function renderCompareChart() {
    if (typeof Chart === 'undefined') return;
    chartDefaults();
    const runs = state.compare;
    if (state.chCmp) state.chCmp.destroy();
    state.chCmp = new Chart($('chCompare'), {
      type: 'bar',
      data: { labels: runs.map(r => r.name), datasets: [{ data: runs.map(r => r.avg_latency_s),
        backgroundColor: runs.map(r => r.id === state.runId ? '#6366F1' : (r.relay_mode === 'adaptive' ? '#38BDF8' : '#334155')),
        borderRadius: 4 }] },
      options: { indexAxis: 'y', maintainAspectRatio: false,
        onClick: (_e, els) => { if (els.length) loadRun(runs[els[0].index].id); },
        onHover: (e, els) => { e.native.target.style.cursor = els.length ? 'pointer' : 'default'; },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: c => `Average ${fmt(c.parsed.x)} s`,
          afterLabel: c => { const r = runs[c.dataIndex]; return `${r.relay_mode} relay, range ${r.drone_range_m} m, loss ${Math.round(r.loss_base * 100)}%, ${r.delivered}/${r.captured} delivered`; } } } },
        scales: { x: { beginAtZero: true, grid: { color: 'rgba(255, 255, 255, 0.06)' }, title: { display: true, text: 'Average Latency (s)' } }, y: { grid: { display: false }, ticks: { font: { size: 11 } } } } }
    });
  }

  /* ------------------------------------------------------- tables / events */
  function renderFlows() {
    const rows = state.flows.slice().sort((a, b) => (a.kind === b.kind ? a.flow_id - b.flow_id : a.kind === 'data' ? -1 : 1));
    document.querySelector('#flowTable tbody').innerHTML = rows.map(f => `
      <tr><td>${esc(f.src)} → ${esc(f.dst)}</td><td>${f.kind === 'data' ? 'Report Payload' : 'Acknowledgement'}</td>
      <td class="n">${f.tx_packets}</td><td class="n">${f.rx_packets}</td><td class="n">${f.lost_packets}</td>
      <td class="n">${fmt(f.loss_pct)}%</td><td class="n">${fmt(f.mean_delay_ms)} ms</td><td class="n">${fmt(f.throughput_kbps, 2)} kbps</td></tr>`).join('');
  }
  function eventText(e) {
    if (e.node === 'Link') return { t: `${e.detail} link ${e.event === 'up' ? 'established' : 'lost'}`, c: e.event === 'up' ? 'good' : 'bad' };
    if (e.node === 'Tower') return { t: e.detail, c: e.event === 'offline' ? 'bad' : 'good' };
    if (e.event === 'captured') return { t: 'Captured ' + e.detail, c: '' };
    return { t: e.detail ? `${e.event.replace('_', ' ')}: ${e.detail}` : e.event.replace('_', ' '), c: e.event === 'rx_lost' ? 'bad' : '' };
  }
  function renderEvents() {
    const all = $('evAll').checked, packet = new Set(['forwarded', 'received', 'rx_lost']);
    const evs = state.events.filter(e => all || !packet.has(e.event));
    $('events').innerHTML = evs.map(e => { const x = eventText(e);
      return `<div class="ev"><span class="t">${mmss(e.time_s)}</span><span>${esc(e.node)}</span><span class="${x.c}">${esc(x.t)}</span></div>`; }).join('')
      || '<div class="none">No events recorded.</div>';
  }

  init().catch(e => { console.error(e); });
})();
