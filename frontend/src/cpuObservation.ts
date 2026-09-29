import type { CpuCapture, CpuTopology } from './versionTypes';

export interface CpuObservation extends CpuTopology {
  capture?: CpuCapture;
  inInventory: boolean;
}

export const cpuIdentity = (cpu: Pick<CpuTopology, 'host_role' | 'cpu_id'>) => cpu.host_role + '/' + cpu.cpu_id;

export function allObservedCpus(inventory: CpuTopology[], captures: CpuCapture[]): CpuObservation[] {
  const samples = new Map(captures.map(cpu => [cpuIdentity(cpu), cpu]));
  const rows = new Map<string, CpuObservation>();
  inventory.forEach(cpu => rows.set(cpuIdentity(cpu), { ...cpu, inInventory: true, capture: samples.get(cpuIdentity(cpu)) }));
  captures.forEach(cpu => {
    if (!rows.has(cpuIdentity(cpu))) rows.set(cpuIdentity(cpu), {
      host_role: cpu.host_role, cpu_id: cpu.cpu_id, numa_node: cpu.numa_node,
      socket_id: cpu.socket_id ?? null, core_id: cpu.core_id ?? null,
      smt_sibling_cpu_ids: cpu.smt_sibling_cpu_ids ?? [], capture: cpu, inInventory: false,
    });
  });
  return [...rows.values()].sort((a, b) => a.host_role.localeCompare(b.host_role) || a.cpu_id - b.cpu_id);
}

export function busyTone(value: number | null | undefined) {
  if (value == null) return 'unknown';
  return value < 20 ? 'cool' : value < 50 ? 'low' : value < 80 ? 'medium' : value < 95 ? 'high' : 'max';
}
