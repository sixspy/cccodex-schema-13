const msg = document.getElementById('msg');
const view = document.getElementById('view');
let contracts = [];
let lastCheck = null;
let timelineTable = '';

async function api(path, opts={}) {
  const r = await fetch(path, {
    headers: {'Content-Type': 'application/json', ...(opts.headers||{})},
    ...opts
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP '+r.status));
  return j;
}

function setMsg(t, isErr=false) {
  msg.textContent = t || '';
  msg.className = isErr ? 'err' : 'muted';
}

function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]
  ));
}

function auditChip(auditRef, storage) {
  if (!auditRef) return '<span class="chip bad">缺审计</span>';
  return `<span class="chip mono" title="${esc(storage || '')}">凭证 ${esc(auditRef)}</span>`;
}

async function loadContracts() {
  const data = await api('/api/contracts');
  contracts = data.items || [];
}

function renderContracts() {
  view.innerHTML = `<div class="grid">${contracts.map(c => {
    let chip = '';
    if (c.status === '现行') {
      chip = c.audit_ref
        ? auditChip(c.audit_ref, `upgrade_audits`)
        : '<span class="chip">未走换版（无凭证）</span>';
    }
    return `
    <article class="card">
      <div class="row">
        <h3>${esc(c.table_name)} · v${c.version_no}</h3>
        <span class="chip">${esc(c.status)}</span>
        ${chip}
      </div>
      <p class="muted">${esc(c.note || '')} · 字段 ${c.field_count}</p>
    </article>`;
  }).join('')}</div>`;
}

function draftOptions() {
  return contracts.filter(c => c.status === '草稿')
    .map(c => `<option value="${c.id}">${esc(c.table_name)} v${c.version_no}</option>`).join('');
}

function renderCheck() {
  view.innerHTML = `
    <div class="card grid">
      <label>草稿契约
        <select id="cid">${draftOptions()}</select>
      </label>
      <button class="primary" id="btn-check" type="button">运行破坏性检查</button>
      <pre id="out" class="muted"></pre>
    </div>`;
  document.getElementById('btn-check').onclick = async () => {
    try {
      const cid = document.getElementById('cid').value;
      const j = await api(`/api/contracts/${cid}/breaking-check`, {method:'POST', body:'{}'});
      lastCheck = j.check;
      document.getElementById('out').textContent = JSON.stringify(j.check, null, 2);
      setMsg(`检查结论：${j.check.verdict}`);
    } catch (e) { setMsg(e.message, true); }
  };
}

function renderApply() {
  const options = draftOptions();
  view.innerHTML = `
    <div class="card grid">
      <label>草稿契约
        <select id="cid">${options}</select>
      </label>
      <p class="muted">升版与审计凭证同事务落库：审计写不出，换版整笔回滚。</p>
      <p class="muted">审计形态：独立审计快照表 upgrade_audits（全局只此一种凭证）。</p>
      <button class="primary" id="btn-apply" type="button" ${options ? '' : 'disabled'}>换版应用</button>
    </div>`;
  if (!options) return;
  document.getElementById('btn-apply').onclick = async () => {
    try {
      const cid = document.getElementById('cid').value;
      const j = await api(`/api/contracts/${cid}/apply`, {method:'POST', body:'{}'});
      const a = j.audit;
      setMsg(`已应用 ${j.contract.table_name} v${j.contract.version_no} · 凭证 ${a.audit_ref}（${a.storage}）`);
      await loadContracts();
      renderApply();
    } catch (e) { setMsg(e.message, true); }
  };
}

async function renderReconcile() {
  const reports = (await api('/api/reconcile-reports')).items || [];
  view.innerHTML = `
    <div class="card grid">
      <div class="row">
        <button class="primary" id="btn-rec" type="button">跑一轮对账</button>
      </div>
      <div class="grid" id="reps">${reports.map(r => `
        <article class="card"><strong>${esc(r.as_of)}</strong>
        <p>${esc(r.summary)}</p></article>`).join('') || '<p class="muted">暂无对账</p>'}</div>
    </div>`;
  document.getElementById('btn-rec').onclick = async () => {
    try {
      const j = await api('/api/reconcile-reports', {
        method:'POST', body: JSON.stringify({as_of: '2026-11-18'})
      });
      setMsg(j.report.summary);
      await renderReconcile();
    } catch (e) { setMsg(e.message, true); }
  };
}

async function renderAudits() {
  const items = (await api('/api/audits')).items || [];
  view.innerHTML = `
    <div class="card grid">
      <div class="row">
        <h3>审计队列</h3>
        <span class="chip">独立审计快照表 upgrade_audits</span>
      </div>
      <p class="muted">每次升版一行快照，只增不改。队列即复盘凭证入口。</p>
      ${items.map(a => `
        <article class="card">
          <div class="row">
            <strong>${esc(a.table_name)} v${a.to_version}</strong>
            ${auditChip(a.audit_ref, a.storage)}
            <span class="chip">操作人 ${esc(a.actor)}</span>
          </div>
          <p class="muted">${esc(a.from_version == null ? '—' : 'v'+a.from_version)} → v${a.to_version}
            · 落库 ${esc(a.storage)} · 指纹 ${esc(a.fingerprint_short)} · ${esc(a.created_at)} UTC</p>
        </article>`).join('') || '<p class="muted">暂无审计凭证</p>'}
    </div>`;
}

async function renderTimeline() {
  const tables = [...new Set(contracts.map(c => c.table_name))];
  if (!tables.includes(timelineTable)) timelineTable = tables[0] || '';
  let body = '<p class="muted">暂无事件</p>';
  if (timelineTable) {
    const t = await api(`/api/timeline?table_name=${encodeURIComponent(timelineTable)}`);
    body = (t.events || []).map(e => `
      <article class="card ${e.missing_audit ? 'flagged' : ''}">
        <div class="row">
          <strong>${esc(e.kind)}</strong>
          <span class="muted">${esc(e.at)} UTC</span>
          ${e.missing_audit ? '<span class="chip bad">缺审计</span>' : auditChip(e.audit_ref, e.storage)}
        </div>
        <p>${esc(e.title)} · ${esc(e.detail)}</p>
        ${e.storage ? `<p class="muted mono">凭证落库 ${esc(e.storage)}</p>` : ''}
      </article>`).join('') || '<p class="muted">暂无事件</p>';
  }
  view.innerHTML = `
    <div class="card grid">
      <div class="row">
        <label style="margin:0">表
          <select id="tname">${tables.map(t => `
            <option value="${esc(t)}" ${t === timelineTable ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
        </label>
      </div>
      <p class="muted">时间线把登记与换版串起来；换版节点必须挂同一套审计凭证，缺审计即亮红。</p>
      <div class="grid">${body}</div>
    </div>`;
  document.getElementById('tname').onchange = async (e) => {
    timelineTable = e.target.value;
    await renderTimeline();
  };
}

async function renderCompliance() {
  const c = await api('/api/compliance');
  const tone = c.status === '合规' ? '' : 'bad';
  view.innerHTML = `
    <div class="card grid">
      <div class="row">
        <h3>合规进度</h3>
        <span class="chip ${tone}">${esc(c.status)}</span>
        <span class="chip">${c.progress}%</span>
      </div>
      <p class="muted">对照同一套凭证（${esc(c.form)}）逐条复验：
        升版 ${c.total} 笔，通过 ${c.verified}，缺审计 ${c.missing_audit}，指纹被篡改 ${c.tampered}。</p>
      <div class="bar"><i style="width:${c.progress}%"></i></div>
      ${(c.items || []).map(i => `
        <article class="card ${i.verified ? '' : 'flagged'}">
          <div class="row">
            <strong>${esc(i.table_name)} v${i.to_version}</strong>
            ${i.verified ? `<span class="chip">已核验</span> ${auditChip(i.audit_ref, i.storage)}`
                         : `<span class="chip bad">${esc(i.problems.join('、'))}</span>`}
          </div>
          <p class="muted">${esc(i.actor || '无操作人')} · ${esc(i.created_at)} UTC
            ${i.storage ? '· 凭证落库 ' + esc(i.storage) : ''}</p>
        </article>`).join('') || '<p class="muted">暂无升版记录</p>'}
    </div>`;
}

async function show(tab) {
  setMsg('');
  await loadContracts();
  if (tab === 'contracts') renderContracts();
  if (tab === 'check') renderCheck();
  if (tab === 'apply') renderApply();
  if (tab === 'reconcile') await renderReconcile();
  if (tab === 'audits') await renderAudits();
  if (tab === 'timeline') await renderTimeline();
  if (tab === 'compliance') await renderCompliance();
}

document.querySelectorAll('.pills button').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('.pills button').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
    show(btn.dataset.tab);
  };
});
document.getElementById('btn-logout').onclick = async () => {
  await api('/api/logout', {method:'POST', body:'{}'});
  location.href = '/login';
};
show('contracts');
