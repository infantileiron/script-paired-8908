import {state} from '../stores/session.js';
import {api} from '../api/client.js';
import {esc} from '../utils/html.js';
import {badge} from '../components/common/status.js';
import {table} from '../components/common/table.js';
import {field} from '../components/common/fields.js';
import {PLAN_STATES} from '../constants/enums.js';

const fmt = v => (v === null || v === undefined ? '—' : Number(v).toFixed(3));
const pct = v => (v === null || v === undefined ? '—' : (Number(v) * 100).toFixed(1) + '%');
const stateText = s => PLAN_STATES[s] || s;

function planList() {
  return `<h3>抽检计划</h3><div class="scroll"><table><thead><tr><th>编号</th><th>名称</th><th>种子</th><th>状态</th><th>总体 / 抽样</th><th>各组抽样额</th><th></th></tr></thead><tbody>${
    state.plans.map(p => `<tr><td>#${p.id}</td><td>${esc(p.name)}</td><td>${p.seed}</td><td>${badge(stateText(p.state))}</td><td>${p.population} / ${p.sample_size}</td><td>${p.groups.map(g => `${esc(g.team)} n=${g.sample_size}`).join('；')}</td><td><button data-plan="${p.id}">查看</button></td></tr>`).join('') || '<tr><td>尚无抽检计划。在右侧登记两组完整名册、抽样额和种子。</td></tr>'
  }</tbody></table></div>`;
}

function groupConfig(plan) {
  return `<div class="metrics">${plan.groups.map(g => `<div><strong>${g.sample_size} / ${g.population}</strong>${esc(g.team)} 抽样额 / 名册 · π=${pct(g.inclusion_probability)}</div>`).join('')}<div><strong>${plan.seed}</strong>随机种子</div></div><p class="notice">入选依据：${esc(plan.groups[0]?.selection_rule || '')}。组内不放回；名册行序不影响入选名单。</p>`;
}

function memberTable(plan) {
  const editable = plan.state === 'collecting' && state.user.role !== 'viewer';
  return `<h3>总体名册与入选标记 / seed ${plan.seed}</h3><div class="scroll"><table><thead><tr><th>组别</th><th>顾问编号</th><th>组内排名</th><th>入选</th><th>入样概率</th><th>抽样键（前8位）</th><th>前测</th><th>后测</th>${editable ? '<th>逐个回填</th>' : ''}</tr></thead><tbody>${
    plan.members.map(m => `<tr><td>${esc(m.team)}</td><td>${esc(m.code)}</td><td>${m.rank}</td><td>${m.selected ? '入选' : '未入选'}</td><td>${pct(m.inclusion_probability)}</td><td><code>${esc(m.selection_key.slice(0, 8))}</code></td><td>${m.before ?? '未回填'}</td><td>${m.after ?? '未回填'}</td>${editable ? `<td>${m.selected ? `<form class="backfill" data-code="${esc(m.code)}"><input name="before" type="number" min="0" max="100" step="0.1" placeholder="前测" value="${m.before ?? ''}"><input name="after" type="number" min="0" max="100" step="0.1" placeholder="后测" value="${m.after ?? ''}"><button>保存</button></form>` : '<span>未入选，拒收</span>'}</td>` : ''}</tr>`).join('')
  }</tbody></table></div>`;
}

function contributionTable(result) {
  const rows = (result.group_contributions || []).map(c => [
    c.team, c.population, c.sample_size, c.complete ?? '—',
    c.inclusion_probability === undefined ? '—' : pct(c.inclusion_probability),
    c.design_weight === undefined ? '—' : fmt(c.design_weight),
    c.fpc === undefined ? '—' : fmt(c.fpc),
    c.stratum_mean === undefined ? '—' : fmt(c.stratum_mean),
    c.estimate_contribution === undefined ? '—' : fmt(c.estimate_contribution),
    c.variance_share === undefined || c.variance_share === null ? '—' : pct(c.variance_share),
  ]);
  return table(['组别', '总体 N', '抽样额', '完整回填', '入样概率', '设计权重', 'FPC', '组均值', '对估计贡献', '方差占比'], rows);
}

function resultPanel(plan) {
  const r = plan.result;
  if (!r) return `<p class="notice">计划尚未结案。发单后逐个回填，收齐后结案冻结；缺测或组内样本不足将直接失败，不补零、不换人。</p>`;
  const d = r.design_based;
  const head = `<p class="notice">${esc(r.message)}</p><p class="eyebrow">冻结 checksum ${esc(r.checksum || '')} · seed ${plan.seed} · 结案 ${esc(r.plan?.closed_at || plan.closed_at || '')}</p>`;
  if (r.outcome !== 'done' || !d) {
    return `${head}<h3>失败原因</h3><p>${esc(r.message)}</p>${r.missing?.length ? table(['组别', '顾问编号', '原因'], r.missing.map(m => [m.team, m.code, m.reason])) : ''}${r.insufficient_groups?.length ? table(['组别', '抽样额', '完整回填', '原因'], r.insufficient_groups.map(m => [m.team, m.sample_size, m.complete, m.reason])) : ''}<h3>组别记录</h3>${contributionTable(r)}`;
  }
  const p = r.paired_test;
  return `${head}<div class="metrics"><div><strong>${fmt(d.estimate)}</strong>总体平均改善 / 分</div><div><strong>${fmt(d.standard_error)}</strong>标准误（含 FPC）</div><div><strong>[${d.ci95.map(fmt).join(', ')}]</strong>95% 区间</div></div><p>估计量：${esc(d.estimator)}；目标：${esc(d.target)}；自由度 ${d.df}；总体 ${d.population_total} 人，完整样本 ${d.sample_total} 人。</p><p class="notice">${esc(d.formula)}<br>${esc(d.note)}</p>${d.warnings?.length ? `<p class="notice">${d.warnings.map(esc).join('<br>')}</p>` : ''}<h3>组别贡献</h3>${contributionTable(r)}<h3>普通配对检验（与总体估计分开）</h3>${p?.available ? `<p class="notice">${esc(p.note)}</p><p>n=${p.n} · 均值差 ${fmt(p.difference)} · t=${fmt(p.t)} · df=${p.df} · p=${Number(p.p_value).toExponential(3)} · 95%CI [${p.ci95.map(fmt).join(', ')}] · dz=${fmt(p.cohen_dz)} · 置换 p=${Number(p.permutation_p).toExponential(3)}（${esc(p.permutation_method)}，${p.iterations} 次）</p>` : `<p class="notice">普通配对检验不可用：${esc(p?.reason || '—')}</p>`}`;
}

function detail(plan) {
  const frozen = plan.state === 'done' || plan.state === 'failed';
  const canWrite = state.user.role !== 'viewer';
  const actions = canWrite ? `${plan.state === 'draft' ? '<button id="dispatch-plan">发单（按名单逐个回填）</button>' : ''}${plan.state === 'collecting' ? '<button id="dispatch-plan">重试发单（沿用原名单）</button> <button id="close-plan" class="primary">结案冻结收样</button>' : ''}` : '';
  return `<section class="panel"><h2>#${plan.id} ${esc(plan.name)} ${badge(stateText(plan.state))}</h2><p class="eyebrow">登记 ${esc(plan.created_at)} · 发单 ${esc(plan.dispatched_at || '未发单')} · 结案 ${esc(plan.closed_at || '未结案')}</p>${groupConfig(plan)}<p>${actions} <button id="plan-export">导出计划与结果 JSON</button> <button id="plan-refresh">刷新</button></p>${memberTable(plan)}</section><section class="panel">${resultPanel(plan)}${frozen ? '<p class="notice">本计划已冻结，晚到分数与其他回填一律不再接收；需要新样本请登记新计划。</p>' : ''}</section>`;
}

export function render() {
  const plan = state.plan;
  return `<div class="split"><section class="panel"><h2>分层随机抽检计划</h2><p>自愿回传不能代表全体顾问。登记两组完整名册、各组抽样额与种子，组内按稳定抽样键不放回抽取；发单后逐个回填，未入选者拒收，缺测不补零、不换人。</p>${planList()}${plan ? detail(plan) : '<p class="notice">从上方选择一个计划查看总体名册、入选依据、回填与组别贡献；尚未结案的计划不会产生总体估计。</p>'}</section><aside class="side"><h2>登记新计划</h2><form id="plan-create">${field('计划名称', 'name', 'text', 'required value="11月续约话术分层抽检"')}${field('随机种子（整数）', 'seed', 'number', 'required min=0 max=2147483647 value=1115')}<label>企业一组完整名册<textarea name="roster_a" rows="6" required placeholder="每行一个顾问编号，或逗号/空格分隔"></textarea></label>${field('企业一组抽样额', 'sample_a', 'number', 'required min=1 value=6')}<label>企业二组完整名册<textarea name="roster_b" rows="6" required placeholder="每行一个顾问编号"></textarea></label>${field('企业二组抽样额', 'sample_b', 'number', 'required min=1 value=6')}<p>两组必须同时登记且编号不跨组重复。抽样键=sha256(seed, 组别, 编号)，行序变化不会改换名单。</p><button class="primary">登记计划</button></form><p class="notice">估计口径：按入样概率加权到总体，区间含有限总体修正；与普通配对检验分开报告。</p></aside></div>`;
}
