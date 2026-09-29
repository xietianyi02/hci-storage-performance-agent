export interface ProcessThreadDescriptor {
  host_role: string;
  process_role: string;
  pool: string;
  branch: string;
  name?: string;
  pid?: number;
  tid?: number;
  starttime_ticks?: number;
  cpu_pct?: number | null;
}

export interface ProcessThreadGroup<T> {
  id: string;
  host_role: string;
  process_role: string;
  pid?: number;
  rows: T[];
}

const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });
const natural = (a: string, b: string) => collator.compare(a, b) || (a < b ? -1 : a > b ? 1 : 0);
const hostOrder = (host: string) => ({ guest: 0, local: 1, remote: 2 })[host as 'guest' | 'local' | 'remote'] ?? 3;
const numberOrder = (a?: number, b?: number) => a === b ? 0 : a === undefined ? -1 : b === undefined ? 1 : a - b;

export function groupProcessThreads<T>(
  rows: readonly T[],
  getter: (row: T) => ProcessThreadDescriptor = row => row as ProcessThreadDescriptor,
): ProcessThreadGroup<T>[] {
  const groups = new Map<string, {
    host_role: string;
    process_role: string;
    pid?: number;
    entries: { row: T; descriptor: ProcessThreadDescriptor; index: number }[];
  }>();
  rows.forEach((row, index) => {
    const descriptor = getter(row);
    // Preserve declared identities; neither a pool alias nor a thread's birth
    // time identifies a different process. Missing PID means a role summary.
    const id = JSON.stringify([descriptor.host_role, descriptor.process_role, descriptor.pid ?? null]);
    const group = groups.get(id) ?? {
      host_role: descriptor.host_role, process_role: descriptor.process_role,
      pid: descriptor.pid, entries: [],
    };
    group.entries.push({ row, descriptor, index });
    groups.set(id, group);
  });
  return [...groups].sort(([, a], [, b]) => hostOrder(a.host_role) - hostOrder(b.host_role)
    || natural(a.host_role, b.host_role)
    || natural(a.process_role, b.process_role)
    || numberOrder(a.pid, b.pid)).map(([id, group]) => ({
      id, host_role: group.host_role, process_role: group.process_role, pid: group.pid,
      rows: group.entries.sort((a, b) => natural(a.descriptor.pool, b.descriptor.pool)
        || natural(a.descriptor.name ?? '', b.descriptor.name ?? '')
        || numberOrder(a.descriptor.tid, b.descriptor.tid)
        || numberOrder(a.descriptor.starttime_ticks, b.descriptor.starttime_ticks)
        || natural(a.descriptor.branch, b.descriptor.branch)
        || a.index - b.index).map(entry => entry.row),
    }));
}

// Sum only the collected threads. This does not certify complete process CPU
// coverage; simultaneous threads on different CPUs can legitimately exceed 100%.
export function capturedCpuTotal<T>(
  rows: readonly T[],
  getter: (row: T) => ProcessThreadDescriptor = row => row as ProcessThreadDescriptor,
): number | null {
  if (rows.length === 0) return null;
  let total = 0;
  for (const row of rows) {
    const value = getter(row).cpu_pct;
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
    total += value;
  }
  return Number.isFinite(total) ? total : null;
}
