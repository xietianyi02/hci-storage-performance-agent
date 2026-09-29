import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode, RefObject } from 'react';
import { api } from './api';
import { formatNumber } from './numberFormat';
import { Icon, date, statusLabels } from './ui';
import type { HostRole, VersionBatch, VersionCandidate, VersionCatalogBatch, VersionComparison, VersionComparisonSummary, VersionSnapshot, VersionVerdict } from './versionTypes';
import TimeObservation, { initialObservationSelection } from './TimeObservation';
import type { ThreadGrouping } from './TimeObservation';
import { groupIoThreads } from './ioThreadOrder';
import { groupProcessThreads } from './processThreadGroups';
import './version.css';

type View = 'overview' | 'threads' | 'cpu' | 'network' | 'layers';
const views:{id:View;title:string}[]=[{id:'overview',title:'概览'},{id:'threads',title:'线程'},{id:'cpu',title:'CPU / NUMA'},{id:'network',title:'网络'},{id:'layers',title:'时延 / 深度'}];
const hostLabels:Record<HostRole,string>={guest:'guest',local:'本地',remote:'远端'};
const verdictLabels:Record<string,string>={pending:'待验证',confirmed:'已确认',rejected:'已否定'};
const fmt=(value:number|null|undefined,digits=2)=>formatNumber(value,digits);
const list=(value:number[]|undefined)=>value?.length?value.join(', '):'—';
const isDeclined=(value:VersionComparisonSummary)=>value.deltas.iops_pct!==null&&value.deltas.iops_pct<0;
const pairPath=(batch:string,scenario:string)=>'/version/comparisons/'+encodeURIComponent(batch)+'/'+encodeURIComponent(scenario);
type RetryTarget = 'catalog' | 'summary' | 'detail';
type SnapshotSamples = Pick<VersionSnapshot, 'sample_windows' | 'cpu_inventory'>;
type ComparisonSamples = { id:string; current:SnapshotSamples; previous:SnapshotSamples|null };
function remember<T>(cache:Map<string,T>,key:string,value:T,limit:number) {
  cache.delete(key);cache.set(key,value);
  while(cache.size>limit)cache.delete(cache.keys().next().value!);
}
function samplesOf(item:VersionComparison):ComparisonSamples {
  return {id:item.id,current:{sample_windows:item.current.sample_windows,cpu_inventory:item.current.cpu_inventory},previous:item.previous?{sample_windows:item.previous.sample_windows,cpu_inventory:item.previous.cpu_inventory}:null};
}
function withoutSamples(item:VersionComparison):VersionComparison {
  const strip=(snapshot:VersionSnapshot):VersionSnapshot=>{const {sample_windows:_windows,cpu_inventory:_inventory,...summary}=snapshot;return summary;};
  return {...item,current:strip(item.current),previous:item.previous?strip(item.previous):null};
}
function summaryOf(item:VersionComparison):VersionComparisonSummary {
  const snapshot=(value:VersionSnapshot)=>({id:value.id,title:value.title,protocol:value.protocol,fio:value.fio,window:value.window,metrics:value.metrics});
  return {id:item.id,current_batch:item.current_batch,previous_batch:item.previous_batch,current:snapshot(item.current),previous:item.previous?snapshot(item.previous):null,deltas:item.deltas};
}

function Delta({value,lowerIsBetter=false}:{value:number|null|undefined;lowerIsBetter?:boolean}) {
  const worsened=value!=null&&(lowerIsBetter?value>0:value<0);
  return <span className={'vd-number '+(worsened?'vd-negative':'')}>{value==null?'—':(value>0?'+':'')+fmt(value,1)+'%'}</span>;
}
function Pair({before,after,unit='',digits=2}:{before:number|null|undefined;after:number|null|undefined;unit?:string;digits?:number}) {
  return <span className="vd-number vd-pair"><span>{fmt(before,digits)}</span><i>→</i><span>{fmt(after,digits)}{unit}</span></span>;
}
function TextPair({before,after}:{before:string;after:string}) {return <span className="vd-text-pair"><span>{before}</span><i>→</i><span>{after}</span></span>;}
function Table({children,className=''}:{children:ReactNode;className?:string}) {return <div className="vd-table-scroll"><table className={'vd-table '+className}>{children}</table></div>;}
function Disclosure({title,children}:{title:ReactNode;children:ReactNode}) {return <details className="vd-disclosure"><summary>{title}</summary><div className="vd-disclosure-content">{children}</div></details>;}

export default function VersionDiagnosis() {
  const [batches,setBatches]=useState<VersionCatalogBatch[]>([]),[batchId,setBatchId]=useState('');
  const [scenarioList,setScenarioList]=useState<{batchId:string;items:VersionComparisonSummary[]}>({batchId:'',items:[]}),[scenarioId,setScenarioId]=useState('');
  const [detail,setDetail]=useState<VersionComparison|null>(null),[loadedSamples,setLoadedSamples]=useState<ComparisonSamples|null>(null);
  const [sampleError,setSampleError]=useState<{id:string;message:string}|null>(null);
  const [view,setView]=useState<View>('overview'),[onlyDeclines,setOnlyDeclines]=useState(false);
  const [observationSelection,setObservationSelection]=useState(initialObservationSelection);
  const [threadGrouping,setThreadGrouping]=useState<ThreadGrouping>('process');
  const [loading,setLoading]=useState(true),[loadingPair,setLoadingPair]=useState(false),[loadingDetail,setLoadingDetail]=useState(false),[loadingSamples,setLoadingSamples]=useState(false),[busy,setBusy]=useState(false);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[importOpen,setImportOpen]=useState(false);
  const [retryTarget,setRetryTarget]=useState<RetryTarget>('catalog');
  const [summaryRetry,setSummaryRetry]=useState(0),[detailRetry,setDetailRetry]=useState(0),[samplesRetry,setSamplesRetry]=useState(0);
  const catalogRequest=useRef<AbortController|null>(null),mounted=useRef(false);
  // Full samples are independent of the lean analysis. Keep at most three pairs in this page.
  const sampleCache=useRef(new Map<string,ComparisonSamples>()),detailCache=useRef(new Map<string,VersionComparison>());
  const resultRef=useRef<HTMLDivElement>(null);
  const loadBatches=useCallback(async(preferred?:string)=>{
    catalogRequest.current?.abort();const request=new AbortController();catalogRequest.current=request;setLoading(true);setError('');
    try {const data=await api<VersionCatalogBatch[]>('/version/catalog',undefined,request.signal);if(request.signal.aborted)return;const sorted=[...data].sort((a,b)=>new Date(b.created_at).getTime()-new Date(a.created_at).getTime());setBatches(sorted);setBatchId(current=>sorted.some(value=>value.id===(preferred||current))?(preferred||current):sorted[0]?.id||'');}
    catch(reason){if(!request.signal.aborted){setError(reason instanceof Error?reason.message:'批次读取失败');setRetryTarget('catalog');}}finally{if(!request.signal.aborted)setLoading(false);}
  },[]);
  useEffect(()=>{mounted.current=true;void loadBatches();return()=>{mounted.current=false;catalogRequest.current?.abort();};},[loadBatches]);
  const batch=batches.find(value=>value.id===batchId);
  const comparisons=scenarioList.batchId===batchId?scenarioList.items:[];
  const selectedSummary=comparisons.find(value=>value.current.id===scenarioId);
  const selected=detail?.id===selectedSummary?.id?detail:null;
  useEffect(()=>{
    if(!batch)return;
    const request=new AbortController();setLoadingPair(true);setScenarioList({batchId:batch.id,items:[]});setScenarioId('');setDetail(null);setLoadedSamples(null);setView('overview');setError('');
    void api<VersionComparisonSummary[]>('/version/batches/'+encodeURIComponent(batch.id)+'/scenarios',undefined,request.signal).then(data=>{
      if(request.signal.aborted)return;setScenarioList({batchId:batch.id,items:data});
      const declined=[...data].filter(isDeclined).sort((a,b)=>(a.deltas.iops_pct??0)-(b.deltas.iops_pct??0));setScenarioId((declined[0]||data[0])?.current.id||'');
    }).catch(reason=>{if(!request.signal.aborted){setError(reason instanceof Error?reason.message:'场景读取失败');setRetryTarget('summary');}}).finally(()=>{if(!request.signal.aborted)setLoadingPair(false);});
    return()=>request.abort();
  },[batch,summaryRetry]);
  const selectedPairId=selectedSummary?.id;
  useEffect(()=>{
    if(!selectedPairId||!scenarioId){setDetail(null);setLoadingDetail(false);return;}
    const cached=detailCache.current.get(selectedPairId);
    if(cached){remember(detailCache.current,selectedPairId,cached,16);setDetail(cached);setLoadingDetail(false);return;}
    const request=new AbortController();setDetail(null);setLoadingDetail(true);setError('');
    void api<VersionComparison>(pairPath(batchId,scenarioId)+'?include_samples=false',undefined,request.signal).then(data=>{
      if(request.signal.aborted)return;const lean=withoutSamples(data);remember(detailCache.current,data.id,lean,16);setDetail(lean);
      // A newly imported previous round can change the comparison while this request is in flight.
      setScenarioList(current=>current.batchId===batchId?{...current,items:current.items.map(item=>item.current.id===scenarioId?summaryOf(data):item)}:current);
    }).catch(reason=>{if(!request.signal.aborted){setError(reason instanceof Error?reason.message:'场景详情读取失败');setRetryTarget('detail');}}).finally(()=>{if(!request.signal.aborted)setLoadingDetail(false);});
    return()=>request.abort();
  },[batchId,scenarioId,selectedPairId,detailRetry]);
  const needsSamples=view==='threads'||view==='cpu'||view==='layers';
  const detailPairId=selected?.id;
  useEffect(()=>{
    if(!needsSamples||!detailPairId){setLoadingSamples(false);return;}
    const cached=sampleCache.current.get(detailPairId);
    if(cached){remember(sampleCache.current,detailPairId,cached,3);setLoadedSamples(cached);setLoadingSamples(false);setSampleError(null);return;}
    const request=new AbortController();setLoadedSamples(null);setLoadingSamples(true);setSampleError(null);
    void api<VersionComparison>(pairPath(batchId,scenarioId),undefined,request.signal).then(data=>{
      if(request.signal.aborted)return;const samples=samplesOf(data);remember(sampleCache.current,data.id,samples,3);setLoadedSamples(samples);
      if(data.id!==detailPairId){const lean=withoutSamples(data);remember(detailCache.current,data.id,lean,16);setDetail(lean);setScenarioList(current=>current.batchId===batchId?{...current,items:current.items.map(item=>item.current.id===scenarioId?summaryOf(data):item)}:current);}
    }).catch(reason=>{if(!request.signal.aborted)setSampleError({id:detailPairId,message:reason instanceof Error?reason.message:'采样读取失败'});}).finally(()=>{if(!request.signal.aborted)setLoadingSamples(false);});
    return()=>request.abort();
  },[batchId,scenarioId,detailPairId,needsSamples,samplesRetry]);
  useEffect(()=>{if(!notice)return;const timer=window.setTimeout(()=>setNotice(''),4000);return()=>window.clearTimeout(timer);},[notice]);
  const observed=selected&&loadedSamples?.id===selected.id?{...selected,current:{...selected.current,...loadedSamples.current},previous:selected.previous&&loadedSamples.previous?{...selected.previous,...loadedSamples.previous}:selected.previous}:null;
  const visible=comparisons.filter(value=>!onlyDeclines||isDeclined(value));
  function selectScenario(id:string){if(busy)return;setScenarioId(id);setView('overview');setNotice('');setError('');}
  function retryRead(){setError('');if(retryTarget==='catalog')void loadBatches();else if(retryTarget==='summary')setSummaryRetry(value=>value+1);else {if(selectedPairId)detailCache.current.delete(selectedPairId);setDetailRetry(value=>value+1);}}
  function updateComparison(value:VersionComparison){const lean=withoutSamples(value);remember(detailCache.current,value.id,lean,16);setDetail(lean);setScenarioList(current=>current.batchId===value.current_batch.id?{...current,items:current.items.map(item=>item.current.id===value.current.id?summaryOf(value):item)}:current);}
  async function analyze(){
    if(!selected||busy)return;setBusy(true);setError('');setView('overview');
    try {const result=await api<VersionComparison>(pairPath(batchId,scenarioId)+'/analyze?include_samples=false',{});if(!mounted.current)return;updateComparison(result);setNotice(selected.analysis?'已读取保存的分析。':'分析已保存，候选待验证。');window.requestAnimationFrame(()=>resultRef.current?.scrollIntoView({behavior:'smooth',block:'nearest'}));}
    catch(reason){if(mounted.current){setError(reason instanceof Error?reason.message:'分析失败');setRetryTarget('detail');}}finally{if(mounted.current)setBusy(false);}
  }
  async function feedback(id:string,verdict:VersionVerdict,note:string){
    if(!selected?.analysis||busy||!note.trim())return;setBusy(true);setError('');
    try {const result=await api<VersionComparison>(pairPath(batchId,scenarioId)+'/feedback?include_samples=false',{analysis_id:selected.analysis.id,candidate_id:id,verdict,note,reviewer:'性能专家'});if(!mounted.current)return;updateComparison(result);setNotice('评价已保存。');}
    catch(reason){if(mounted.current){setError(reason instanceof Error?reason.message:'评价保存失败');setRetryTarget('detail');}}finally{if(mounted.current)setBusy(false);}
  }
  function tabKey(event:ReactKeyboardEvent<HTMLButtonElement>,index:number){
    if(busy)return;let next=index;
    if(event.key==='ArrowRight')next=(index+1)%views.length;else if(event.key==='ArrowLeft')next=(index+views.length-1)%views.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=views.length-1;else return;
    event.preventDefault();setView(views[next].id);document.getElementById('vd-tab-'+views[next].id)?.focus();
  }
  const sampleFailure=sampleError&&selected&&sampleError.id===selected.id?sampleError.message:'';
  const samplingPlaceholder=<div className="vd-empty" role={sampleFailure?'alert':'status'}>{sampleFailure?<><span>{sampleFailure}</span> <button className="button button-secondary" onClick={()=>setSamplesRetry(value=>value+1)}>重试读取采样</button></>:loadingSamples?'读取该场景的 3 s 采样…':'准备读取 3 s 采样…'}</div>;
  return <div className="vd-page">
    <header className="vd-header"><div><h1>版本内排查</h1>{batch&&<span className="vd-source">{batch.source==='mock'?'模拟数据':'导入实测'}</span>}</div><button className="button button-secondary" disabled={busy} onClick={()=>setImportOpen(true)}>导入批次</button></header>
    {error&&<div className="vd-message vd-error" role="alert"><span>{error}</span><button disabled={busy} onClick={retryRead}>重新读取</button><button aria-label="关闭错误" onClick={()=>setError('')}><Icon name="close" size={14}/></button></div>}
    {notice&&<div className="vd-message" role="status">{notice}</div>}
    {loading?<div className="vd-empty">读取批次…</div>:!batch?<div className="vd-empty">暂无测试批次，请导入 JSON。</div>:<>
      <div className="vd-batch-bar"><label>本轮 <select aria-label="选择测试批次" disabled={busy} value={batchId} onChange={event=>{setBatchId(event.target.value);setOnlyDeclines(false);}}>{batches.map(value=><option key={value.id} value={value.id}>{value.title} / {value.build} / {value.source==='mock'?'模拟':'实测'}</option>)}</select></label><span title={batch.environment.description}>{batch.environment.label}</span><span className="vd-batch-date">{date(batch.created_at)}</span></div>
      <section className="vd-scenarios"><div className="vd-section-heading"><h2>fio 场景</h2><div className="vd-filters"><button disabled={busy} aria-pressed={!onlyDeclines} className={!onlyDeclines?'active':''} onClick={()=>setOnlyDeclines(false)}>全部 {comparisons.length}</button><button disabled={busy} aria-pressed={onlyDeclines} className={onlyDeclines?'active':''} onClick={()=>setOnlyDeclines(true)}>仅下降 {comparisons.filter(isDeclined).length}</button></div></div>
        {loadingPair?<div className="vd-empty">配对上一轮…</div>:<Table className="vd-scene-table"><thead><tr><th>场景 / fio</th><th>上次 IOPS</th><th>本次 IOPS</th><th>Δ IOPS</th><th>Δ 延迟</th></tr></thead><tbody>{visible.map(value=><tr key={value.id} className={scenarioId===value.current.id?'vd-selected':''} onClick={()=>selectScenario(value.current.id)}><td><button disabled={busy} aria-pressed={scenarioId===value.current.id} onClick={()=>selectScenario(value.current.id)}><strong>{value.current.title}</strong><span>{value.current.protocol.toUpperCase()} / {value.current.fio.rw} / {value.current.fio.bs} / QD {value.current.fio.iodepth} × {value.current.fio.numjobs} / {value.current.fio.vm_count} VM</span></button></td><td className="vd-number">{fmt(value.previous?.metrics.iops,0)}</td><td className="vd-number">{fmt(value.current.metrics.iops,0)}</td><td><Delta value={value.deltas.iops_pct}/></td><td><Delta value={value.deltas.latency_pct} lowerIsBetter/></td></tr>)}</tbody></Table>}
        {!loadingPair&&visible.length===0&&<div className="vd-empty">{onlyDeclines?'没有 IOPS 下降场景。':'没有场景记录。'}</div>}
      </section>
      {loadingDetail&&<div className="vd-empty" role="status">读取所选场景概览…</div>}
      {selected&&<section className="vd-comparison"><div className="vd-comparison-heading">
        <h2>{selected.current.title}</h2>
        <button className="button button-primary vd-analyze-button" disabled={busy} onClick={()=>void analyze()}>{busy?'处理中…':selected.analysis?'查看分析':'分析差异'}</button>
        <div className="vd-build-pair" role="group" aria-label="测试版本对比">
          <div className="vd-build-chip"><span>上轮</span><code>{selected.previous_batch?.build||'首次记录'}</code></div>
          <Icon name="arrow" size={18}/>
          <div className="vd-build-chip vd-build-current"><span>本轮</span><code>{selected.current_batch.build}</code></div>
        </div>
      </div>
        <div className="vd-tabs" role="tablist" aria-label="版本差异详情">{views.map((tab,index)=><button role="tab" key={tab.id} id={'vd-tab-'+tab.id} aria-controls={'vd-panel-'+tab.id} aria-selected={view===tab.id} tabIndex={view===tab.id?0:-1} disabled={busy} onKeyDown={event=>tabKey(event,index)} onClick={()=>setView(tab.id)}>{tab.title}</button>)}</div>
        <div role="tabpanel" id="vd-panel-overview" aria-labelledby="vd-tab-overview" hidden={view!=='overview'}><Overview key={selected.id} item={selected} busy={busy} resultRef={resultRef} onFeedback={(id,verdict,note)=>void feedback(id,verdict,note)}/></div>
        <div role="tabpanel" id="vd-panel-threads" aria-labelledby="vd-tab-threads" hidden={view!=='threads'}>{view==='threads'&&(observed?<TimeObservation item={observed} mode="threads" selection={observationSelection} onSelectionChange={setObservationSelection} threadGrouping={threadGrouping} onThreadGroupingChange={setThreadGrouping}><ThreadSummary item={observed} grouping={threadGrouping}/></TimeObservation>:samplingPlaceholder)}</div>
        <div role="tabpanel" id="vd-panel-cpu" aria-labelledby="vd-tab-cpu" hidden={view!=='cpu'}>{view==='cpu'&&(observed?<TimeObservation item={observed} mode="cpu" selection={observationSelection} onSelectionChange={setObservationSelection}><details className="to-details to-summary"><summary>整轮 CPU / NUMA 汇总</summary><CpuNuma item={observed}/></details></TimeObservation>:samplingPlaceholder)}</div>
        <div role="tabpanel" id="vd-panel-network" aria-labelledby="vd-tab-network" hidden={view!=='network'}>{view==='network'&&<Network item={selected}/>}</div>
        <div role="tabpanel" id="vd-panel-layers" aria-labelledby="vd-tab-layers" hidden={view!=='layers'}>{view==='layers'&&(observed?<TimeObservation item={observed} mode="layers" selection={observationSelection} onSelectionChange={setObservationSelection}/>:samplingPlaceholder)}</div>
      </section>}
    </>}
    {importOpen&&<ImportBatch onClose={()=>setImportOpen(false)} onImported={value=>{setImportOpen(false);setOnlyDeclines(false);void loadBatches(value.id);setNotice('批次 '+value.id+' 已导入。');}}/>}
  </div>;
}
function Overview({item,busy,resultRef,onFeedback}:{item:VersionComparison;busy:boolean;resultRef:RefObject<HTMLDivElement|null>;onFeedback:(id:string,verdict:VersionVerdict,note:string)=>void}){
  const before=item.previous?.metrics,after=item.current.metrics;
  const rows=[{title:'IOPS',before:before?.iops,after:after.iops,delta:item.deltas.iops_pct,digits:0,lower:false},{title:'平均延迟 / ms',before:before?.latency_ms,after:after.latency_ms,delta:item.deltas.latency_pct,digits:2,lower:true},{title:'P99 / ms',before:before?.p99_ms,after:after.p99_ms,delta:item.deltas.p99_pct,digits:2,lower:true},{title:'带宽 / MiB/s',before:before?.bandwidth_mib_s,after:after.bandwidth_mib_s,delta:before&&before.bandwidth_mib_s>0?(after.bandwidth_mib_s/before.bandwidth_mib_s-1)*100:null,digits:1,lower:false}];
  const facts=observedChanges(item);
  return <div className="vd-overview">
    {!item.previous&&<p className="vd-first-record">首次记录：没有同来源、同环境、同 fio 的上一轮，不能判断退化。</p>}
    <div className="vd-overview-columns"><Table className="vd-metric-table"><thead><tr><th>fio 指标</th><th>上次</th><th>本次</th><th>变化</th></tr></thead><tbody>{rows.map(row=><tr key={row.title}><td>{row.title}</td><td className="vd-number">{fmt(row.before,row.digits)}</td><td className="vd-number">{fmt(row.after,row.digits)}</td><td><Delta value={row.delta} lowerIsBetter={row.lower}/></td></tr>)}</tbody></Table><div className="vd-observed"><h3>数据变化</h3>{facts.length?<ul>{facts.map((fact,index)=><li key={index}>{fact}</li>)}</ul>:<p>无可比较的额外变化，或采集数据不足。</p>}</div></div>
    <div ref={resultRef} className="vd-analysis"><div className="vd-section-heading"><h3>分析结果</h3>{item.analysis&&<ReviewStats candidates={item.analysis.candidates}/>}</div>{item.analysis?<><p className="vd-analysis-summary">{item.analysis.summary}</p>{item.analysis.candidates.map((candidate,index)=><Candidate key={item.analysis?.id+'-'+candidate.id} item={candidate} index={index} busy={busy} onFeedback={(verdict,note)=>onFeedback(candidate.id,verdict,note)}/>)}{!item.analysis.candidates.length&&<p className="vd-muted">未形成候选，请查看采集质量或补充上一轮。</p>}</>:<p className="vd-muted">未分析。点击“分析差异”生成候选与验证方案。</p>}</div>
    <div className="vd-supporting">
      <Disclosure title="fio 时间线"><div className="vd-chart-area"><TimeChart item={item} metric="iops" title="IOPS"/><TimeChart item={item} metric="latency_ms" title="平均延迟 / ms"/></div></Disclosure>
      <Disclosure title={'采集质量与口径'+(item.quality.length?' · '+item.quality.length+' 条':'')}><ul>{item.quality.map((value,index)=><li key={index}>{value}</li>)}</ul><p>窗口 / 秒：{fmt(item.previous?.window.duration_s,0)} → {fmt(item.current.window.duration_s,0)}；guest 完成 IO：{fmt(item.previous?.window.completed_ios,0)} → {fmt(item.current.window.completed_ios,0)}。</p><p>每 IO 成本使用同窗口 guest 完成数。CPU 合计可跨核超过 100%；IPC 使用线程池总计数；缺失为“—”。</p><p>计数范围或比例不一致时不直接比较；队列关联 CPU 的 IRQ / softirq 不代表队列独占耗时。</p></Disclosure>
      <Disclosure title={'变更背景 · '+item.current_batch.changes.length+' 条'}>{item.current_batch.changes.length?<ul>{item.current_batch.changes.map((value,index)=><li key={index}>{value}</li>)}</ul>:<p>未提供变更清单。</p>}<p className="vd-muted">变更仅为关联线索。</p></Disclosure>
      {item.analysis&&<Disclosure title={'分析记录 · '+item.analysis.framework+' · '+item.analysis.steps.length+' 步'}><Table><thead><tr><th>步骤</th><th>状态</th></tr></thead><tbody>{item.analysis.steps.map(step=><tr key={step.id}><td>{step.title}</td><td>{statusLabels[step.status]||step.status}</td></tr>)}</tbody></Table><div className="vd-event-list">{item.analysis.events.map((event,index)=><div key={index}><strong>{event.title}</strong><time>{date(event.timestamp)}</time><p>{event.detail}</p></div>)}</div></Disclosure>}
      <Disclosure title="完整 fio 参数与资料引用"><pre>{JSON.stringify({fio:item.current.fio,previous_artifacts:item.previous?.artifacts||[],current_artifacts:item.current.artifacts},null,2)}</pre><p className="vd-muted">只保存引用，不读取对应文件。</p></Disclosure>
    </div>
  </div>;
}
function observedChanges(item:VersionComparison):string[]{
  if(!item.previous)return [];const facts:string[]=[];
  const cost=[...item.threads].filter(row=>row.derived_before?.cpu_us_per_io!=null&&row.derived_after?.cpu_us_per_io!=null).sort((a,b)=>Math.abs((b.derived_after?.cpu_us_per_io||0)-(b.derived_before?.cpu_us_per_io||0))-Math.abs((a.derived_after?.cpu_us_per_io||0)-(a.derived_before?.cpu_us_per_io||0)))[0];
  if(cost&&cost.derived_before?.cpu_us_per_io!==cost.derived_after?.cpu_us_per_io){const capture=cost.after||cost.before!;facts.push(hostLabels[capture.host_role]+' '+capture.pool+' CPU/IO：'+fmt(cost.derived_before?.cpu_us_per_io)+' → '+fmt(cost.derived_after?.cpu_us_per_io)+' μs');}
  const drop=item.network.find(row=>row.before&&row.after&&row.before.drops!=null&&row.after.drops!=null&&row.before.drops!==row.after.drops);
  if(drop?.before&&drop.after)facts.push(hostLabels[drop.after.host_role]+' '+drop.after.interface+'/'+drop.after.queue+' 丢包：'+fmt(drop.before.drops,0)+' → '+fmt(drop.after.drops,0));
  const moved=item.threads.find(row=>row.before&&row.after&&row.before.cpu_ids.length&&row.after.cpu_ids.length&&list(row.before.cpu_ids)!==list(row.after.cpu_ids));
  if(moved?.before&&moved.after)facts.push(hostLabels[moved.after.host_role]+' '+moved.after.pool+' CPU：'+list(moved.before.cpu_ids)+' → '+list(moved.after.cpu_ids));
  if(facts.length<3){const numa=item.threads.find(row=>row.before&&row.after&&row.before.remote_access_pct!=null&&row.after.remote_access_pct!=null&&row.before.remote_access_pct!==row.after.remote_access_pct);if(numa?.before&&numa.after)facts.push(hostLabels[numa.after.host_role]+' '+numa.after.pool+' 跨 NUMA：'+fmt(numa.before.remote_access_pct,1)+' → '+fmt(numa.after.remote_access_pct,1)+'%');}
  return facts.slice(0,3);
}
function ThreadSummary({item,grouping}:{item:VersionComparison;grouping:ThreadGrouping}){
  const groups=grouping==='io' ? groupIoThreads(item.threads,row=>({...(row.after||row.before!),protocol:item.current.protocol}))
    : groupProcessThreads(item.threads,row=>row.after||row.before!).map(group=>({id:group.id,title:(hostLabels[group.host_role as HostRole]??group.host_role)+' · '+group.process_role,rows:group.rows}));
  return <div className="vd-evidence-tab"><Table>
    <thead><tr><th>主机 / 角色 / 池</th><th>CPU 合计 %</th><th>CPU/IO μs</th><th>IPC</th><th>指令/IO</th><th>cycles/IO</th><th>队列等待 ms</th><th>perf</th></tr></thead>
    {groups.map(group=><tbody key={group.id} aria-label={group.title}>
      <tr className="to-thread-group"><th colSpan={8}><strong>{group.title}</strong><span>{group.rows.length} 个线程池</span></th></tr>
      {group.rows.map(row=>{const c=row.after||row.before!;return <tr key={row.key}>
        <td><strong>{hostLabels[c.host_role]} / {c.process_role}</strong><small>{c.pool} / {c.branch||'共用'} / {fmt(row.before?.thread_count,0)} → {fmt(row.after?.thread_count,0)} 线程</small></td>
        <td><Pair before={row.derived_before?.cpu_pct} after={row.derived_after?.cpu_pct} digits={1}/></td>
        <td><Pair before={row.derived_before?.cpu_us_per_io} after={row.derived_after?.cpu_us_per_io}/></td>
        <td><Pair before={row.derived_before?.ipc} after={row.derived_after?.ipc}/></td>
        <td><Pair before={row.derived_before?.instructions_per_io} after={row.derived_after?.instructions_per_io} digits={0}/></td>
        <td><Pair before={row.derived_before?.cycles_per_io} after={row.derived_after?.cycles_per_io} digits={0}/></td>
        <td><Pair before={row.before?.runqueue_wait_ms} after={row.after?.runqueue_wait_ms}/></td>
        <td><details className="vd-perf"><summary>计数 / 热点</summary><div><strong>上次</strong><PerfCapture capture={row.before}/><strong>本次</strong><PerfCapture capture={row.after}/></div></details></td>
      </tr>;})}
    </tbody>)}
  </Table>{!item.threads.length&&<p className="vd-empty">未采集线程数据。</p>}<p className="vd-table-footnote">{grouping==='process'?'整轮按进程角色汇总，未分 PID':'按 IO 流分组'}；上次 → 本次。CPU 合计可跨核，计数范围见 perf。</p></div>;
}
function CpuNuma({item}:{item:VersionComparison}){
  return <div className="vd-evidence-tab"><h3 className="vd-table-title">线程位置</h3><Table><thead><tr><th>主机 / 池</th><th>实际 CPU</th><th>允许 CPU</th><th>CPU NUMA</th><th>内存 NUMA</th><th>跨 NUMA %</th></tr></thead><tbody>{item.threads.map(row=>{const c=row.after||row.before!;return <tr key={row.key}><td>{hostLabels[c.host_role]} / {c.process_role}<small>{c.pool}</small></td><td><TextPair before={list(row.before?.cpu_ids)} after={list(row.after?.cpu_ids)}/></td><td><TextPair before={list(row.before?.allowed_cpu_ids)} after={list(row.after?.allowed_cpu_ids)}/></td><td><TextPair before={list(row.before?.numa_nodes)} after={list(row.after?.numa_nodes)}/></td><td><TextPair before={list(row.before?.memory_numa_nodes)} after={list(row.after?.memory_numa_nodes)}/></td><td><Pair before={row.before?.remote_access_pct} after={row.after?.remote_access_pct} digits={1}/></td></tr>;})}</tbody></Table><h3 className="vd-table-title">逻辑 CPU 负载</h3><Table><thead><tr><th>主机 / CPU</th><th>NUMA</th><th>User %</th><th>System %</th><th>IRQ %</th><th>SoftIRQ %</th><th>Idle %</th><th>竞争者</th></tr></thead><tbody>{item.cpus.map(row=>{const c=row.after||row.before!;return <tr key={row.key}><td>{hostLabels[c.host_role]} / {c.cpu_id}</td><td><TextPair before={row.before?.numa_node==null?'未知':String(row.before.numa_node)} after={row.after?.numa_node==null?'未知':String(row.after.numa_node)}/></td><td><Pair before={row.before?.user_pct} after={row.after?.user_pct} digits={1}/></td><td><Pair before={row.before?.system_pct} after={row.after?.system_pct} digits={1}/></td><td><Pair before={row.before?.irq_pct} after={row.after?.irq_pct} digits={1}/></td><td><Pair before={row.before?.softirq_pct} after={row.after?.softirq_pct} digits={1}/></td><td><Pair before={row.before?.idle_pct} after={row.after?.idle_pct} digits={1}/></td><td><TextPair before={row.before?.competitors.join(' / ')||'—'} after={row.after?.competitors.join(' / ')||'—'}/></td></tr>;})}</tbody></Table>{!item.cpus.length&&<p className="vd-empty">未采集逐 CPU 数据。</p>}<p className="vd-table-footnote">上次 → 本次。逻辑 CPU 百分比与线程池合计分别计算。</p></div>;
}
function Network({item}:{item:VersionComparison}){
  const roles=(['local','remote','guest'] as const).filter(role=>item.network.some(row=>(row.after||row.before)?.host_role===role));
  return <div className="vd-evidence-tab">{roles.map(role=><section key={role}><h3 className="vd-table-title">{hostLabels[role]}网卡</h3><Table><thead><tr><th>网卡 / 队列</th><th>IRQ / CPU</th><th>IRQ/s</th><th>包/s</th><th>丢包</th><th>重传</th><th>关联 CPU IRQ / softirq</th></tr></thead><tbody>{item.network.filter(row=>(row.after||row.before)?.host_role===role).map(row=>{const c=row.after||row.before!;return <tr key={row.key}><td>{c.interface}/{c.queue}<small>NUMA {c.numa_node??'未知'}</small></td><td><TextPair before={row.before?(row.before.irq_id??'—')+' / '+list(row.before.cpu_ids):'—'} after={row.after?(row.after.irq_id??'—')+' / '+list(row.after.cpu_ids):'—'}/><small>允许 {list(row.before?.allowed_cpu_ids)} → {list(row.after?.allowed_cpu_ids)}</small></td><td><Pair before={row.before?.irq_per_s} after={row.after?.irq_per_s} digits={0}/></td><td><Pair before={row.before?.packets_per_s} after={row.after?.packets_per_s} digits={0}/></td><td><Pair before={row.before?.drops} after={row.after?.drops} digits={0}/></td><td><Pair before={row.before?.retransmits} after={row.after?.retransmits} digits={0}/></td><td><RelatedCpu item={item} row={row}/></td></tr>;})}</tbody></Table></section>)}{!roles.length&&<p className="vd-empty">未采集网卡数据。</p>}<p className="vd-table-footnote">关联 CPU 的 IRQ / softirq 为 CPU 总负载，不代表队列独占耗时。</p></div>;
}
function RelatedCpu({item,row}:{item:VersionComparison;row:VersionComparison['network'][number]}){
  const role=(row.after||row.before)?.host_role,ids=new Set([...(row.before?.cpu_ids||[]),...(row.after?.cpu_ids||[])]);
  const cpus=item.cpus.filter(value=>{const c=value.after||value.before;return !!c&&c.host_role===role&&ids.has(c.cpu_id);});
  return <div className="vd-related-cpu">{cpus.length?cpus.map(value=>{const c=value.after||value.before!;return <div key={value.key}>CPU {c.cpu_id}<small>IRQ {fmt(value.before?.irq_pct,1)} → {fmt(value.after?.irq_pct,1)}% / softirq {fmt(value.before?.softirq_pct,1)} → {fmt(value.after?.softirq_pct,1)}%</small></div>;}):'—'}</div>;
}
function ReviewStats({candidates}:{candidates:VersionCandidate[]}){
  const c=candidates.filter(value=>value.verdict==='confirmed').length,r=candidates.filter(value=>value.verdict==='rejected').length,p=candidates.filter(value=>value.verdict==='pending').length;
  return <span className="vd-review-stats" title="确认比例=已确认/(已确认+已否定)，排除待验证">确认 {c} / 否定 {r} / 待验证 {p}<span> · {c+r?fmt(c/(c+r)*100,0)+'%':'—'} ({c}/{c+r})</span></span>;
}
function Candidate({item,index,busy,onFeedback}:{item:VersionCandidate;index:number;busy:boolean;onFeedback:(verdict:VersionVerdict,note:string)=>void}){
  const [verdict,setVerdict]=useState(item.verdict),[note,setNote]=useState(item.note);
  useEffect(()=>{setVerdict(item.verdict);setNote(item.note);},[item.verdict,item.note]);
  return <details className="vd-candidate"><summary><span>{index+1}.</span><strong>{item.title}</strong><span className="vd-priority">{item.priority==='high'?'优先':'补充'}</span><span className="vd-verdict">{verdictLabels[item.verdict]}</span></summary><div className="vd-candidate-content"><h4>证据</h4><ul>{item.evidence.map((value,i)=><li key={i}>{value}</li>)}</ul><div className="vd-candidate-columns"><div><h4>缺口</h4>{item.missing.length?<ul>{item.missing.map((value,i)=><li key={i}>{value}</li>)}</ul>:<p>需实验确认因果。</p>}</div><div><h4>验证方案</h4>{item.validation.length?<ol>{item.validation.map((value,i)=><li key={i}>{value}</li>)}</ol>:<p>尚无方案。</p>}</div></div><div className="vd-feedback"><div className="vd-feedback-controls"><span>评价</span>{(['pending','confirmed','rejected'] as const).map(value=><button key={value} disabled={busy} aria-pressed={verdict===value} className={verdict===value?'active':''} onClick={()=>setVerdict(value)}>{verdictLabels[value]}</button>)}</div><textarea disabled={busy} aria-label={'候选 '+(index+1)+' 的专家评价备注'} value={note} onChange={event=>setNote(event.target.value)} rows={2} placeholder="验证证据 / 否定理由（必填）"/><div className="vd-feedback-bottom">{item.review_history.length?<details className="vd-review-history"><summary>历次评价 ({item.review_history.length})</summary>{item.review_history.map((value,i)=><div key={i}><strong>{verdictLabels[value.verdict]||value.verdict}</strong> · {value.reviewer} · {date(value.timestamp)}<p>{value.note||'无备注'}</p></div>)}</details>:<span/>}<button className="button button-secondary" disabled={busy||!note.trim()} onClick={()=>onFeedback(verdict,note)}>{busy?'保存中…':'保存评价'}</button></div></div></div></details>;
}
function TimeChart({item,metric,title}:{item:VersionComparison;metric:'iops'|'latency_ms';title:string}) {
  const before=item.previous?.timeline||[],after=item.current.timeline;
  const all=[...before,...after];
  const maxValue=Math.max(...all.map(value=>value[metric]),1)*1.12;
  const maxTime=Math.max(...all.map(value=>value.offset_s),1);
  const points=(data:typeof after)=>data.map(value=>`${45+value.offset_s/maxTime*405},${132-value[metric]/maxValue*108}`).join(' ');
  return <div className="vd-chart"><div className="vd-chart-heading"><strong>{title}</strong><div className="vd-chart-legend"><span><i/>上次</span><span><i/>本次</span></div></div>{all.length?<svg viewBox="0 0 465 158" role="img" aria-label={`${title}，按各轮采样窗口的相对秒数对齐`}><title>{title} · 上次与本次对比</title>{[0,.5,1].map(ratio=><g key={ratio}><line x1="45" x2="450" y1={132-ratio*108} y2={132-ratio*108} stroke="#e9eff2" strokeDasharray="3 3"/><text x="36" y={135-ratio*108} textAnchor="end" fontSize="10" fill="#a6b9c4">{metric==='iops'?`${fmt(maxValue*ratio/1000,0)}k`:fmt(maxValue*ratio,1)}</text></g>)}<line x1="45" x2="450" y1="132" y2="132" stroke="#dce7ec"/>{before.length>0&&<polyline fill="none" stroke="#b4c1ce" strokeWidth="2" points={points(before)}/>}<polyline fill="none" stroke="#416a9c" strokeWidth="2.2" points={points(after)}/>{[0,.25,.5,.75,1].map(ratio=><text key={ratio} x={45+ratio*405} y="149" textAnchor="middle" fontSize="10" fill="#a9bac4">{fmt(ratio*maxTime,0)}s</text>)}</svg>:<div className="vd-empty">未导入时间线采样。</div>}<div className="vd-chart-foot"><span>相对时间对齐，不补齐缺失采样点</span><span>{item.source==='mock'?'模拟曲线':'导入曲线'}</span></div></div>;
}
function PerfCapture({capture}:{capture:VersionComparison['threads'][number]['before']}) {return capture?<><p>CPU 时间 {fmt(capture.cpu_time_ms)} ms · 指令 {fmt(capture.instructions,0)} · cycles {fmt(capture.cycles,0)}</p><p>计数范围：{capture.counter_scope||'未采集'}；计数比例：{capture.counting_ratio===null?'—':`${fmt(capture.counting_ratio*100,1)}%`}</p><p>切换 {fmt(capture.context_switches,0)} · 迁移 {fmt(capture.migrations,0)}</p>{capture.top_functions.length?<ul>{capture.top_functions.map(value=><li key={value.name}>{value.name} · {fmt(value.samples_pct,1)}%</li>)}</ul>:<p>未采集 perf 函数热点。</p>}</>:<p>未采集该轮数据。</p>;}

function ImportBatch({onClose,onImported}:{onClose:()=>void;onImported:(value:VersionBatch)=>void}) {
  const [payload,setPayload]=useState('');
  const [example,setExample]=useState<VersionBatch|null>(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [busy,setBusy]=useState(false);
  const panel=useRef<HTMLDivElement>(null);
  const file=useRef<HTMLInputElement>(null);
  const latest=useRef({onClose,busy});latest.current={onClose,busy};
  useEffect(()=>{const previousFocus=document.activeElement as HTMLElement|null;const previousOverflow=document.body.style.overflow;document.body.style.overflow='hidden';panel.current?.querySelector<HTMLButtonElement>('button')?.focus();const handler=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!latest.current.busy)latest.current.onClose();if(event.key==='Tab'&&panel.current){const elements=Array.from(panel.current.querySelectorAll<HTMLElement>('button:not([disabled]),textarea'));const first=elements[0],last=elements[elements.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}};document.addEventListener('keydown',handler);return()=>{document.body.style.overflow=previousOverflow;document.removeEventListener('keydown',handler);previousFocus?.focus();};},[]);
  async function loadExample() {setLoading(true);setError('');try{const value=await api<VersionBatch>('/version/import-example');setExample(value);setPayload(JSON.stringify(value,null,2));}catch(reason){setError(reason instanceof Error?reason.message:'示例读取失败');}finally{setLoading(false);}}
  async function readFile(value:File|undefined) {if(!value)return;setError('');try{setPayload(await value.text());}catch{setError('读取文件失败，请确认是 UTF-8 JSON 文件。');}}
  async function submit() {setError('');let value:unknown;try{value=JSON.parse(payload);}catch{setError('JSON 格式错误。请检查引号、逗号以及括号是否完整。');return;}setBusy(true);try{const response=await fetch('/api/version/batches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});const result=await response.json();if(!response.ok){if(Array.isArray(result.detail))throw new Error(result.detail.map((item:{loc?:unknown[];msg?:string})=>`${(item.loc||[]).join('.')}：${item.msg||'格式不正确'}`).join('\n'));throw new Error(typeof result.detail==='string'?result.detail:`批次导入失败 (${response.status})`);}onImported(result as VersionBatch);}catch(reason){setError(reason instanceof Error?reason.message:'批次保存失败');}finally{setBusy(false);}}
  function downloadExample() {if(!example)return;const url=URL.createObjectURL(new Blob([JSON.stringify(example,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`${example.id}-schema-example.json`;document.body.appendChild(link);link.click();link.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000);}
  return <div className="modal-backdrop" onClick={event=>{if(event.target===event.currentTarget&&!busy)onClose();}}><div ref={panel} className="modal vd-import-modal" role="dialog" aria-modal="true" aria-labelledby="version-import-title"><div className="modal-header"><div><h2 id="version-import-title">导入版本采集批次</h2></div><button className="icon-button" aria-label="关闭导入批次窗口" disabled={busy} onClick={onClose}><Icon name="close"/></button></div><div className="modal-body"><div className="vd-import-tools"><div><input hidden tabIndex={-1} ref={file} type="file" accept=".json,application/json" onChange={event=>void readFile(event.target.files?.[0])}/><button className="button button-secondary" disabled={busy} onClick={()=>file.current?.click()}><Icon name="file" size={14}/>选择 JSON 文件</button><button className="text-button" disabled={busy||loading} onClick={()=>void loadExample()}>{loading?'读取中…':'载入规范示例'}</button></div>{example&&<button className="text-button" onClick={downloadExample}><Icon name="download" size={14}/>下载示例 JSON</button>}</div><textarea className="vd-json-input" disabled={busy} aria-label="采集批次 JSON 内容" placeholder="在此粘贴规范批次 JSON，或载入示例查看完整字段…" value={payload} onChange={event=>setPayload(event.target.value)} spellCheck={false}/><p className="vd-import-help">示例 source 为 mock；真实采集记录使用 measured，两类来源不会互相配对。缺失计数填写 null。environment.id 的档案保持固定，批次 id 必须唯一。</p>{error&&<div className="vd-import-error" role="alert">{error}</div>}</div><div className="modal-footer"><span>结构错误会指出字段；原始 perf 路径仅作为引用保存。</span><div><button className="button button-secondary" onClick={onClose} disabled={busy}>取消</button><button className="button button-primary" disabled={busy||loading||!payload.trim()} onClick={()=>void submit()}><Icon name="plus" size={15}/>{busy?'保存中…':'校验并导入'}</button></div></div></div></div>;
}
