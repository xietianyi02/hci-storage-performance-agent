import { useEffect, useMemo, useRef, useState } from 'react';
import { formatNumber } from './numberFormat';
import type { ReactNode } from 'react';
import type { CpuCapture, CpuTopology, HostRole, IndividualThreadSample, ObservationPlan, SampleWindow, VersionComparison } from './versionTypes';
import { alignSampleWindows, endpointTolerance, measurementPointCount, phaseOffset, plansComparable } from './observationWindows';
import { allObservedCpus, busyTone, cpuIdentity } from './cpuObservation';
import type { CpuObservation } from './cpuObservation';
import { groupIoThreads } from './ioThreadOrder';
import { capturedCpuTotal, groupProcessThreads } from './processThreadGroups';
import './time-observation.css';

const hosts: Record<HostRole, string> = { guest: 'guest', local: '本地', remote: '远端' };
const number = (value: number | null | undefined, digits = 1) => formatNumber(value, digits);
const percent = (value: number | null | undefined, digits = 1) => value == null ? '—' : number(value, digits) + '%';
const windowLabel = (value: SampleWindow, plan?: ObservationPlan | null) => {
  const offset = phaseOffset(value, plan);
  return (value.phase === 'measurement' ? '正式 ' : '预热 ') + number(offset.start, 0) + '–' + number(offset.end, 0) + ' s';
};

export interface ObservationSelection {
  comparisonId: string;
  includeWarmup: boolean;
  selectedKey: string;
  side: 'current' | 'previous';
}
export const initialObservationSelection: ObservationSelection = { comparisonId: '', includeWarmup: false, selectedKey: '', side: 'current' };
type ObservationMode = 'threads' | 'cpu' | 'layers';
export type ThreadGrouping = 'process' | 'io';

function validIpc(row: IndividualThreadSample) {
  const scope = row.counter_scope?.trim().toLowerCase();
  return row.instructions != null && row.instructions >= 0 && row.cycles != null && row.cycles > 0
    && row.counting_ratio != null && row.counting_ratio >= 0.9 && row.counting_ratio <= 1
    && !!scope && !/^(unknown|unspecified|unavailable|not_collected|none|未知|未采集|未记录|缺失)(\b|:|：|$)/.test(scope)
    ? row.instructions / row.cycles : null;
}

function logicalCpu(row: IndividualThreadSample, sample: SampleWindow) {
  return sample.cpus.find(cpu => cpu.host_role === row.host_role && cpu.cpu_id === row.cpu_id);
}

function Smt({ cpu, sample }: { cpu?: CpuCapture; sample: SampleWindow }) {
  if (!cpu?.smt_sibling_cpu_ids?.length) return <>—</>;
  const siblings = cpu.smt_sibling_cpu_ids.filter(id => id !== cpu.cpu_id);
  if (!siblings.length) return <span className="to-muted">无同核兄弟</span>;
  return <>{siblings.map(id => {
    const sibling = sample.cpus.find(value => value.host_role === cpu.host_role && value.cpu_id === id);
    return <div key={id} className="vd-number">CPU {id} · {percent(sibling?.busy_pct)}</div>;
  })}</>;
}

export default function TimeObservation({ item, children, mode = 'threads', selection, onSelectionChange, threadGrouping, onThreadGroupingChange }: { item: VersionComparison; children?: ReactNode; mode?: ObservationMode; selection?: ObservationSelection; onSelectionChange?: (value: ObservationSelection) => void; threadGrouping?: ThreadGrouping; onThreadGroupingChange?: (value: ThreadGrouping) => void }) {
  const [localSelection, setLocalSelection] = useState(initialObservationSelection);
  const [localGrouping, setLocalGrouping] = useState<ThreadGrouping>('process');
  const grouping = threadGrouping ?? localGrouping;
  const groupingControl = <div className="to-group-switch" role="group" aria-label="线程展示方式">{([{ value: 'process', label: '按进程' }, { value: 'io', label: '按 IO 流' }] as const).map(option => <button key={option.value} aria-pressed={grouping === option.value} onClick={() => onThreadGroupingChange ? onThreadGroupingChange(option.value) : setLocalGrouping(option.value)}>{option.label}</button>)}</div>;
  const savedSelection = selection ?? localSelection;
  const activeSelection = savedSelection.comparisonId === item.id ? savedSelection : { ...initialObservationSelection, comparisonId: item.id };
  const { includeWarmup, selectedKey, side } = activeSelection;
  function updateSelection(patch: Partial<Omit<ObservationSelection, 'comparisonId'>>) {
    const next = { ...activeSelection, ...patch, comparisonId: item.id };
    if (onSelectionChange) onSelectionChange(next); else setLocalSelection(next);
  }
  const currentWindows = item.current.sample_windows ?? [];
  const previousWindows = item.previous?.sample_windows ?? [];
  const currentPlan = item.current.observation, previousPlan = item.previous?.observation;
  const plan = currentPlan ?? previousPlan;
  const comparable = plansComparable(currentPlan, previousPlan);
  const toleranceMs = Math.min(endpointTolerance(currentPlan), endpointTolerance(previousPlan)) * 1000;
  const windows = useMemo(() => alignSampleWindows(currentWindows, previousWindows, currentPlan, previousPlan).filter(choice => includeWarmup || (choice.current ?? choice.previous!).phase === 'measurement'), [currentWindows, previousWindows, currentPlan, previousPlan, includeWarmup]);
  const index = Math.max(0, windows.findIndex(value => value.key === selectedKey));
  const selected = windows[index];
  const key = selected?.key;
  const current = selected?.current;
  const previous = selected?.previous;
  const selectedWindow = current ?? previous;
  const sample = side === 'current' ? current : previous;
  const delta = comparable && current?.iops != null && previous?.iops != null && previous.iops > 0
    ? (current.iops / previous.iops - 1) * 100 : null;
  function step(offset: number) {
    const next = windows[index + offset];
    if (next) updateSelection({ selectedKey: next.key });
  }
  const counts = {
    current: currentWindows.filter(value => value.phase === 'measurement').length,
    previous: previousWindows.filter(value => value.phase === 'measurement').length,
  };
  const expectedPoints = measurementPointCount(plan);

  return <div className="to-workbench">
    <div className="to-plan">
      <div><strong>按时间观察</strong><span>{plan
        ? '预热 ' + number(plan.warmup_s, 0) + ' s · 正式 ' + number(plan.measurement_s, 0) + ' s · 每 ' + number(plan.interval_s, 0) + ' s'
        : '未记录采样计划'}</span></div>
      <label><input type="checkbox" checked={includeWarmup} disabled={!currentWindows.length && !previousWindows.length} onChange={event => updateSelection({ includeWarmup: event.target.checked })}/>包含预热</label>
    </div>
    {!windows.length ? <>{mode === 'threads' && <div className="to-thread-heading">{groupingControl}</div>}<div className="to-no-data">
      <strong>{mode === 'layers' ? '层时延 / 深度待接入' : mode === 'cpu' ? '未采集 CPU 时序数据' : '未采集逐线程时序数据'}</strong>
    </div></> : <>
      <div className="to-window-controls">
        <button className="to-step" aria-label="上一个采样区间" disabled={index === 0} onClick={() => step(-1)}>←</button>
        <select aria-label="选择采样区间" value={key} onChange={event => updateSelection({ selectedKey: event.target.value })}>
          {windows.map((value, i) => <option key={value.key} value={value.key}>{i + 1}/{windows.length} · {windowLabel(value.current ?? value.previous!, value.current ? currentPlan : previousPlan)}{value.current && value.previous ? '' : value.current ? ' · 仅本次' : ' · 仅上次'}</option>)}
        </select>
        <button className="to-step" aria-label="下一个采样区间" disabled={index >= windows.length - 1} onClick={() => step(1)}>→</button>
        <input type="range" aria-label="拖动选择采样区间" min={0} max={Math.max(0, windows.length - 1)} value={index} disabled={windows.length === 1} onChange={event => updateSelection({ selectedKey: windows[Number(event.target.value)].key })}/>
        <span className="to-point-count">正式采集 {counts.previous} → {counts.current}{expectedPoints == null ? '' : ' / ' + expectedPoints} 点</span>
      </div>
      <div className="to-iops-line">
        <span>区间平均 IOPS</span><span className="vd-number"><span className="to-muted">上次 {number(previous?.iops, 0)}</span> → <strong>本次 {number(current?.iops, 0)}</strong></span>
        <span className={'vd-number ' + (delta != null && delta < 0 ? 'vd-negative' : 'to-muted')}>{delta == null ? '—' : (delta > 0 ? '+' : '') + number(delta, 1) + '%'}</span>
        <span className="to-window-offset">{current ? '本次' : '上次'}总时间 {number(selectedWindow?.start_offset_s, 2)}–{number(selectedWindow?.end_offset_s, 2)} s</span>
      </div>
      <div className="to-thread-heading"><div className="to-thread-actions"><div className="to-side-switch" aria-label="采样轮次"><button aria-pressed={side === 'current'} onClick={() => updateSelection({ side: 'current' })}>本次{mode === 'threads' ? '线程' : ''}</button><button aria-pressed={side === 'previous'} disabled={!item.previous} onClick={() => updateSelection({ side: 'previous' })}>上次{mode === 'threads' ? '线程' : ''}</button></div>{mode === 'threads' && groupingControl}</div><span>{mode === 'threads' ? (sample?.threads.length ?? 0) + ' 个线程 · ' : ''}{side === 'current' ? item.current_batch.build : item.previous_batch?.build}</span></div>
      {!sample ? <div className="to-no-data"><p>{side === 'current' ? '本次' : '上次'}未采集这个区间，不能用整轮均值替代。</p></div>
        : mode === 'cpu' ? <WindowCpu sample={sample} inventory={side === 'current' ? item.current.cpu_inventory ?? [] : item.previous?.cpu_inventory ?? []}/>
          : mode === 'layers' ? <WindowLayers sample={sample}/>
            : !sample.threads.length ? <div className="to-no-data"><p>这个区间未采集具体线程。</p></div>
              : <ThreadWindow sample={sample} protocol={item.current.protocol} grouping={grouping}/>}
      <p className="to-footnote" title={comparable ? '同计划区间端点偏差 ≤' + number(toleranceMs, 0) + ' ms，唯一匹配，不插值。SMT 表示同物理核逻辑 CPU 的同期负载。' : '采样计划不同或缺失，不提供区间差异。'}>区间统计{mode === 'threads' ? ' · 落核为采样端点' : ''} · {!comparable?'采样计划不可比':current&&previous?'已对齐':'区间未匹配'}</p>
      <details className="to-details"><summary>实际采样区间 / 对齐口径</summary><div className="to-alignment"><p>相同采样间隔；预热与正式时长差各 ≤100 ms；正式区间减去各自预热时长后，起止偏差各 ≤min(100 ms, 间隔 ×3%)。多解或超限保持缺失。</p><dl><dt>上次实际区间</dt><dd>{previous ? number(previous.start_offset_s, 4) + '–' + number(previous.end_offset_s, 4) + ' s / duration ' + number(previous.end_offset_s - previous.start_offset_s, 4) + ' s' : '—'}</dd><dt>本次实际区间</dt><dd>{current ? number(current.start_offset_s, 4) + '–' + number(current.end_offset_s, 4) + ' s / duration ' + number(current.end_offset_s - current.start_offset_s, 4) + ' s' : '—'}</dd><dt>上次采样计划</dt><dd>{previousPlan ? '预热 ' + number(previousPlan.warmup_s, 4) + ' s / 正式 ' + number(previousPlan.measurement_s, 4) + ' s / 间隔 ' + number(previousPlan.interval_s, 4) + ' s' : '—'}</dd><dt>本次采样计划</dt><dd>{currentPlan ? '预热 ' + number(currentPlan.warmup_s, 4) + ' s / 正式 ' + number(currentPlan.measurement_s, 4) + ' s / 间隔 ' + number(currentPlan.interval_s, 4) + ' s' : '—'}</dd></dl></div></details>
    </>}
    {children && (mode === 'threads' ? <details className="to-details to-summary"><summary>整轮线程池汇总</summary>{children}</details> : children)}
  </div>;
}

function ThreadWindow({ sample, protocol, grouping }: { sample: SampleWindow; protocol: 'nfs' | 'vhost' | 'iscsi'; grouping: ThreadGrouping }) {
  const groups = grouping === 'io' ? groupIoThreads(sample.threads, row => ({ ...row, protocol }))
    : groupProcessThreads(sample.threads).map(group => ({ id: group.id, title: (hosts[group.host_role as HostRole] ?? group.host_role) + ' · ' + group.process_role + (group.pid == null ? '' : ' · PID ' + group.pid), rows: group.rows }));
  return <div className="vd-table-scroll"><table className="vd-table to-thread-table"><thead><tr><th>{grouping === 'process' ? '线程 / TID' : '主机 / 进程 / 线程'}</th><th>CPU 端点</th><th title="线程在此区间内的 CPU 利用率">CPU%</th><th>IPC</th><th>SMT 兄弟 / busy%</th><th>详情</th></tr></thead>{groups.map(group => <tbody key={group.id} aria-label={group.title}><tr className="to-thread-group"><th colSpan={6}><strong>{group.title}</strong><span>{group.rows.length} 个线程</span>{grouping === 'process' && <span className="to-process-total" title="所选区间已采集线程的 CPU 合计，可跨核超过 100%；任一线程缺失时不提供合计。">已采集 CPU 合计 {percent(capturedCpuTotal(group.rows))}</span>}</th></tr>{group.rows.map(row => {
    const cpu = logicalCpu(row, sample);
    const moved = row.previous_cpu_id != null && row.cpu_id != null && row.previous_cpu_id !== row.cpu_id;
    return <tr key={[row.host_role, row.pid, row.tid, row.starttime_ticks].join('/')}>
      <td><strong>{grouping === 'process' ? row.name||row.pool : hosts[row.host_role] + ' · ' + row.process_role}</strong>{grouping === 'io' && <small>{row.name||row.pool}</small>}<small className="to-identity" title="PID / TID">{grouping === 'process' ? 'TID ' + row.tid : row.pid + ' / ' + row.tid}</small></td>
      <td><span className={'vd-number ' + (moved ? 'to-moved' : '')}>{number(row.previous_cpu_id, 0)} → {number(row.cpu_id, 0)}</span><small className="to-cpu-note">NUMA {number(row.numa_node, 0)}<br/>busy {percent(cpu?.busy_pct)}</small></td>
      <td className="vd-number">{number(row.cpu_pct)}</td>
      <td className="vd-number">{number(validIpc(row), 2)}</td>
      <td className="to-smt"><Smt cpu={cpu} sample={sample}/></td>
      <td><details className="to-thread-detail"><summary>计数 / 调度</summary><dl>
        <dt>线程池 / 分支</dt><dd>{row.pool} / {row.branch||'—'}</dd>
        <dt>允许 CPU</dt><dd>{row.allowed_cpu_ids.length ? row.allowed_cpu_ids.join(', ') : '—'}</dd>
        <dt>迁移次数</dt><dd>{number(row.migrations, 0)}</dd>
        <dt>队列等待 / ms</dt><dd>{number(row.runqueue_wait_ms, 2)}</dd>
        <dt>instructions</dt><dd>{number(row.instructions, 0)}</dd>
        <dt>cycles</dt><dd>{number(row.cycles, 0)}</dd>
        <dt>计数覆盖</dt><dd>{row.counting_ratio == null ? '—' : percent(row.counting_ratio * 100, 1)}</dd>
        <dt>计数范围</dt><dd>{row.counter_scope || '—'}</dd>
        <dt>状态 / starttime</dt><dd>{row.state || '—'} / {number(row.starttime_ticks, 0)}</dd>
        <dt>socket / core</dt><dd>{number(cpu?.socket_id, 0)} / {number(cpu?.core_id, 0)}</dd>
      </dl><p>IPC = instructions / cycles；覆盖不足 90% 或范围不明时不展示。</p></details></td>
    </tr>;
  })}</tbody>)}</table></div>;
}

function WindowCpu({ sample, inventory }: { sample: SampleWindow; inventory: CpuTopology[] }) {
  const [host, setHost] = useState<HostRole | 'all'>(() => [...inventory, ...sample.cpus].some(cpu => cpu.host_role === 'local') ? 'local' : 'all');
  const [numa, setNuma] = useState('all');
  const [selectedCpu, setSelectedCpu] = useState('');
  const cpuDetailsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedCpu) cpuDetailsRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [selectedCpu]);
  const rows = useMemo(() => allObservedCpus(inventory, sample.cpus), [inventory, sample.cpus]);
  const activeHost = host === 'all' || rows.some(cpu => cpu.host_role === host) ? host : 'all';
  const hostRows = rows.filter(cpu => activeHost === 'all' || cpu.host_role === activeHost);
  const numaOptions = [...new Set(hostRows.map(cpu => cpu.numa_node == null ? 'unknown' : String(cpu.numa_node)))].sort((a, b) => a === 'unknown' ? 1 : b === 'unknown' ? -1 : Number(a) - Number(b));
  const activeNuma = numa === 'all' || numaOptions.includes(numa) ? numa : 'all';
  const filtered = hostRows.filter(cpu => activeNuma === 'all' || (cpu.numa_node == null ? 'unknown' : String(cpu.numa_node)) === activeNuma);
  const roles = (['guest', 'local', 'remote'] as const).filter(role => filtered.some(cpu => cpu.host_role === role));
  const selected = filtered.find(cpu => cpuIdentity(cpu) === selectedCpu);
  return <div className="to-cpu-workbench">
    <div className="to-cpu-controls"><label>主机<select aria-label="筛选 CPU 主机" value={activeHost} onChange={event => { setHost(event.target.value as HostRole | 'all'); setNuma('all'); setSelectedCpu(''); }}>{['all', 'guest', 'local', 'remote'].filter(role => role === 'all' || rows.some(cpu => cpu.host_role === role)).map(role => <option key={role} value={role}>{role === 'all' ? '全部主机' : hosts[role as HostRole]}</option>)}</select></label><label>NUMA<select aria-label="筛选 CPU NUMA" value={activeNuma} onChange={event => { setNuma(event.target.value); setSelectedCpu(''); }}><option value="all">全部 NUMA</option>{numaOptions.map(node => <option key={node} value={node}>{node === 'unknown' ? 'NUMA 未知' : 'NUMA ' + node}</option>)}</select></label><span>{activeHost === 'all' ? '按主机展示' : filtered.length + ' 个逻辑 CPU'} · 包含空闲核</span></div>
    {!rows.length ? <div className="to-no-data">未采集 CPU 负载，CPU 范围未知。</div> : <>
      <div className="to-cpu-legend" aria-label="CPU busy 热度范围">{[['cool', '0–20%'], ['low', '20–50%'], ['medium', '50–80%'], ['high', '80–95%'], ['max', '95–100%'], ['unknown', '缺失']].map(([tone, label]) => <span key={tone}><i className={'to-cpu-tone-' + tone}/>{label}</span>)}</div>
      {roles.map(role => {
        const group = filtered.filter(cpu => cpu.host_role === role);
        const known = inventory.filter(cpu => cpu.host_role === role).length;
        const captured = rows.filter(cpu => cpu.host_role === role && cpu.capture && (!known || cpu.inInventory)).length;
        const extra = rows.filter(cpu => cpu.host_role === role && !cpu.inInventory).length;
        return <section className="to-cpu-host" key={role}><div className="to-cpu-host-heading"><h3>{hosts[role]}</h3><span>{known ? '已采集 ' + captured + ' / ' + known + ' 核' + (extra ? ' · 档案外 ' + extra + ' 核' : '') : '已采集 ' + captured + ' 核 · 范围未知'}</span></div><div className="to-cpu-grid">{group.map(cpu => <button key={cpuIdentity(cpu)} aria-label={hosts[role] + ' CPU ' + cpu.cpu_id + '，busy ' + percent(cpu.capture?.busy_pct)} aria-pressed={selectedCpu === cpuIdentity(cpu)} className={'to-cpu-cell to-cpu-tone-' + busyTone(cpu.capture?.busy_pct)} title={'CPU ' + cpu.cpu_id + ' · NUMA ' + number(cpu.numa_node, 0) + ' · busy ' + percent(cpu.capture?.busy_pct)} onClick={() => setSelectedCpu(selectedCpu === cpuIdentity(cpu) ? '' : cpuIdentity(cpu))}><strong>{cpu.cpu_id}</strong><span>{percent(cpu.capture?.busy_pct, 0)}</span></button>)}</div></section>;
      })}
      {!filtered.length && <div className="to-no-data">此筛选范围没有 CPU。</div>}
      {selected && <div ref={cpuDetailsRef}><CpuDetails cpu={selected} rows={rows} sample={sample} onClose={() => setSelectedCpu('')}/></div>}
      <p className="to-cpu-note">点击 CPU 查看同区间指标。缺失为“—”；IRQ / softirq 为逻辑 CPU 总负载。</p>
      <details className="to-details"><summary>CPU 明细表{activeHost === 'all' ? ' · 按主机' : ' · ' + filtered.length + ' 核'}</summary><div className="vd-table-scroll"><table className="vd-table to-all-cpu-table"><thead><tr><th>主机 / CPU</th><th>NUMA</th><th>socket / core</th><th>SMT 同核 CPU</th><th>busy%</th><th>User%</th><th>System%</th><th>IRQ%</th><th>SoftIRQ%</th><th>Idle%</th></tr></thead><tbody>{filtered.map(cpu => <tr key={cpuIdentity(cpu)}><td>{hosts[cpu.host_role]} / {cpu.cpu_id}</td><td className="vd-number">{number(cpu.numa_node, 0)}</td><td className="vd-number">{number(cpu.socket_id, 0)} / {number(cpu.core_id, 0)}</td><td className="vd-number">{cpu.smt_sibling_cpu_ids.length ? cpu.smt_sibling_cpu_ids.join(', ') : '—'}</td><td className="vd-number">{number(cpu.capture?.busy_pct)}</td><td className="vd-number">{number(cpu.capture?.user_pct)}</td><td className="vd-number">{number(cpu.capture?.system_pct)}</td><td className="vd-number">{number(cpu.capture?.irq_pct)}</td><td className="vd-number">{number(cpu.capture?.softirq_pct)}</td><td className="vd-number">{number(cpu.capture?.idle_pct)}</td></tr>)}</tbody></table></div></details>
    </>}
  </div>;
}

function CpuDetails({ cpu, rows, sample, onClose }: { cpu: CpuObservation; rows: CpuObservation[]; sample: SampleWindow; onClose: () => void }) {
  const values = [['busy', cpu.capture?.busy_pct], ['User', cpu.capture?.user_pct], ['System', cpu.capture?.system_pct], ['IRQ', cpu.capture?.irq_pct], ['SoftIRQ', cpu.capture?.softirq_pct], ['Idle', cpu.capture?.idle_pct]] as const;
  const threads = sample.threads.filter(thread => thread.host_role === cpu.host_role && thread.cpu_id === cpu.cpu_id);
  const siblings = cpu.smt_sibling_cpu_ids.filter(id => id !== cpu.cpu_id);
  return <div className="to-cpu-selected"><div className="to-cpu-selected-heading"><strong>{hosts[cpu.host_role]} · CPU {cpu.cpu_id}</strong><span>NUMA {number(cpu.numa_node, 0)} · socket {number(cpu.socket_id, 0)} · core {number(cpu.core_id, 0)}</span><button aria-label="关闭 CPU 详情" onClick={onClose}>×</button></div><dl>{values.map(([label, value]) => <div key={label}><dt>{label}%</dt><dd>{number(value)}</dd></div>)}</dl><div className="to-cpu-detail-line"><span>SMT 兄弟</span>{!cpu.smt_sibling_cpu_ids.length ? '—' : !siblings.length ? '无同核兄弟' : siblings.map(id => { const sibling = rows.find(row => row.host_role === cpu.host_role && row.cpu_id === id); return <span key={id} className="vd-number">CPU {id} · busy {percent(sibling?.capture?.busy_pct)}</span>; })}</div><div className="to-cpu-detail-line"><span>线程端点</span>{threads.length ? threads.map(thread => <span key={[thread.pid, thread.tid, thread.starttime_ticks].join('/')} title={thread.process_role + ' / ' + thread.pool}>{thread.name || thread.pool} <span className="vd-number">{thread.pid}/{thread.tid}</span></span>) : '未采集到此 CPU 的线程端点'}</div>{cpu.capture?.competitors.length ? <div className="to-cpu-detail-line"><span>竞争者记录</span>{cpu.capture.competitors.join(' / ')}</div> : null}</div>;
}

function WindowLayers({ sample }: { sample: SampleWindow }) {
  return sample.layers.length ? <div className="vd-table-scroll"><table className="vd-table to-layer-table"><thead><tr><th>层 / 范围</th><th>时延 / μs</th><th>深度</th></tr></thead><tbody>{sample.layers.map(layer => <tr key={layer.layer + '/' + layer.scope}><td>{layer.layer}<small>{layer.scope}</small></td><td className="vd-number">{number(layer.latency_us, 2)}</td><td className="vd-number">{number(layer.queue_depth, 2)}</td></tr>)}</tbody></table></div> : <div className="to-no-data">层时延 / 深度待接入。</div>;
}
