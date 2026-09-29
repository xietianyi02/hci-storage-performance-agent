import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { Badge, Empty, Icon, Status, date, number, workflowIcons, workflowLabels } from './ui';
import type { Case, Knowledge, Overview, Page, Tool, Workflow, WorkflowId } from './types';
import CaseDetail from './CaseDetail';
import NewCase from './NewCase';
import { KnowledgePage, ToolsPage, WorkflowsPage } from './RegistryPages';
import VersionDiagnosis from './VersionDiagnosis';
import PocTuning from './PocTuning';
import './navigation.css';
import './technical-shell.css';

const nav: {id:Page; title:string; icon:string}[] = [
  {id:'overview',title:'概览',icon:'grid'},
  {id:'version',title:'版本内排查',icon:'activity'},
  {id:'poc',title:'POC 调优',icon:'server'},
  {id:'cases',title:'案例记录',icon:'cases'},
  {id:'knowledge',title:'知识库',icon:'book'}, {id:'workflows',title:'工作流',icon:'workflow'}, {id:'tools',title:'工具',icon:'tool'},
];
function currentRoute() { const [page,id] = window.location.hash.slice(1).split('/'); return {page:nav.some(item=>item.id===page)?page as Page:'version' as Page,id:id || null}; }
export default function App() {
  const [route,setRoute] = useState(currentRoute);
  const [cases,setCases] = useState<Case[]>([]);
  const [overview,setOverview] = useState<Overview>({});
  const [workflows,setWorkflows] = useState<Workflow[]>([]);
  const [knowledge,setKnowledge] = useState<Knowledge[]>([]);
  const [tools,setTools] = useState<Tool[]>([]);
  const [selected,setSelected] = useState<Case|null>(null);
  const [loading,setLoading] = useState(true);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [toast,setToast] = useState('');
  const [newCase,setNewCase] = useState<WorkflowId | null>(null);
  const [menuOpen,setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const workbenchLoadedRef = useRef(false);
  const [filter,setFilter] = useState('all');
  const [query,setQuery] = useState('');

  const load = useCallback(async () => {
    const results = await Promise.allSettled([api<Case[]>('/cases'),api<Overview>('/overview'),api<Workflow[]>('/workflows'),api<Knowledge[]>('/knowledge'),api<Tool[]>('/tools')]);
    const setters = [setCases,setOverview,setWorkflows,setKnowledge,setTools];
    results.forEach((result,index)=>{ if(result.status==='fulfilled') (setters[index] as (value:unknown)=>void)(result.value); });
    const failure = results.find(result=>result.status==='rejected');
    if (failure?.status === 'rejected') setError(failure.reason instanceof Error ? failure.reason.message : '无法连接 Agent 服务，请确认后端已启动。');
    setLoading(false);
  },[]);
  useEffect(()=>{const handler=()=>{setRoute(currentRoute());setMenuOpen(false);}; window.addEventListener('hashchange',handler); return ()=>window.removeEventListener('hashchange',handler);},[]);
  useEffect(()=>{if(route.page==='version'||route.page==='poc'||workbenchLoadedRef.current)return;workbenchLoadedRef.current=true;void load();},[load,route.page]);
  useEffect(()=>{window.scrollTo({top:0});},[route.page,route.id]);
  useEffect(()=>{if(!menuOpen)return; const frame=window.requestAnimationFrame(()=>sidebarRef.current?.querySelector<HTMLAnchorElement>('a[aria-current="page"]')?.focus());return()=>window.cancelAnimationFrame(frame);},[menuOpen]);
  useEffect(()=>{if(!route.id){setSelected(null);return;} let cancelled=false; api<Case>(`/cases/${route.id}`).then(value=>{if(!cancelled)setSelected(value);}).catch(reason=>setError(reason.message));return()=>{cancelled=true;};},[route.id]);
  useEffect(()=>{if(!toast)return;const timeout=window.setTimeout(()=>setToast(''),4500);return()=>window.clearTimeout(timeout);},[toast]);
  function navigate(page:Page,id?:string) {window.location.hash=id?`${page}/${id}`:page; if(!id)setSelected(null);setMenuOpen(false);setQuery('');setFilter('all');}
  function closeMenu() {setMenuOpen(false);menuButtonRef.current?.focus();}
  async function mutate(path:string,body:unknown,message:string) {setBusy(true);setError('');try {const updated=await api<Case>(path,body);setSelected(updated);await load();setToast(message);return updated;} catch(reason) {setError(reason instanceof Error?reason.message:'操作失败');return null;} finally {setBusy(false);}}
  async function create(body:unknown) {const value=await mutate('/cases',body,'案例已创建。');if(value){setNewCase(null);navigate('cases',value.id);}}
  const total = overview.total_cases ?? cases.length;
  const waiting = overview.awaiting_approval ?? cases.filter(item=>item.status==='awaiting_approval').length;
  const completed = overview.completed_cases ?? cases.filter(item=>item.status==='completed').length;
  const allRecommendations = cases.flatMap(item=>item.recommendations || []);
  const rec = overview.recommendations || {total:allRecommendations.length,confirmed:allRecommendations.filter(item=>item.verdict==='confirmed').length,rejected:allRecommendations.filter(item=>item.verdict==='rejected').length,pending:allRecommendations.filter(item=>item.verdict==='pending').length,accuracy_pct:null};
  const reviewed = rec.confirmed + rec.rejected;
  const confirmedRate = reviewed ? Math.round(rec.confirmed / reviewed * 100) : null;
  const title = selected && route.page==='cases' ? '案例记录 / 案例详情' : nav.find(item=>item.id===route.page)?.title;
  const filteredCases = cases.filter(item=>(filter==='all'||item.workflow_id===filter)&&(query===''||`${item.title} ${item.id} ${item.protocol}`.toLowerCase().includes(query.toLowerCase())));
  const isTechnical = route.page === 'version' || route.page === 'poc';
  const navLink = (item:typeof nav[number]) => <a key={item.id} href={`#${item.id}`} onClick={event=>{setMenuOpen(false);setQuery('');setFilter('all');event.currentTarget.closest('details')?.removeAttribute('open');}} className={`nav-item ${route.page===item.id?'selected':''}`} aria-current={route.page===item.id?'page':undefined}><Icon name={item.icon}/><span>{item.title}</span></a>;
  const navigation = <>{nav.slice(0,4).map(navLink)}<div className="navigation-divider" role="separator"/>{nav.slice(4).map(navLink)}</>;

  return <div className={`app-shell ${isTechnical?'version-shell':''}`}>
    {!isTechnical&&menuOpen&&<button aria-label="关闭导航遮罩" className="sidebar-backdrop" onClick={closeMenu}/>}
    {!isTechnical&&<aside id="workspace-navigation" ref={sidebarRef} className={`sidebar ${menuOpen?'sidebar-open':''}`} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();closeMenu();}}}>
      <div className="sidebar-heading"><a className="brand" href="#overview" onClick={()=>setMenuOpen(false)}><span className="brand-symbol"><Icon name="activity" size={20}/></span><div>HCI<span>性能工作台</span></div></a><button className="icon-button sidebar-close" aria-label="关闭导航" onClick={closeMenu}><Icon name="close" size={18}/></button></div>
      <nav aria-label="主导航">{navigation}</nav>
    </aside>}
    <div className="main-shell">
      {isTechnical?<header className="technical-topbar"><a href="#version">HCI 性能工作台</a><details className="technical-navigation" onKeyDown={event=>{if(event.key==='Escape'){event.currentTarget.removeAttribute('open');event.currentTarget.querySelector('summary')?.focus();}}}><summary>页面</summary><nav aria-label="页面导航">{navigation}</nav></details></header>:<header className="topbar"><div className="topbar-title"><button ref={menuButtonRef} className="icon-button mobile-menu" onClick={()=>setMenuOpen(true)} aria-label="打开菜单" aria-expanded={menuOpen} aria-controls="workspace-navigation"><Icon name="menu"/></button><span>{title}</span></div><div className="topbar-right"><span className="demo-indicator"><span/>演示环境</span></div></header>}
      <main className="main-content">
        {!isTechnical&&error&&<div role="alert" className="alert alert-error"><Icon name="info"/><span>{error}。如连接失败，请确认 API 服务运行于 127.0.0.1:8000。</span><button className="text-button" onClick={()=>{setError('');void load();}}>重新连接</button><button className="icon-button" onClick={()=>setError('')} aria-label="关闭错误"><Icon name="close" size={16}/></button></div>}
        {loading&&!isTechnical?<div className="loading"><span className="spinner"/><p>正在连接性能专家工作台…</p></div>:<>
        {route.page==='version'&&<VersionDiagnosis/>}
        {route.page==='poc'&&<PocTuning/>}
        {route.page==='overview'&&<>
          <div className="page-heading"><h1>概览</h1><button className="button button-primary" onClick={()=>setNewCase('version')}><Icon name="plus" size={18}/>新建诊断案例</button></div>
          <div className="demo-note"><Icon name="info" size={17}/><span>指标、诊断与实验结果为模拟数据；IO 架构来自产品文档。</span><Badge tone="teal">模拟数据</Badge></div>
          <div className="stats-grid"><Stat label="累计案例" value={total} suffix="个案例" icon="cases" detail={`${overview.total_runs ?? 0} 次运行记录`}/><Stat label="已完成" value={completed} suffix="个案例" icon="check"/><Stat label="等待实验确认" value={waiting} suffix="项" icon="clock" detail="开始实验前由专家确认" tone="amber"/><Stat label="建议已确认比例" value={confirmedRate===null?'—':`${confirmedRate}%`} icon="spark" detail={`已确认 ${rec.confirmed} / 已评审 ${reviewed} 条建议`} tone="teal"/></div>
          <section className="workflow-hero"><div className="section-heading"><h2>场景入口</h2><button className="text-button" onClick={()=>navigate('workflows')}>查看工作流 <Icon name="arrow" size={16}/></button></div><div className="track-grid">{(['version','poc','research'] as WorkflowId[]).map(id=><button className={`track-card track-${id}`} key={id} onClick={()=>id==='research'?setNewCase(id):navigate(id)}><div className="track-icon"><Icon name={workflowIcons[id]} size={24}/></div><h3>{id==='version'?'版本内排查':workflowLabels[id]}</h3><span className="track-action">{id==='version'?'打开排查':id==='poc'?'打开调优':'新建案例'}<Icon name="arrow" size={17}/></span></button>)}</div></section>
          <div className="dashboard-bottom"><section className="panel recent-panel"><div className="section-heading"><h2>最近案例</h2><button className="text-button" onClick={()=>navigate('cases')}>全部案例<Icon name="arrow" size={16}/></button></div><CaseRows cases={(overview.recent_cases||cases).slice(0,4)} onSelect={item=>navigate('cases',item.id)} compact/>{cases.length===0&&<Empty title="暂无案例" detail="点击“新建诊断案例”开始。"/>}</section><section className="panel feedback-panel"><div className="section-heading"><h2>建议评价</h2><Icon name="spark" size={20}/></div><div className="feedback-chart"><div className="donut" style={{background:rec.total?`conic-gradient(#168d82 0 ${rec.confirmed/rec.total*360}deg, #c86d64 ${rec.confirmed/rec.total*360}deg ${(rec.confirmed+rec.rejected)/rec.total*360}deg, #e8edf0 ${(rec.confirmed+rec.rejected)/rec.total*360}deg 360deg)`:'#e8edf0'}}><div><strong>{rec.total}</strong><span>累计建议</span></div></div><div className="chart-legend">{[['green','已确认',rec.confirmed],['red','已否定',rec.rejected],['neutral','待验证',rec.pending]].map(([tone,label,value])=><div key={label}><span className={`legend-dot dot-${tone}`}/><span>{label}</span><strong>{value}</strong></div>)}</div></div><div className="feedback-footnote"><Icon name="info" size={14}/><span>已确认比例只计算已评审建议；演示统计不代表真实诊断准确率。</span></div></section></div>
        </>}
        {route.page==='cases'&&(route.id?(selected?<CaseDetail item={selected} busy={busy} onBack={()=>navigate('cases')} onRun={()=>void mutate(`/cases/${selected.id}/run`,{},'分析完成。请检查实验方案并确认后继续。')} onApprove={(approved,note)=>void mutate(`/cases/${selected.id}/approve`,{approved,note},approved?'模拟实验已完成，结果和报告已记录。':'已取消模拟实验。')} onFeedback={(recommendation_id,verdict,note)=>void mutate(`/cases/${selected.id}/feedback`,{recommendation_id,verdict,note},'专家反馈已保存，统计已更新。')}/>:<div className="loading"><span className="spinner"/><p>正在加载案例记录…</p></div>):<>
          <div className="page-heading"><h1>案例记录</h1><button className="button button-primary" onClick={()=>setNewCase('version')}><Icon name="plus" size={18}/>新建诊断案例</button></div>
          <section className="panel cases-panel"><div className="case-filters"><div className="segmented">{['all','version','poc','research'].map(id=><button className={filter===id?'active':''} key={id} onClick={()=>setFilter(id)}>{id==='all'?'全部案例':workflowLabels[id]}<span>{id==='all'?cases.length:cases.filter(item=>item.workflow_id===id).length}</span></button>)}</div><label className="search-field"><Icon name="search" size={17}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="搜索案例名称、编号或协议" aria-label="搜索案例"/></label></div><CaseRows cases={filteredCases} onSelect={item=>navigate('cases',item.id)}/>{filteredCases.length===0&&<Empty title="暂无符合条件的案例" detail="创建案例，或调整筛选条件。"/>}</section>
        </>)}
        {route.page==='knowledge'&&<KnowledgePage items={knowledge}/>}
        {route.page==='workflows'&&<WorkflowsPage items={workflows} onCreate={id=>id==='poc'?navigate('poc'):setNewCase(id)}/>}
        {route.page==='tools'&&<ToolsPage items={tools}/>}
        </>}
      </main>
    </div>
    {newCase&&<NewCase initialWorkflow={newCase} busy={busy} error={error} onClose={()=>setNewCase(null)} onCreate={body=>void create(body)}/>}
    {toast&&<div role="status" className="toast"><Icon name="check" size={18}/>{toast}</div>}
  </div>;
}
function Stat({label,value,suffix,icon,detail,tone='blue'}:{label:string;value:string|number;suffix?:string;icon:string;detail?:string;tone?:string}) {return <div className="stat-card"><div className="stat-label">{label}<span className={`stat-icon stat-icon-${tone}`}><Icon name={icon} size={18}/></span></div><div className="stat-value">{typeof value==='number'?number(value):value}<span>{suffix}</span></div>{detail&&<div className="stat-detail">{detail}</div>}</div>;}
function CaseRows({cases,onSelect,compact=false}:{cases:Case[];onSelect:(item:Case)=>void;compact?:boolean}) {return <div className={`case-table ${compact?'compact':''}`}>{!compact&&<div className="case-table-head"><span>案例 / 场景</span><span>协议与测试模型</span><span>工作流状态</span><span>更新 / 操作</span></div>}{cases.map(item=><button className="case-row" key={item.id} onClick={()=>onSelect(item)}><div className="case-row-name"><span className={`case-icon case-icon-${item.workflow_id}`}><Icon name={workflowIcons[item.workflow_id]} size={19}/></span><div><strong>{item.title}</strong><small>{item.id} <span>·</span> {workflowLabels[item.workflow_id]}</small></div></div>{!compact&&<div className="case-model"><Badge>{item.protocol.toUpperCase()}</Badge><small>{item.scenario?.fio?.rw || item.fio?.rw || 'fio'} · {item.scenario?.fio?.bs || item.fio?.bs || '4k'}</small></div>}<div><Status status={item.status}/>{compact&&<small className="row-date">{date(item.updated_at)}</small>}</div><div className="case-row-end">{!compact&&<span>{date(item.updated_at)}</span>}<Icon name="chevron" size={16}/></div></button>)}</div>;}
