/* 隧道代理 IP 质量测试 WebUI 前端逻辑（原生 JS，无构建依赖） */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const STATUS_LABEL = { running: '运行中', completed: '已完成', stopped: '已停止', interrupted: '已中断' };

const state = {
  vendors: [],
  runs: [],
  selectedVendors: new Set(),
  selectedRunIds: new Set(),
  editingVendorId: null,
  monitorRunId: null,
  pollTimer: null,
};

async function api(url, options = {}) {
  const resp = await fetch(url, options);
  let body = {};
  try { body = await resp.json(); } catch { /* 空响应体 */ }
  if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
  return body;
}

function post(url, data) {
  return api(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

/* ---------- 页签 ---------- */

function switchTab(name) {
  $$('.tab').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === name));
  $$('.panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
  if (name === 'monitor') startPolling(); else stopPolling();
}

function goCompare(runId) {
  $('#compare-select').value = runId;
  switchTab('compare');
  renderCompare();
}
window.goCompare = goCompare;

/* ---------- 厂商管理 ---------- */

async function loadVendors() {
  state.vendors = await api('/api/vendors');
  renderVendors();
  renderVendorPicker();
}

function maskRaw(raw) {
  return raw.replace(/\/\/[^@/]+@/, '//***@');
}

function vendorAddress(v) {
  if (v.raw_url) return `<span class="mono">${esc(maskRaw(v.raw_url))}</span>`;
  return `<span class="mono">${esc(v.host)}:${esc(v.port ?? '')}</span>`;
}

function renderVendors() {
  const tbody = $('#vendors-body');
  if (!state.vendors.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">还没有厂商，点左上角「新增厂商」添加隧道配置。</td></tr>';
    return;
  }
  tbody.innerHTML = state.vendors.map((v) => `
    <tr>
      <td>${v.id}</td>
      <td><b>${esc(v.name)}</b>${v.notes ? `<div class="notes">${esc(v.notes)}</div>` : ''}</td>
      <td><span class="chip">${v.protocol === 'socks5' ? 'SOCKS5' : 'HTTP'}</span></td>
      <td>${vendorAddress(v)}</td>
      <td id="test-${v.id}">—</td>
      <td><span class="chip ${v.enabled ? 'on' : 'off'}">${v.enabled ? '启用' : '停用'}</span></td>
      <td class="actions">
        <button class="btn small" data-act="test" data-id="${v.id}">测试</button>
        <button class="btn small" data-act="edit" data-id="${v.id}">编辑</button>
        <button class="btn small danger" data-act="del" data-id="${v.id}">删除</button>
      </td>
    </tr>`).join('');
}

function openVendorModal(vendor = null) {
  state.editingVendorId = vendor ? vendor.id : null;
  $('#vendor-modal-title').textContent = vendor ? `编辑厂商 #${vendor.id}` : '新增厂商';
  const form = $('#vendor-form');
  form.reset();
  if (vendor) {
    form.elements.name.value = vendor.name;
    form.elements.protocol.value = vendor.protocol;
    form.elements.host.value = vendor.host || '';
    form.elements.port.value = vendor.port ?? '';
    form.elements.username.value = vendor.username || '';
    form.elements.password.value = vendor.password || '';
    form.elements.raw_url.value = vendor.raw_url || '';
    form.elements.notes.value = vendor.notes || '';
  }
  $('#vendor-modal').showModal();
}

async function saveVendor(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData($('#vendor-form')).entries());
  payload.port = payload.port ? Number(payload.port) : null;
  const editing = state.editingVendorId;
  const url = editing ? `/api/vendors/${editing}` : '/api/vendors';
  try {
    await api(url, { method: editing ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    $('#vendor-modal').close();
    await loadVendors();
  } catch (err) {
    alert(err.message);
  }
}

async function testVendor(id) {
  const cell = $(`#test-${id}`);
  cell.innerHTML = '<span class="chip pending">测试中…</span>';
  try {
    const r = await api(`/api/vendors/${id}/test`, { method: 'POST' });
    cell.innerHTML = r.ok
      ? `<span class="chip ok">✓ ${esc(r.ip)}（${r.latency_ms}ms）</span>`
      : `<span class="chip bad" title="${esc(r.error)}">✗ ${esc(r.error.slice(0, 42))}</span>`;
  } catch (err) {
    cell.innerHTML = `<span class="chip bad">✗ ${esc(err.message)}</span>`;
  }
}

async function deleteVendor(id) {
  const vendor = state.vendors.find((v) => v.id === id);
  if (!confirm(`确认删除厂商「${vendor ? vendor.name : id}」？历史采样数据保留。`)) return;
  await api(`/api/vendors/${id}`, { method: 'DELETE' });
  await loadVendors();
}

/* ---------- 测试任务 ---------- */

function renderVendorPicker() {
  const box = $('#run-vendors');
  const usable = state.vendors.filter((v) => v.enabled);
  if (!usable.length) {
    box.innerHTML = '<span class="hint">暂无启用的厂商，请先在「厂商管理」添加并启用。</span>';
    return;
  }
  box.innerHTML = usable.map((v) => `
    <button type="button" class="pick ${state.selectedVendors.has(v.id) ? 'selected' : ''}" data-id="${v.id}">${esc(v.name)}</button>
  `).join('');
}

async function startRun() {
  if (!state.selectedVendors.size) { alert('请先点选至少一个厂商'); return; }
  const payload = {
    name: $('#run-name').value.trim(),
    vendor_ids: Array.from(state.selectedVendors),
    duration_minutes: Number($('#run-duration').value) || 60,
    interval_seconds: Number($('#run-interval').value) || 5,
    timeout_seconds: Number($('#run-timeout').value) || 10,
    target_url: $('#run-target').value.trim(),
  };
  try {
    const r = await post('/api/runs', payload);
    state.selectedVendors.clear();
    state.monitorRunId = r.id;
    renderVendorPicker();
    await loadRuns();
    switchTab('monitor');
  } catch (err) {
    alert(err.message);
  }
}

async function loadRuns() {
  state.runs = await api('/api/runs');
  renderRuns();
  refreshMonitorSelect();
  refreshCompareSelect();
}

function statusChip(run) {
  return `<span class="chip st-${run.status}">${STATUS_LABEL[run.status] || run.status}${run.alive ? ' ●' : ''}</span>`;
}

function syncCheckAll() {
  const checkAll = $('#runs-check-all');
  const selectable = state.runs.filter((r) => !r.alive);
  const checkedCount = selectable.filter((r) => state.selectedRunIds.has(r.id)).length;
  checkAll.checked = selectable.length > 0 && checkedCount === selectable.length;
  checkAll.indeterminate = checkedCount > 0 && checkedCount < selectable.length;
  updateBatchBar();
}

function updateBatchBar() {
  $('#sel-count').textContent = state.selectedRunIds.size;
  $('#btn-batch-delete').disabled = state.selectedRunIds.size === 0;
}

function renderRuns() {
  state.selectedRunIds = new Set(
    Array.from(state.selectedRunIds).filter((id) => state.runs.some((r) => r.id === id && !r.alive))
  );
  syncCheckAll();
  const tbody = $('#runs-body');
  if (!state.runs.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">还没有测试任务，上方发起一次即可。</td></tr>';
    return;
  }
  tbody.innerHTML = state.runs.map((r) => {
    const checkCell = r.alive
      ? '<td class="check-cell"><input type="checkbox" disabled title="运行中的任务需先停止才能删除"></td>'
      : `<td class="check-cell"><input type="checkbox" class="runs-check" data-id="${r.id}" ${state.selectedRunIds.has(r.id) ? 'checked' : ''}></td>`;
    return `
    <tr>
      ${checkCell}
      <td>${r.id}</td>
      <td>${esc(r.name) || `<span class="hint">任务#${r.id}</span>`}</td>
      <td>${esc(r.vendors.join('、')) || '—'}</td>
      <td>${r.duration_minutes} 分钟 / ${r.interval_seconds}s</td>
      <td>${statusChip(r)}</td>
      <td class="mono">${esc(r.started_at)}</td>
      <td>${r.samples}</td>
      <td class="actions">
        <button class="btn small" data-act="monitor" data-id="${r.id}">监控</button>
        <button class="btn small" data-act="compare" data-id="${r.id}">对比</button>
        ${r.alive ? `<button class="btn small danger" data-act="stop" data-id="${r.id}">停止</button>` : ''}
      </td>
    </tr>`;
  }).join('');
}

async function confirmBatchDelete() {
  try {
    await post('/api/runs/batch-delete', { run_ids: Array.from(state.selectedRunIds) });
    $('#batch-delete-modal').close();
    state.selectedRunIds.clear();
    await loadRuns();
  } catch (err) {
    $('#batch-delete-modal').close();
    alert(err.message);
  }
}

/* ---------- 运行监控 ---------- */

function refreshMonitorSelect() {
  const sel = $('#monitor-select');
  const prev = Number(sel.value);
  sel.innerHTML = state.runs.length
    ? state.runs.map((r) => `<option value="${r.id}">#${r.id} ${esc(r.name) || ''}（${r.vendors.join('、') || '厂商已删'}）</option>`).join('')
    : '<option value="">（暂无任务）</option>';
  if (state.monitorRunId && state.runs.some((r) => r.id === state.monitorRunId)) sel.value = state.monitorRunId;
  else if (prev && state.runs.some((r) => r.id === prev)) sel.value = prev;
  else if (state.runs.length) { state.monitorRunId = state.runs[0].id; sel.value = state.monitorRunId; }
  else state.monitorRunId = null;
}

function startPolling() {
  stopPolling();
  pollMonitor();
  state.pollTimer = setInterval(pollMonitor, 3000);
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function pollMonitor() {
  const runId = Number($('#monitor-select').value);
  if (!runId) {
    $('#monitor-cards').innerHTML = '<div class="card" style="text-align:center;color:var(--muted)">当前没有测试任务，去「测试任务」发起一次对比。</div>';
    $('#btn-stop-run').disabled = true;
    $('#monitor-meta').textContent = '';
    return;
  }
  let data;
  try { data = await api(`/api/runs/${runId}`); } catch { return; }
  state.monitorRunId = runId;
  renderMonitor(data);
  $('#btn-stop-run').disabled = !data.alive;
}

function renderMonitor(data) {
  const run = data.run;
  const started = new Date(run.started_at.replace(' ', 'T'));
  const totalMs = run.duration_minutes * 60000;
  const elapsed = data.alive ? Math.min(Date.now() - started.getTime(), totalMs) : totalMs;
  const pct = totalMs ? Math.round((elapsed / totalMs) * 100) : 100;
  const remainSec = Math.max(0, Math.ceil((totalMs - elapsed) / 1000));
  const remainText = data.alive
    ? `剩余约 ${remainSec > 90 ? Math.round(remainSec / 60) + ' 分钟' : remainSec + ' 秒'}`
    : '已结束';
  const goBtn = data.alive ? '' : `<button class="btn small" onclick="goCompare(${run.id})">查看对比 →</button>`;
  $('#monitor-meta').innerHTML = `任务#${run.id} ${statusChip(run)} · ${remainText} · 目标 ${esc(run.target_url)} ${goBtn}`;
  $('#monitor-cards').innerHTML = data.vendors.map((v) => {
    const fail = v.probes - v.ok_n;
    const last = v.last || {};
    const lastLine = last.ts
      ? (last.ok
        ? `<span class="chip ok">✓ ${esc(last.ip)}</span><span class="hint">${last.latency_ms}ms · ${esc(last.ts)}</span>`
        : `<span class="chip bad" title="${esc(last.error || '')}">✗ ${esc((last.error || '').slice(0, 60))}</span><span class="hint">${esc(last.ts)}</span>`)
      : '<span class="hint">等待首次采样…</span>';
    return `
      <div class="card monitor-card">
        <div class="mc-head">
          <b>${esc(v.name)}</b>
          <span class="hint">${v.probes} 次采样 · 成功 ${v.ok_n} · 失败 ${fail}</span>
        </div>
        <div class="progress"><div class="bar" style="width:${pct}%"></div></div>
        <div class="mc-stats">
          <span>去重 IP：<b>${v.uniq}</b></span>
          <span>最近：${lastLine}</span>
        </div>
      </div>`;
  }).join('');
}

/* ---------- 对比分析 ---------- */

function refreshCompareSelect() {
  const sel = $('#compare-select');
  const prev = Number(sel.value);
  sel.innerHTML = state.runs.length
    ? state.runs.map((r) => `<option value="${r.id}">#${r.id} ${esc(r.name) || ''}（${STATUS_LABEL[r.status]} · ${esc(r.vendors.join('、')) || '—'}）</option>`).join('')
    : '<option value="">（暂无任务）</option>';
  if (prev && state.runs.some((r) => r.id === prev)) sel.value = prev;
}

async function renderCompare() {
  const runId = Number($('#compare-select').value);
  const body = $('#compare-body');
  if (!runId) {
    body.innerHTML = '<div class="card" style="text-align:center;color:var(--muted)">暂无任务，先去「测试任务」发起一次对比。</div>';
    $('#detail-vendor').innerHTML = '';
    $('#samples-body').innerHTML = '';
    $('#detail-count').textContent = '';
    return;
  }
  let data;
  try { data = await api(`/api/runs/${runId}/summary`); } catch (err) {
    body.innerHTML = `<div class="card" style="color:#b91c1c">加载失败：${esc(err.message)}</div>`;
    return;
  }
  const threshold = Number($('#cmp-threshold').value) || 15;

  const metricRows = data.vendors.map((v) => {
    const fail = v.probes - v.ok_n;
    const tops = v.top_24.map((t) => `${esc(t.net)}（${(t.share * 100).toFixed(1)}%）`).join('<br>') || '—';
    return `<tr>
      <td><b>${esc(v.name)}</b></td>
      <td>${v.probes}</td>
      <td>${v.ok_n}</td>
      <td>${fail}</td>
      <td><b>${v.unique_ips}</b></td>
      <td>${(v.dup_rate * 100).toFixed(1)}%</td>
      <td>${v.unique_24}</td>
      <td class="small">${tops}</td>
    </tr>`;
  }).join('');

  let matrixHtml;
  if (data.matrix.length) {
    matrixHtml = `<div class="card"><h3>跨家交集（IP 级）</h3>
      <div class="scroll-x"><table class="matrix">
        <thead><tr><th>厂商对</th><th>共同 IP</th><th>交集%</th><th>共同 /24</th><th>/24 交集%</th><th>结论</th></tr></thead>
        <tbody>
        ${data.matrix.map((m) => {
          const same = m.pct < threshold;
          const cellCls = same ? 'cell-ok' : (m.pct < 40 ? 'cell-warn' : 'cell-bad');
          return `<tr>
            <td>${esc(m.a)} × ${esc(m.b)}</td>
            <td>${m.shared}</td>
            <td class="${cellCls}"><b>${m.pct}%</b></td>
            <td>${m.shared_24}</td>
            <td>${m.pct_24}%</td>
            <td>${same ? '<span class="chip ok">真不太相同</span>' : '<span class="chip bad">存在重叠</span>'}</td>
          </tr>`;
        }).join('')}
        </tbody>
      </table></div>
      <p class="hint" style="margin-top:10px">交集% = 共同去重IP ÷ 较小方去重IP。&lt; ${threshold}% 绿=真不太相同；${threshold}~40% 黄=轻度重叠；&gt;40% 红=高度重叠（大概率同源池）。</p>
    </div>`;
  } else {
    matrixHtml = '<div class="card"><h3>跨家交集</h3><p class="hint">该任务只选了一个厂商，无交叉对象；发起任务时选 2 家以上即可对比。</p></div>';
  }

  body.innerHTML = `
    <div class="card">
      <h3>各厂商 IP 质量</h3>
      <div class="scroll-x"><table>
        <thead><tr><th>厂商</th><th>采样</th><th>成功</th><th>失败</th><th>去重 IP</th><th>重复率</th><th>去重 /24 段</th><th>Top /24（占比）</th></tr></thead>
        <tbody>${metricRows}</tbody>
      </table></div>
      <p class="hint" style="margin-top:10px">重复率 = 1 − 去重IP ÷ 成功采样；/24 集中度看「去重 /24 段」与「Top /24 占比」，越分散越健康。</p>
    </div>
    ${matrixHtml}`;

  $('#detail-vendor').innerHTML = data.vendors.map((v) => `<option value="${v.vendor_id}">${esc(v.name)}</option>`).join('');
  loadSamples();
}

async function loadSamples() {
  const runId = Number($('#compare-select').value);
  const vendorId = Number($('#detail-vendor').value);
  if (!runId || !vendorId) {
    $('#samples-body').innerHTML = '';
    $('#detail-count').textContent = '';
    return;
  }
  const rows = await api(`/api/runs/${runId}/samples?vendor_id=${vendorId}&limit=300`);
  $('#detail-count').textContent = `最近 ${rows.length} 条（完整数据请导出 CSV）`;
  $('#btn-export').href = `/api/runs/${runId}/export?vendor_id=${vendorId}`;
  $('#samples-body').innerHTML = rows.map((r) => `
    <tr>
      <td class="mono">${esc(r.ts)}</td>
      <td>${r.ok ? '<span class="chip ok">✓</span>' : '<span class="chip bad">✗</span>'}</td>
      <td class="mono">${esc(r.ip || '—')}</td>
      <td>${r.latency_ms ?? '—'}</td>
      <td class="small err" title="${esc(r.error || '')}">${esc(r.error || '')}</td>
      <td class="small mono">${esc((r.endpoint || '').replace('https://', '').replace('http://', ''))}</td>
    </tr>`).join('');
}

/* ---------- 事件绑定与启动 ---------- */

function bind() {
  $$('.tab').forEach((btn) => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

  $('#btn-add-vendor').addEventListener('click', () => openVendorModal(null));
  $('#vendor-form').addEventListener('submit', saveVendor);
  $('#vendor-cancel').addEventListener('click', () => $('#vendor-modal').close());

  $('#vendors-table').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.dataset.act === 'test') testVendor(id);
    if (btn.dataset.act === 'edit') openVendorModal(state.vendors.find((v) => v.id === id));
    if (btn.dataset.act === 'del') deleteVendor(id);
  });

  $('#run-vendors').addEventListener('click', (e) => {
    const btn = e.target.closest('.pick');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (state.selectedVendors.has(id)) state.selectedVendors.delete(id);
    else state.selectedVendors.add(id);
    renderVendorPicker();
  });
  $('#btn-start-run').addEventListener('click', startRun);

  $('#runs-table').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.dataset.act === 'monitor') { state.monitorRunId = id; switchTab('monitor'); pollMonitor(); }
    if (btn.dataset.act === 'compare') goCompare(id);
    if (btn.dataset.act === 'stop') {
      if (!confirm(`确认停止任务 #${id}？`)) return;
      await post(`/api/runs/${id}/stop`);
      await loadRuns();
    }
  });

  $('#runs-check-all').addEventListener('change', (e) => {
    const on = e.target.checked;
    state.runs.forEach((r) => {
      if (r.alive) return;
      if (on) state.selectedRunIds.add(r.id); else state.selectedRunIds.delete(r.id);
    });
    renderRuns();
  });
  $('#runs-table').addEventListener('change', (e) => {
    const box = e.target.closest('.runs-check');
    if (!box) return;
    const id = Number(box.dataset.id);
    if (box.checked) state.selectedRunIds.add(id); else state.selectedRunIds.delete(id);
    syncCheckAll();
  });
  $('#btn-batch-delete').addEventListener('click', () => {
    const n = state.selectedRunIds.size;
    if (!n) return;
    $('#batch-delete-text').innerHTML = `将删除选中的 <b>${n}</b> 个任务及其全部采样明细，操作不可恢复。`;
    $('#batch-delete-modal').showModal();
  });
  $('#batch-delete-cancel').addEventListener('click', () => $('#batch-delete-modal').close());
  $('#batch-delete-confirm').addEventListener('click', confirmBatchDelete);

  $('#monitor-select').addEventListener('change', pollMonitor);
  $('#btn-stop-run').addEventListener('click', async () => {
    const id = Number($('#monitor-select').value);
    if (!id || !confirm(`确认停止任务 #${id}？`)) return;
    await post(`/api/runs/${id}/stop`);
    setTimeout(pollMonitor, 400);
  });

  $('#compare-select').addEventListener('change', renderCompare);
  $('#btn-refresh-compare').addEventListener('click', async () => { await loadRuns(); renderCompare(); });
  $('#cmp-threshold').addEventListener('change', renderCompare);
  $('#detail-vendor').addEventListener('change', loadSamples);
}

async function boot() {
  bind();
  await loadVendors();
  await loadRuns();
  renderCompare();
}

boot();
