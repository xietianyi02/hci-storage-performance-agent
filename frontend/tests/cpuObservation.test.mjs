import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/cpuObservation.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { allObservedCpus, busyTone } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

const topology = (cpu_id, values = {}) => ({ host_role: 'local', cpu_id, numa_node: 0, socket_id: 0, core_id: cpu_id, smt_sibling_cpu_ids: [cpu_id], ...values });
const capture = (cpu_id, values = {}) => ({ ...topology(cpu_id), user_pct: 0, system_pct: 0, irq_pct: 0, softirq_pct: 0, idle_pct: 100, busy_pct: 0, competitors: [], ...values });

test('inventory displays idle and missing CPUs without inferring consecutive identifiers or zero loads', () => {
  const sampled = capture(4);
  const rows = allObservedCpus([topology(0), topology(4), topology(128)], [sampled]);
  assert.deepEqual(rows.map(cpu => cpu.cpu_id), [0, 4, 128]);
  assert.equal(rows[0].capture, undefined);
  assert.equal(rows[1].capture, sampled);
  assert.equal(rows[1].capture.busy_pct, 0);
  assert.equal(rows[2].capture, undefined);
  assert.ok(rows.every(cpu => cpu.inInventory));
});

test('legacy records keep only measured CPU IDs with range unknown', () => {
  const rows = allObservedCpus([], [capture(5), capture(70)]);
  assert.deepEqual(rows.map(cpu => cpu.cpu_id), [5, 70]);
  assert.ok(rows.every(cpu => !cpu.inInventory));
});

test('CPU identities are isolated by host and missing current capture is not replaced from another scope', () => {
  const remote = capture(4, { host_role: 'remote', busy_pct: 90 });
  const rows = allObservedCpus([topology(4), topology(4, { host_role: 'remote' })], [remote]);
  assert.equal(rows.find(cpu => cpu.host_role === 'local').capture, undefined);
  assert.equal(rows.find(cpu => cpu.host_role === 'remote').capture, remote);
});

test('measured CPU outside inventory remains visible and marked outside inventory', () => {
  const rows = allObservedCpus([topology(0)], [capture(8)]);
  assert.deepEqual(rows.map(cpu => cpu.cpu_id), [0, 8]);
  assert.equal(rows[1].inInventory, false);
  assert.equal(rows[1].capture.cpu_id, 8);
});

test('missing busy stays unknown even if idle is present, real zero stays a cold load', () => {
  const row = allObservedCpus([], [capture(5, { busy_pct: null, idle_pct: 20 })])[0];
  assert.equal(row.capture.busy_pct, null);
  assert.equal(busyTone(row.capture.busy_pct), 'unknown');
  assert.equal(busyTone(0), 'cool');
  assert.equal(busyTone(undefined), 'unknown');
});
