import type { ReactNode } from 'react';

const paths: Record<string, ReactNode> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  cases: <><path d="M8 6V4h8v2M3 7h18v14H3z"/><path d="M3 12h18M9 12v3h6v-3"/></>,
  book: <><path d="M12 6C9 3 5 3 2 4v16c4-1 7-1 10 2 3-3 6-3 10-2V4c-3-1-7-1-10 2zM12 6v16"/></>,
  workflow: <><rect x="8" y="2" width="8" height="5" rx="1"/><rect x="2" y="17" width="7" height="5" rx="1"/><rect x="15" y="17" width="7" height="5" rx="1"/><path d="M12 7v5M5.5 17v-5h13v5"/></>,
  tool: <><path d="M14 6a5 5 0 0 0-6 6L2 18l4 4 6-6a5 5 0 0 0 6-6l-4 4-4-4 4-4z"/></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
  chevron: <path d="m9 5 7 7-7 7"/>,
  down: <path d="m6 9 6 6 6-6"/>,
  plus: <path d="M12 5v14M5 12h14"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
  activity: <path d="M2 12h5l3-8 4 16 3-8h5"/>,
  server: <><rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M6 6.5h.01M6 17.5h.01M10 7h7M10 18h7"/></>,
  flask: <><path d="M9 3h6M10 3v6L4 19a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2L14 9V3M8 14h8"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4M18 4h4"/></>,
  file: <><path d="M14 2H4v20h16V8zM14 2v6h6M8 12h8M8 16h8"/></>,
  play: <path d="m8 4 13 8-13 8z"/>,
  shield: <><path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6z"/><path d="m8 12 3 3 5-6"/></>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
  refresh: <><path d="M20 8a9 9 0 0 0-15-3L2 8M2 3v5h5M4 16a9 9 0 0 0 15 3l3-3M22 21v-5h-5"/></>,
  menu: <path d="M3 6h18M3 12h18M3 18h18"/>,
  download: <><path d="M12 3v12m-5-5 5 5 5-5M3 16v5h18v-5"/></>,
};
export function Icon({name,size=20,className=''}:{name:string;size?:number;className?:string}) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" className={className}>{paths[name] || paths.file}</svg>;
}
export function Badge({children,tone='neutral'}:{children:ReactNode;tone?:string}) { return <span className={`badge badge-${tone}`}>{children}</span>; }
export function Empty({title,detail}:{title:string;detail?:string}) {return <div className="empty"><Icon name="file" size={30}/><h3>{title}</h3>{detail && <p>{detail}</p>}</div>;}
export const workflowLabels: Record<string,string> = {version:'版本问题排查',poc:'POC 调优',research:'预研调优'};
export const workflowIcons: Record<string,string> = {version:'activity',poc:'server',research:'flask'};
export const statusLabels: Record<string,string> = {new:'待开始',running:'运行中',awaiting_approval:'待实验确认',completed:'已完成',failed:'执行失败',cancelled:'已取消',skipped:'已跳过',pending:'待执行',active:'进行中',blocked:'等待确认',planned:'待实验',candidate:'候选假设',supported:'证据支持',rejected:'已否定',confirmed:'已确认'};
export function statusTone(status:string) { return status==='completed'||status==='confirmed'||status==='supported' ? 'green' : status==='awaiting_approval'||status==='blocked'||status==='pending' ? 'amber' : status==='failed'||status==='rejected' ? 'red' : status==='running'||status==='active' ? 'blue' : 'neutral'; }
export function Status({status,label}:{status:string;label?:string}) {return <Badge tone={statusTone(status)}><span className="status-dot"/>{label || statusLabels[status] || status}</Badge>;}
export function number(value:number|undefined) {return value === undefined ? '—' : new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1}).format(value);}
export function date(value?:string) {if(!value)return '刚刚';const parsed = new Date(value);return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}
export function formatContent(value:unknown):string { if(value===undefined||value===null)return ''; if(typeof value==='string')return value; if(Array.isArray(value))return value.map(formatContent).join('\n'); return JSON.stringify(value,null,2); }
