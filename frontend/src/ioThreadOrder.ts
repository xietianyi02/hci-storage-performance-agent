export interface IoThreadDescriptor {
  host_role: string;
  process_role: string;
  pool: string;
  branch: string;
  name?: string;
  pid?: number;
  tid?: number;
  starttime_ticks?: number;
  protocol?: 'nfs' | 'vhost' | 'iscsi';
}

export type IoThreadGroupId = 'guest' | 'protocol' | 'ior' | 'local-data' | 'remote-data' | 'completion' | 'arbiter' | 'unmapped';
export interface IoThreadGroup<T> { id: IoThreadGroupId; title: string; rows: T[] }

export const ioThreadGroups: readonly { id: IoThreadGroupId; title: string }[] = [
  { id: 'guest', title: 'VM · fio' }, { id: 'protocol', title: '协议层' },
  { id: 'ior', title: '业务入口 · IOR' }, { id: 'local-data', title: '本地数据分支' },
  { id: 'remote-data', title: '远端数据分支' }, { id: 'completion', title: '完成返回' },
  { id: 'arbiter', title: '仲裁控制' }, { id: 'unmapped', title: '未映射' },
];

// Extend known pool aliases here; branch and process-role checks remain explicit.
const poolAliases: readonly [string, RegExp][] = [
  ['fio', /^fio(?:[-_./ ]\d+)?$/],
  ['qemu-vcpu', /^(?:qemu-vcpu(?:[-_./ ]\d+)?|cpu.*kvm)$/],
  ['main', /^(?:main|main-loop)$/],
  ['nfs-poller', /^(?:nfs-poller(?:[-_./ ]\d+)?|nfs.*poll(?:er)?(?:[-_./ ]\d+)?)$/],
  ['nfs-rpc', /^(?:nfs-rpc|glfs-nfs)(?:[-_./ ]\d+)?$/],
  ['gfapi-opt', /^(?:gfapi-opt(?:[-_./ ]\d+)?|ior)$/],
  ['gfapi-core', /^(?:gfapi-core(?:[-_./ ]\d+)?|afr)$/],
  ['glfsd-net', /^glfsd-net(?:[-_./ ]\d+)?$/],
  ['tierd-core', /^tierd-core(?:[-_./ ]\d+)?$/],
  ['tierd-aio', /^tierd-aio(?:[-_./ ]\d+)?$/],
  ['libcomm', /^libcomm(?:[-_./ ]\d+)?$/],
  ['gfapi-net', /^gfapi-net(?:[-_./ ]\d+)?$/],
];
const normalize = (value?: string) => value?.trim().toLowerCase() ?? '';
const natural = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });
const hostOrder = (host: string) => ({ guest: 0, local: 1, remote: 2 })[host as 'guest' | 'local' | 'remote'] ?? 3;
const isQemu = (role: string) => /^(?:kvm|qemu(?:[-_].*)?)$/.test(role);
const isStord = (role: string) => /^(?:asan-)?stord$/.test(role);
const isGluster = (role: string) => /^(?:glusterfsd|glusterfsd-data|data-glusterfsd|data)$/.test(role);
const explicitArbiter = (row: IoThreadDescriptor) => [row.branch, row.process_role, row.pool].some(value => /(?:^|[^a-z0-9])(?:arbiter|data[_-]arbiter)(?:$|[^a-z0-9])/.test(normalize(value)));
const explicitData = (row: IoThreadDescriptor) => /^(?:(?:local|remote)[-_])?data(?:[-_]branch)?$/.test(normalize(row.branch)) || /^(?:glusterfsd-data|data-glusterfsd|data)$/.test(normalize(row.process_role));

function poolOf(row: IoThreadDescriptor) {
  const declared = normalize(row.pool);
  const pool = poolAliases.find(([, pattern]) => pattern.test(declared))?.[0];
  if (pool) return pool;
  if (!declared || ['unmapped', 'unknown', 'unknown-pool'].includes(declared)) return poolAliases.find(([, pattern]) => pattern.test(normalize(row.name)))?.[0] ?? declared;
  return declared;
}

function position(row: IoThreadDescriptor): { group: IoThreadGroupId; pool: string; order: number } {
  const host = normalize(row.host_role), role = normalize(row.process_role), pool = poolOf(row);
  if (explicitArbiter(row)) return { group: 'arbiter', pool, order: 0 };
  if (host === 'guest' && (pool === 'fio' || role === 'fio')) return { group: 'guest', pool, order: 0 };
  const nfsKnown = row.protocol === undefined || row.protocol === 'nfs';
  if (host === 'local' && nfsKnown) {
    const front = isQemu(role) ? { 'qemu-vcpu': 0, main: 1, 'nfs-poller': 2 }[pool as 'qemu-vcpu' | 'main' | 'nfs-poller'] : undefined;
    if (front !== undefined) return { group: 'protocol', pool, order: front };
    if (pool === 'nfs-rpc' && isStord(role)) return { group: 'protocol', pool, order: 3 };
  }
  if (host === 'local' && isStord(role) && (pool === 'gfapi-opt' || pool === 'gfapi-core')) return { group: 'ior', pool, order: pool === 'gfapi-opt' ? 0 : 1 };
  const data = explicitData(row);
  if ((host === 'local' || host === 'remote') && data) {
    const dataOrder = { 'glfsd-net': 0, 'tierd-core': 1, 'tierd-aio': 2 }[pool as 'glfsd-net' | 'tierd-core' | 'tierd-aio'];
    const validProcess = pool === 'glfsd-net' ? isGluster(role) : isStord(role);
    if (dataOrder !== undefined && validProcess) return { group: host === 'local' ? 'local-data' : 'remote-data', pool, order: dataOrder };
    if (pool === 'libcomm' && isGluster(role)) return { group: 'completion', pool, order: host === 'local' ? 0 : 1 };
  }
  if (host === 'local' && isStord(role) && pool === 'gfapi-net') return { group: 'completion', pool, order: 2 };
  return { group: 'unmapped', pool, order: 0 };
}

export function groupIoThreads<T>(rows: readonly T[], getter: (row: T) => IoThreadDescriptor = row => row as IoThreadDescriptor): IoThreadGroup<T>[] {
  const grouped = new Map<IoThreadGroupId, { row: T; descriptor: IoThreadDescriptor; pool: string; order: number }[]>();
  rows.forEach(row => {
    const descriptor = getter(row), place = position(descriptor);
    const group = grouped.get(place.group) ?? [];
    group.push({ row, descriptor, pool: place.pool, order: place.order });
    grouped.set(place.group, group);
  });
  return ioThreadGroups.flatMap(group => {
    const values = grouped.get(group.id);
    if (!values?.length) return [];
    values.sort((a, b) => a.order - b.order
      || hostOrder(normalize(a.descriptor.host_role)) - hostOrder(normalize(b.descriptor.host_role))
      || natural.compare(a.pool, b.pool)
      || natural.compare(a.descriptor.name ?? '', b.descriptor.name ?? '')
      || (a.descriptor.pid ?? -1) - (b.descriptor.pid ?? -1)
      || (a.descriptor.tid ?? -1) - (b.descriptor.tid ?? -1)
      || (a.descriptor.starttime_ticks ?? -1) - (b.descriptor.starttime_ticks ?? -1)
      || natural.compare(a.descriptor.process_role, b.descriptor.process_role)
      || natural.compare(a.descriptor.branch, b.descriptor.branch));
    return [{ ...group, rows: values.map(value => value.row) }];
  });
}
