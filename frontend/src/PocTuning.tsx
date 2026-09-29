import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import { api } from './api';
import { Icon, date, statusLabels } from './ui';
import type { PocAnalysis, PocCampaign, PocGroup, PocParameterDefinition, PocParameters, PocRecommendation, PocVerdict } from './pocTypes';
import './poc-tuning.css';

type Tab = 'parameters' | 'experiments' | 'conclusion';
type ImportKind = 'campaign' | 'parameter' | 'trial';
const tabs: { id: Tab; title: string }[] = [{ id: 'parameters', title: '参数' }, { id: 'experiments', title: '实验对比' }, { id: 'conclusion', title: '结论' }];
const labels = { pending: '待验证', confirmed: '已确认', rejected: '已否定' };
const metricLabels = { iops: 'IOPS', bandwidth_mib_s: '带宽 / MiB/s', latency_ms: '平均延迟 / ms' };
const fmt = (value: number | null | undefined, digits = 2) => value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value);
const valueText = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value);
const campaignPath = (id: string) => '/poc/campaigns/' + encodeURIComponent(id);

function Table({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className="poc-table-scroll"><table className={'poc-table ' + className}>{children}</table></div>;
}
function Parameters({ values, definitions }: { values: PocParameters; definitions: PocParameterDefinition[] }) {
  return <div className="poc-values">{Object.entries(values).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => <div key={key}><span title={key}>{definitions.find(def => def.id === key)?.label || key}</span><code>{valueText(value)}</code></div>)}</div>;
}

export default function PocTuning() {
  const [campaigns, setCampaigns] = useState<PocCampaign[]>([]), [definitions, setDefinitions] = useState<PocParameterDefinition[]>([]);
  const [campaignId, setCampaignId] = useState(''), [campaign, setCampaign] = useState<PocCampaign | null>(null);
  const [tab, setTab] = useState<Tab>('experiments'), [historyId, setHistoryId] = useState('');
  const [loading, setLoading] = useState(true), [loadingDetail, setLoadingDetail] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [importKind, setImportKind] = useState<ImportKind | null>(null);
  const load = useCallback(async (preferred?: string) => {
    setError('');
    const results = await Promise.allSettled([api<PocCampaign[]>('/poc/campaigns'), api<PocParameterDefinition[]>('/poc/parameters')]);
    if (results[0].status === 'fulfilled') {
      const sorted = [...results[0].value].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
      setCampaigns(sorted); setCampaignId(current => preferred || current || sorted[0]?.id || '');
    }
    if (results[1].status === 'fulfilled') setDefinitions(results[1].value);
    const failed = results.find(result => result.status === 'rejected');
    if (failed?.status === 'rejected') setError(failed.reason instanceof Error ? failed.reason.message : 'POC 数据读取失败');
    setLoading(false);
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!campaignId) { setCampaign(null); return; }
    let cancelled = false; setLoadingDetail(true); setCampaign(null); setHistoryId('');
    api<PocCampaign>(campaignPath(campaignId)).then(value => { if (!cancelled) setCampaign(value); }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : '场景读取失败'); }).finally(() => { if (!cancelled) setLoadingDetail(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(''), 4000); return () => window.clearTimeout(timer); }, [notice]);
  const history = campaign?.analysis_history ?? [];
  const analysis = historyId ? history.find(value => value.id === historyId) ?? null : campaign?.analysis ?? null;
  const historical = !!analysis && analysis.id !== campaign?.analysis?.id;
  function update(value: PocCampaign) {
    setCampaign(value); setCampaigns(current => current.some(item => item.id === value.id) ? current.map(item => item.id === value.id ? value : item) : [value, ...current]);
  }
  async function analyze() {
    if (!campaign || busy) return; setBusy(true); setError('');
    try { update(await api<PocCampaign>(campaignPath(campaign.id) + '/analyze', {})); setHistoryId(''); setTab('conclusion'); setNotice('参数分析已保存。'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '分析失败'); } finally { setBusy(false); }
  }
  async function feedback(verdict: PocVerdict, note: string) {
    if (!campaign?.analysis || historical || busy || !note.trim()) return; setBusy(true); setError('');
    try { update(await api<PocCampaign>(campaignPath(campaign.id) + '/feedback', { analysis_id: campaign.analysis.id, verdict, note: note.trim(), reviewer: '性能专家' })); setNotice('评价已保存。'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '评价保存失败'); } finally { setBusy(false); }
  }
  async function submitJson(kind: ImportKind, body: unknown) {
    if (kind === 'parameter') {
      const saved = await api<PocParameterDefinition>('/poc/parameters', body);
      setDefinitions(current => current.some(value => value.id === saved.id) ? current.map(value => value.id === saved.id ? saved : value) : [...current, saved]); setNotice('参数定义已注册。');
    } else if (kind === 'campaign') {
      const saved = await api<PocCampaign>('/poc/campaigns', body); update(saved); setCampaignId(saved.id); setHistoryId(''); setTab('experiments'); setNotice('调优场景已创建。');
    } else {
      if (!campaign) throw new Error('请先选择调优场景。');
      update(await api<PocCampaign>(campaignPath(campaign.id) + '/trials', body)); setHistoryId(''); setTab('experiments'); setNotice('实验结果已导入。');
    }
    setImportKind(null);
  }
  function tabKey(event: ReactKeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0; else if (event.key === 'End') next = tabs.length - 1; else return;
    event.preventDefault(); setTab(tabs[next].id); document.getElementById('poc-tab-' + tabs[next].id)?.focus();
  }
  return <div className="poc-page">
    <header className="poc-page-heading"><div><h1>POC 调优</h1>{campaign && <span className={'poc-source ' + campaign.source}>{campaign.source === 'mock' ? '模拟数据' : '导入实测'}</span>}</div><button className="poc-button secondary" disabled={busy} onClick={() => setImportKind('campaign')}><Icon name="plus" size={15}/>新建调优场景</button></header>
    <div className="poc-connection"><span/>真实环境未连接<span className="poc-connection-note">导入实验观测后分析参数</span></div>
    {error && <div role="alert" className="poc-message error"><span>{error}</span><button disabled={busy} onClick={() => void load()}>重新读取</button><button aria-label="关闭 POC 错误" onClick={() => setError('')}>×</button></div>}
    {notice && <div role="status" className="poc-message">{notice}</div>}
    {loading ? <div className="poc-empty">读取 POC 场景…</div> : !campaigns.length ? <div className="poc-empty">暂无调优场景，点击“新建调优场景”。</div> : <>
      <div className="poc-campaign-select"><label>调优场景<select aria-label="选择 POC 调优场景" value={campaignId} disabled={busy} onChange={event => { setCampaignId(event.target.value); setTab('experiments'); setError(''); }}>{campaigns.map(value => <option key={value.id} value={value.id}>{value.title} / {value.protocol.toUpperCase()} / {value.source === 'mock' ? '模拟' : '实测'}</option>)}</select></label></div>
      {loadingDetail ? <div className="poc-empty">读取场景记录…</div> : campaign && <>
        <Scope campaign={campaign}/>
        <section className="poc-panel"><div className="poc-tabs" role="tablist" aria-label="POC 调优详情">{tabs.map((value, index) => <button role="tab" aria-selected={tab === value.id} aria-controls={'poc-panel-' + value.id} id={'poc-tab-' + value.id} tabIndex={tab === value.id ? 0 : -1} key={value.id} disabled={busy} onKeyDown={event => tabKey(event, index)} onClick={() => setTab(value.id)}>{value.title}</button>)}</div>
          <div role="tabpanel" id="poc-panel-parameters" aria-labelledby="poc-tab-parameters" hidden={tab !== 'parameters'}><ParameterRegistry definitions={definitions} activeIds={Object.keys(campaign.baseline_parameters)} onRegister={() => setImportKind('parameter')} busy={busy}/></div>
          <div role="tabpanel" id="poc-panel-experiments" aria-labelledby="poc-tab-experiments" hidden={tab !== 'experiments'}><div className="poc-section-heading"><div><h2>实验对比</h2><span>{campaign.trials.length} 条观测 · {campaign.goal.min_repeats} 次重复起评</span></div><div className="poc-actions"><button className="poc-button secondary" disabled={busy} onClick={() => setImportKind('trial')}>导入实验结果</button><button className="poc-button primary" disabled={busy || !campaign.trials.length} onClick={() => void analyze()}>{busy ? '处理中…' : '分析参数'}</button></div></div>{campaign.analysis ? <Groups analysis={campaign.analysis} campaign={campaign}/> : <p className="poc-help">未分析，导入观测后点击“分析参数”比较完整参数组合。</p>}<Trials campaign={campaign}/></div>
          <div role="tabpanel" id="poc-panel-conclusion" aria-labelledby="poc-tab-conclusion" hidden={tab !== 'conclusion'}><div className="poc-section-heading"><div><h2>结论</h2><RecommendationStats history={history}/></div>{history.length > 0 && (!campaign.analysis || history.length > 1) && <select aria-label="选择 POC 分析历史" value={historyId} disabled={busy} onChange={event => setHistoryId(event.target.value)}><option value="">{campaign.analysis ? '当前分析 · ' + date(campaign.analysis.created_at) : '当前记录 · 待重新分析'}</option>{history.filter(value => value.id !== campaign.analysis?.id).map((value, index) => <option key={value.id} value={value.id}>历史 {index + 1} · {date(value.created_at)}</option>)}</select>}</div><Conclusion key={campaign.id + '/' + (analysis?.id ?? 'empty')} campaign={campaign} analysis={analysis} historical={historical} busy={busy} onAnalyze={() => void analyze()} onFeedback={(verdict, note) => void feedback(verdict, note)}/></div>
        </section>
      </>}
    </>}
    {importKind && <JsonDialog kind={importKind} campaign={campaign} definitions={definitions} onClose={() => setImportKind(null)} onSubmit={body => submitJson(importKind, body)}/>}
  </div>;
}

function Scope({ campaign }: { campaign: PocCampaign }) {
  const fio = campaign.fio;
  return <section className="poc-scope"><div className="poc-scope-main"><div><span>设备</span><strong>{campaign.environment.label}</strong></div><div><span>版本 / 协议</span><strong>{campaign.build} · {campaign.protocol.toUpperCase()}</strong></div><div><span>fio</span><strong>{String(fio.rw ?? '未提供')} / {String(fio.bs ?? '未提供')} · QD {String(fio.iodepth ?? '—')} × {String(fio.numjobs ?? '—')}</strong></div><div><span>目标 / 约束</span><strong>{metricLabels[campaign.goal.metric]} · P99 {campaign.goal.p99_limit_ms == null ? '未设上限' : '≤' + fmt(campaign.goal.p99_limit_ms) + ' ms'}</strong></div></div><details className="poc-scope-details"><summary>固定适用范围 / 完整 fio / 基线参数</summary><pre>{JSON.stringify({ environment: campaign.environment, build: campaign.build, protocol: campaign.protocol, fio: campaign.fio, goal: campaign.goal, baseline_parameters: campaign.baseline_parameters }, null, 2)}</pre></details></section>;
}

function ParameterRegistry({ definitions, activeIds, onRegister, busy }: { definitions: PocParameterDefinition[]; activeIds: string[]; onRegister: () => void; busy: boolean }) {
  return <><div className="poc-section-heading"><div><h2>参数定义</h2><span>{definitions.length} 项注册 · 本场景 {activeIds.length} 项</span></div><button className="poc-button secondary" disabled={busy} onClick={onRegister}>注册参数定义</button></div><div className="poc-parameter-list">{definitions.map(def => <article key={def.id} className="poc-parameter"><div className="poc-parameter-heading"><h3>{def.label}</h3><span className={'poc-tag ' + (def.maturity === 'example' ? 'example' : '')}>{def.maturity === 'example' ? '示例定义' : '已注册'}</span>{activeIds.includes(def.id) && <span className="poc-tag">本场景</span>}</div><p>{def.description}</p><dl><dt>标识 / 组件</dt><dd><code>{def.id}</code> / {def.component}</dd><dt>读取 / 应用绑定</dt><dd>{def.adapter_key || '待接入'}</dd><dt>{def.maturity === 'example' ? '示例档位' : '值范围'}</dt><dd>{def.choices.length ? def.choices.map(valueText).join(' / ') : def.min != null || def.max != null ? fmt(def.min) + ' ～ ' + fmt(def.max) : '待提供'}{def.maturity === 'example' ? '（非真实单位）' : def.unit ? ' / ' + def.unit : ''}</dd><dt>协议 / 类型</dt><dd>{def.protocols.map(protocol => protocol.toUpperCase()).join(', ')} / {def.value_type}</dd><dt>观察指标</dt><dd>{def.observables.join(', ') || '待提供'}</dd></dl><details><summary>设备 / fio 适用条件</summary><pre>{JSON.stringify({ device_requirements: def.device_requirements, fio_requirements: def.fio_requirements, unit: def.unit }, null, 2)}</pre></details></article>)}{!definitions.length && <div className="poc-empty">暂无参数定义。</div>}</div></>;
}

function Groups({ analysis, campaign }: { analysis: PocAnalysis; campaign: PocCampaign }) {
  return <><Table className="poc-group-table"><thead><tr><th>完整参数组合</th><th>重复</th><th>median {metricLabels[campaign.goal.metric]}</th><th>median P99 / ms</th><th>磁盘延迟 / ms</th><th>tierd 深度</th><th>状态</th></tr></thead><tbody>{analysis.groups.map(group => <tr key={group.id} className={group.id === analysis.best_observed_group_id ? 'poc-selected-group' : ''}><td><Parameters values={group.parameters} definitions={campaign.parameter_definitions}/>{group.id === analysis.baseline_group_id && <span className="poc-tag">基线组合</span>}</td><td className="poc-number">{group.repeats}</td><td className="poc-number">{fmt(group.medians[campaign.goal.metric], campaign.goal.metric === 'iops' ? 0 : 2)}</td><td className="poc-number">{fmt(group.medians.p99_ms)}</td><td className="poc-number">{fmt(group.medians.disk_latency_ms)}</td><td className="poc-number">{fmt(group.medians.tierd_depth)}</td><td><span className={'poc-group-status ' + (group.eligible ? 'eligible' : 'limited')}>{group.eligible ? group.id === analysis.best_observed_group_id ? '当前样本候选' : '约束内' : '不参与候选'}</span>{group.reason && <small>{group.reason}</small>}</td></tr>)}</tbody></Table><p className="poc-help">仅比较本场景已观察的参数组合；{campaign.goal.p99_limit_ms == null ? '未设时延上限，候选需复测。' : '候选须满足时延约束与重复次数。'}缺失指标为“—”。</p><details className="poc-disclosure"><summary>完整目标指标 / 离散范围</summary><GroupRanges groups={analysis.groups}/></details></>;
}

function GroupRanges({ groups }: { groups: PocGroup[] }) {
  return <Table><thead><tr><th>组</th><th>median IOPS</th><th>带宽 median / MiB/s</th><th>平均延迟 median / ms</th><th>观测范围</th></tr></thead><tbody>{groups.map((group, index) => <tr key={group.id}><td>组合 {index + 1}</td><td className="poc-number">{fmt(group.medians.iops, 0)}</td><td className="poc-number">{fmt(group.medians.bandwidth_mib_s)}</td><td className="poc-number">{fmt(group.medians.latency_ms)}</td><td><pre>{JSON.stringify(group.range, null, 2)}</pre></td></tr>)}</tbody></Table>;
}

function Trials({ campaign }: { campaign: PocCampaign }) {
  return <details className="poc-disclosure poc-trials" open={!campaign.analysis}><summary>原始实验观测 · {campaign.trials.length} 条</summary>{campaign.trials.length ? <Table><thead><tr><th>记录 / 完整参数</th><th>IOPS</th><th>P99 / ms</th><th>磁盘延迟 / ms</th><th>tierd 深度</th><th>资料</th></tr></thead><tbody>{campaign.trials.map(trial => <tr key={trial.id}><td><strong>{trial.id}</strong><small>{date(trial.created_at)} · {trial.source === 'mock' ? '模拟' : '实测'}</small><Parameters values={trial.parameters} definitions={campaign.parameter_definitions}/></td><td className="poc-number">{fmt(trial.metrics.iops, 0)}</td><td className="poc-number">{fmt(trial.metrics.p99_ms)}</td><td className="poc-number">{fmt(trial.metrics.disk_latency_ms)}</td><td className="poc-number">{fmt(trial.metrics.tierd_depth)}</td><td><details><summary>记录</summary><pre>{JSON.stringify({ metrics: trial.metrics, artifacts: trial.artifacts, note: trial.note }, null, 2)}</pre></details></td></tr>)}</tbody></Table> : <div className="poc-empty">暂无观测。导入已完成的实验结果后分析。</div>}</details>;
}

function Conclusion({ campaign, analysis, historical, busy, onAnalyze, onFeedback }: { campaign: PocCampaign; analysis: PocAnalysis | null; historical: boolean; busy: boolean; onAnalyze: () => void; onFeedback: (verdict: PocVerdict, note: string) => void }) {
  if (!analysis) return <div className="poc-no-analysis"><p>{campaign.analysis_history.length ? '实验观测已更新，需重新分析；可查看历史结论与评价。' : '暂无分析结论。'}</p><button className="poc-button primary" disabled={busy || !campaign.trials.length} onClick={onAnalyze}>分析参数</button></div>;
  return <div className="poc-conclusion">
    {historical && <div className="poc-history-note">历史分析与当时评价只读。</div>}
    <div className="poc-flow-status">{analysis.steps.map((step, index) => <div key={step.id} title={step.detail}><span>{index + 1}</span><strong>{step.title}</strong><small>{statusLabels[step.status] || step.status}</small></div>)}</div>
    <p className="poc-analysis-summary">{analysis.summary}</p>
    {analysis.recommendation ? <><div className="poc-recommendation"><div className="poc-recommendation-heading"><h3>{campaign.goal.p99_limit_ms == null ? '当前样本目标指标候选' : '约束下的候选参数'}</h3><span className="poc-tag">{labels[analysis.recommendation.verdict]}</span></div><Parameters values={analysis.recommendation.parameters} definitions={campaign.parameter_definitions}/><div className="poc-recommendation-metrics"><span>{metricLabels[analysis.recommendation.goal_metric]}</span><span className="poc-number">基线 {fmt(analysis.recommendation.baseline_value)} → 观测 {fmt(analysis.recommendation.observed_value)}</span><span className="poc-number">{analysis.recommendation.change_pct == null ? '—' : (analysis.recommendation.change_pct > 0 ? '+' : '') + fmt(analysis.recommendation.change_pct, 1) + '%'}</span></div><p>适用：{campaign.environment.label} · {campaign.build} · {campaign.protocol.toUpperCase()} · 当前完整 fio；{campaign.goal.p99_limit_ms == null ? '未设时延上限，需补约束并复测。' : '仅代表当前已测试参数组合。'}</p></div><Evaluation key={analysis.id} recommendation={analysis.recommendation} historical={historical} busy={busy} onSave={onFeedback}/></> : <p className="poc-help">尚无符合当前条件的候选参数，请查看各组约束和样本数量。</p>}
    {analysis.correlations.length > 0 && <details className="poc-disclosure"><summary>参数与指标观察 · {analysis.correlations.length} 项</summary><ul>{analysis.correlations.map((value, index) => <li key={value.parameter_id + '/' + index}><strong>{campaign.parameter_definitions.find(def => def.id === value.parameter_id)?.label || value.parameter_id}</strong><p>{value.summary}</p><small>{value.observables.join(', ')} · 关联观察，尚未验证因果</small></li>)}</ul></details>}
    <details className="poc-disclosure"><summary>分析流程 · {analysis.framework} · {analysis.steps.length} 步</summary><Table><thead><tr><th>步骤</th><th>状态</th><th>记录</th></tr></thead><tbody>{analysis.steps.map(step => <tr key={step.id}><td>{step.title}</td><td>{statusLabels[step.status] || step.status}</td><td>{step.detail}</td></tr>)}</tbody></Table><p className="poc-help">{analysis.id} · {date(analysis.created_at)}</p></details>
  </div>;
}

function RecommendationStats({ history }: { history: PocAnalysis[] }) {
  const recommendations = [...new Map(history.filter(value => value.recommendation).map(value => [value.id, value.recommendation!])).values()];
  if (!recommendations.length) return null;
  return <span className="poc-recommendation-stats" title="仅统计本场景，各独立分析的建议按最新评价计数；当前分析不重复计算">建议：确认 {recommendations.filter(value => value.verdict === 'confirmed').length} / 否定 {recommendations.filter(value => value.verdict === 'rejected').length} / 待验证 {recommendations.filter(value => value.verdict === 'pending').length}</span>;
}

function Evaluation({ recommendation, historical, busy, onSave }: { recommendation: PocRecommendation; historical: boolean; busy: boolean; onSave: (verdict: PocVerdict, note: string) => void }) {
  const [verdict, setVerdict] = useState(recommendation.verdict), [note, setNote] = useState(recommendation.note);
  useEffect(() => { setVerdict(recommendation.verdict); setNote(recommendation.note); }, [recommendation.verdict, recommendation.note]);
  return <section className="poc-evaluation"><div className="poc-section-heading"><h3>建议评价</h3>{historical && <span>历史记录只读</span>}</div><div className="poc-verdict-options">{(['pending', 'confirmed', 'rejected'] as const).map(value => <button disabled={busy || historical} key={value} aria-pressed={verdict === value} onClick={() => setVerdict(value)}>{labels[value]}</button>)}</div><textarea aria-label="POC 专家评价理由" readOnly={historical} disabled={busy} value={note} onChange={event => setNote(event.target.value)} placeholder="验证证据 / 否定理由（必填）" rows={2}/><div className="poc-evaluation-bottom"><details className="poc-review-history"><summary>历次评价 · {recommendation.review_history.length}</summary>{recommendation.review_history.map((value, index) => <div key={index}><strong>{labels[value.verdict]}</strong> · {value.reviewer} · {date(value.timestamp)}<p>{value.note}</p></div>)}</details>{!historical && <button className="poc-button secondary" disabled={busy || !note.trim()} onClick={() => onSave(verdict, note)}>{busy ? '保存中…' : '保存评价'}</button>}</div></section>;
}

function template(kind: ImportKind, campaign: PocCampaign | null, definitions: PocParameterDefinition[]) {
  if (kind === 'parameter') return { id: 'example-new-parameter', label: '新参数（示例）', component: '待提供组件', value_type: 'enum', unit: null, protocols: ['vhost'], description: '示例档位，实际参数名、单位、范围及适用条件待提供。', observables: ['iops', 'p99_ms', 'disk_latency_ms', 'tierd_depth'], choices: ['example-low', 'example-high'], min: null, max: null, maturity: 'example', adapter_key: null, device_requirements: {}, fio_requirements: {} };
  if (kind === 'campaign') return { title: '新 POC 调优场景', source: 'mock', environment: { id: 'poc-device-profile', label: '待填写设备档案', hardware: {} }, build: campaign?.build ?? '待填写版本', protocol: campaign?.protocol ?? 'vhost', fio: campaign?.fio ?? { rw: '待填写 fio 模型', bs: '待填写块大小', iodepth: '待填写深度', numjobs: '待填写并发' }, goal: { metric: 'iops', p99_limit_ms: null, min_repeats: 3 }, baseline_parameters: campaign?.baseline_parameters ?? Object.fromEntries(definitions.filter(def => def.protocols.includes('vhost') && def.maturity === 'example').map(def => [def.id, def.choices[0] ?? '待填写基线值'])) };
  return { environment_id: campaign?.environment.id ?? '待选择场景', build: campaign?.build, protocol: campaign?.protocol, fio: campaign?.fio, source: campaign?.source, parameters: campaign?.baseline_parameters ?? {}, metrics: { iops: '填写本次观测 IOPS', bandwidth_mib_s: '填写本次观测带宽', latency_ms: '填写本次观测平均延迟', p99_ms: null, disk_latency_ms: null, tierd_depth: null }, artifacts: [], note: '' };
}

function JsonDialog({ kind, campaign, definitions, onClose, onSubmit }: { kind: ImportKind; campaign: PocCampaign | null; definitions: PocParameterDefinition[]; onClose: () => void; onSubmit: (value: unknown) => Promise<void> }) {
  const title = kind === 'campaign' ? '新建调优场景' : kind === 'parameter' ? '注册参数定义' : '导入实验结果';
  const initial = JSON.stringify(template(kind, campaign, definitions), null, 2);
  const [payload, setPayload] = useState(initial), [busy, setBusy] = useState(false), [error, setError] = useState(''), [copied, setCopied] = useState(false);
  const panel = useRef<HTMLDivElement>(null), latest = useRef({ busy, onClose }); latest.current = { busy, onClose };
  useEffect(() => {
    const focus = document.activeElement as HTMLElement | null, overflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; panel.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus();
    const handle = (event: KeyboardEvent) => { if (event.key === 'Escape' && !latest.current.busy) latest.current.onClose(); if (event.key === 'Tab' && panel.current) { const elements = [...panel.current.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled])')]; const first = elements[0], last = elements.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } };
    document.addEventListener('keydown', handle); return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', handle); focus?.focus(); };
  }, []);
  async function submit() { if (busy) return; setError(''); let body: unknown; try { body = JSON.parse(payload); } catch { setError('JSON 格式错误，请检查引号、逗号和括号。'); return; } setBusy(true); try { await onSubmit(body); } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败'); } finally { setBusy(false); } }
  async function copy() { try { await navigator.clipboard.writeText(payload); setCopied(true); } catch { setError('无法访问剪贴板，可在 JSON 中选中复制。'); } }
  return <div className="poc-modal-backdrop" onClick={event => { if (event.target === event.currentTarget && !busy) onClose(); }}><div className="poc-modal" ref={panel} role="dialog" aria-modal="true" aria-labelledby="poc-json-title"><header><h2 id="poc-json-title">{title}</h2><button disabled={busy} onClick={onClose} aria-label="关闭 POC JSON 窗口">×</button></header><div className="poc-modal-body"><div className="poc-json-tools"><button disabled={busy} onClick={() => { setPayload(initial); setError(''); setCopied(false); }}>载入模板</button><button disabled={busy} onClick={() => void copy()}>{copied ? '已复制' : '复制 JSON'}</button></div><textarea aria-label="POC JSON 内容" value={payload} disabled={busy} onChange={event => { setPayload(event.target.value); setCopied(false); }} spellCheck={false}/><p>{kind === 'trial' ? '填写已完成实验的观测值；保持设备、版本、协议、fio 与来源一致，参数键必须完整。当前操作不运行实验。' : kind === 'campaign' ? '模板默认模拟来源；实测场景改为 measured，填写设备与完整 fio 后创建。该场景内设备、版本、协议及 fio 固定。' : '示例档位用于界面演示；实际参数名、范围、单位与设备 / fio 条件确认后注册。'}缺失指标用 null。</p>{error && <div role="alert" className="poc-json-error">{error}</div>}</div><footer><button className="poc-button secondary" disabled={busy} onClick={onClose}>取消</button><button className="poc-button primary" disabled={busy || !payload.trim()} onClick={() => void submit()}>{busy ? '校验中…' : kind === 'campaign' ? '校验并创建' : kind === 'parameter' ? '校验并注册' : '校验并导入'}</button></footer></div></div>;
}
