import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/processThreadGroups.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { groupProcessThreads, capturedCpuTotal } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
const row = (values = {}) => ({ host_role: 'local', process_role: 'asan-stord', pool: 'gfapi-opt', branch: 'business', name: 'worker', pid: 100, tid: 101, starttime_ticks: 10, cpu_pct: 20, ...values });
const flatten = groups => groups.flatMap(group => group.rows);

test('same process name is separated by host and PID, never by individual thread birth', () => {
  const rows = [row({ host_role: 'remote', pid: 2 }), row({ pid: 10 }), row({ pid: 2, tid: 22, starttime_ticks: 90 }),
    row({ host_role: 'guest', process_role: 'fio', pid: 90 }), row({ pid: 2, tid: 21, starttime_ticks: 2 })];
  const groups = groupProcessThreads(rows);
  assert.deepEqual(groups.map(group => [group.host_role, group.process_role, group.pid]), [
    ['guest', 'fio', 90], ['local', 'asan-stord', 2], ['local', 'asan-stord', 10], ['remote', 'asan-stord', 2],
  ]);
  assert.equal(new Set(groups.map(group => group.id)).size, groups.length);
  assert.equal(groups[1].rows.length, 2);
  assert.deepEqual(groups[1].rows.map(value => value.starttime_ticks), [2, 90]);
  assert.equal(flatten(groups).length, rows.length);
  assert.equal(new Set(flatten(groups)).size, rows.length);
});

test('missing PID means a separate role summary; unknown fields and declared aliases are retained', () => {
  const unknown = row({ pid: undefined, process_role: '', pool: 'unknown', name: undefined, branch: '' });
  const rows = [row({ pid: undefined, pool: 'tierd-core' }), row({ pid: undefined }), row(),
    row({ process_role: 'stord' }), row({ process_role: 'asan-stord-data' }), unknown];
  const groups = groupProcessThreads(rows);
  const summary = groups.find(group => group.process_role === 'asan-stord' && group.pid === undefined);
  assert.equal(summary.rows.length, 2);
  assert.equal(groups.find(group => group.process_role === 'asan-stord' && group.pid === 100).rows.length, 1);
  assert.ok(groups.some(group => group.process_role === 'stord'));
  assert.ok(groups.some(group => group.process_role === 'asan-stord-data'));
  assert.equal(groups.find(group => group.process_role === '').rows[0], unknown);
  assert.equal(flatten(groups).length, rows.length);
});

test('group names and rows use natural numeric order and stable TID/starttime tie-breaks', () => {
  const rows = [row({ process_role: 'process10' }), row({ process_role: 'process2', pid: 10 }),
    row({ process_role: 'process2', pid: 2, pool: 'pool10' }),
    row({ process_role: 'process2', pid: 2, pool: 'pool2', name: 'worker10' }),
    row({ process_role: 'process2', pid: 2, pool: 'pool2', name: 'worker2', tid: 10 }),
    row({ process_role: 'process2', pid: 2, pool: 'pool2', name: 'worker2', tid: 2, starttime_ticks: 10 }),
    row({ process_role: 'process2', pid: 2, pool: 'pool2', name: 'worker2', tid: 2, starttime_ticks: 2 })];
  const groups = groupProcessThreads(rows);
  assert.deepEqual(groups.map(group => [group.process_role, group.pid]), [['process2', 2], ['process2', 10], ['process10', 100]]);
  assert.deepEqual(groups[0].rows.map(value => [value.pool, value.name, value.tid, value.starttime_ticks]), [
    ['pool2', 'worker2', 2, 2], ['pool2', 'worker2', 2, 10], ['pool2', 'worker2', 10, 10],
    ['pool2', 'worker10', 101, 10], ['pool10', 'worker', 101, 10],
  ]);
  assert.deepEqual(flatten(groupProcessThreads([...rows].reverse())), flatten(groups));
});

test('tuple IDs avoid ambiguous separators and are independent of source order', () => {
  const rows = [row({ host_role: 'local/a', process_role: 'b' }), row({ host_role: 'local', process_role: 'a/b' }),
    row({ process_role: 'role:100', pid: undefined }), row({ process_role: 'role', pid: 100 })];
  const groups = groupProcessThreads(rows);
  assert.equal(new Set(groups.map(group => group.id)).size, 4);
  assert.deepEqual(groupProcessThreads([...rows].reverse()).map(group => group.id), groups.map(group => group.id));
});

test('frozen source rows and wrappers are never mutated, dropped or deduplicated', () => {
  const first = Object.freeze({ capture: Object.freeze(row({ host_role: 'remote' })) });
  const second = Object.freeze({ capture: Object.freeze(row({ tid: 2 })) });
  const third = Object.freeze({ capture: Object.freeze(row({ tid: 1 })) });
  const rows = Object.freeze([first, second, third, second]);
  const groups = groupProcessThreads(rows, value => value.capture);
  assert.deepEqual(rows, [first, second, third, second]);
  assert.deepEqual(flatten(groups), [third, second, second, first]);
  assert.equal(groups[0].rows[0], third);
  assert.equal(groups[0].rows[1], second);
  assert.deepEqual(groupProcessThreads([]), []);
});

test('indistinguishable rows keep their source order instead of inventing identities', () => {
  const first = row({ label: 'first' }), second = row({ label: 'second' });
  assert.deepEqual(groupProcessThreads([first, second])[0].rows, [first, second]);
  assert.deepEqual(groupProcessThreads([second, first])[0].rows, [second, first]);
});

test('captured CPU totals preserve zero and cross-core totals above 100%', () => {
  assert.equal(capturedCpuTotal([row({ cpu_pct: 0 })]), 0);
  assert.equal(capturedCpuTotal([row({ cpu_pct: 80 }), row({ cpu_pct: 70 })]), 150);
  const wrappers = [{ after: row({ cpu_pct: 80 }) }, { after: row({ cpu_pct: 70 }) }];
  assert.equal(capturedCpuTotal(wrappers, value => value.after), 150);
});

test('captured CPU totals stay unknown for missing, invalid, empty or overflowing observations', () => {
  assert.equal(capturedCpuTotal([]), null);
  for (const value of [undefined, null, NaN, Infinity, -Infinity, -1, '20']) {
    assert.equal(capturedCpuTotal([row({ cpu_pct: 20 }), row({ cpu_pct: value })]), null);
  }
  assert.equal(capturedCpuTotal([row({ cpu_pct: Number.MAX_VALUE }), row({ cpu_pct: Number.MAX_VALUE })]), null);
});
