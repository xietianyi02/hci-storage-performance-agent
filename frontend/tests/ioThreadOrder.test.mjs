import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/ioThreadOrder.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { groupIoThreads } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
const row = (pool, values = {}) => ({ host_role: 'local', process_role: 'asan-stord', pool, branch: 'business', name: pool, pid: 100, tid: 100, ...values });
const data = (pool, host = 'local', values = {}) => row(pool, { host_role: host, branch: host + '-data', process_role: ['glfsd-net', 'libcomm'].includes(pool) ? 'glusterfsd' : 'asan-stord', ...values });
const flatten = groups => groups.flatMap(group => group.rows);

test('shuffled rows follow guest, protocol, IOR, local DATA, remote DATA, completion, arbiter, unmapped', () => {
  const rows = [row('mystery'), data('tierd-aio', 'remote'), row('gfapi-core'), data('libcomm', 'remote'),
    data('glfsd-net', 'local', { process_role: 'glusterfsd' }), row('main-loop', { process_role: 'kvm', branch: 'protocol' }),
    data('glfsd-net', 'remote', { process_role: 'glusterfsd' }), row('fio', { host_role: 'guest', process_role: 'fio' }),
    data('tierd-core'), row('gfapi-opt'), row('nfs-poller', { process_role: 'qemu' }), row('glfs-nfs'),
    data('tierd-aio'), row('qemu-vcpu', { process_role: 'qemu' }), data('tierd-core', 'remote'),
    row('gfapi-net'), data('libcomm'), row('glfsd-net', { process_role: 'glusterfsd', branch: 'DATA_ARBITER' })];
  const groups = groupIoThreads(rows);
  assert.deepEqual(groups.map(group => group.id), ['guest', 'protocol', 'ior', 'local-data', 'remote-data', 'completion', 'arbiter', 'unmapped']);
  assert.deepEqual(groups.find(group => group.id === 'protocol').rows.map(value => value.pool), ['qemu-vcpu', 'main-loop', 'nfs-poller', 'glfs-nfs']);
  for (const id of ['local-data', 'remote-data']) assert.deepEqual(groups.find(group => group.id === id).rows.map(value => value.pool), ['glfsd-net', 'tierd-core', 'tierd-aio']);
  assert.deepEqual(groups.find(group => group.id === 'completion').rows.map(value => [value.host_role, value.pool]), [['local', 'libcomm'], ['remote', 'libcomm'], ['local', 'gfapi-net']]);
  assert.equal(flatten(groups).length, rows.length);
  assert.equal(new Set(flatten(groups)).size, rows.length);
  assert.deepEqual(flatten(groupIoThreads([...rows].reverse())), flatten(groups));
});

test('arbiter metadata has priority over DATA role and known request/return pools', () => {
  const rows = [data('libcomm', 'remote', { process_role: 'glusterfsd-data', branch: 'DATA_ARBITER' }),
    row('gfapi-opt', { branch: 'arbiter' }), data('glfsd-net', 'local', { process_role: 'glusterfsd-arbiter' })];
  const groups = groupIoThreads(rows);
  assert.deepEqual(groups.map(group => group.id), ['arbiter']);
  assert.equal(groups[0].rows.length, 3);
});

test('unknown glusterfsd and an unclassified branch are retained without assuming DATA', () => {
  const rows = [row('glfsd-net', { process_role: 'glusterfsd', branch: 'local-gluster-unclassified' }),
    row('libcomm', { process_role: 'glusterfsd', branch: '' }), row('background', { name: 'worker' })];
  assert.deepEqual(groupIoThreads(rows).map(group => group.id), ['unmapped']);
  assert.equal(flatten(groupIoThreads(rows)).length, 3);
  assert.equal(groupIoThreads([row('glfsd-net', { process_role: 'glusterfsd-data', branch: '' })])[0].id, 'local-data');
});

test('collector process aliases and main pool work without fabricated protocol handlers', () => {
  const nfs = [row('main', { process_role: 'qemu' }), row('main-loop', { process_role: 'kvm' }), row('nfs-rpc', { process_role: 'stord' }), row('glfs-nfs', { process_role: 'asan-stord' })];
  assert.deepEqual(groupIoThreads(nfs).map(group => group.id), ['protocol']);
  for (const protocol of ['vhost', 'iscsi']) {
    const groups = groupIoThreads([...nfs, row('gfapi-opt'), data('tierd-core')], value => ({ ...value, protocol }));
    assert.deepEqual(groups.map(group => group.id), ['ior', 'local-data', 'unmapped']);
    assert.equal(groups.find(group => group.id === 'unmapped').rows.length, nfs.length);
  }
});

test('known declared pool has priority; missing or unmapped pool may use a known thread name', () => {
  const declared = data('tierd-core', 'local', { name: 'gfapi-opt-0' });
  assert.equal(groupIoThreads([declared])[0].id, 'local-data');
  assert.equal(groupIoThreads([row('unmapped', { name: 'gfapi-opt-2' })])[0].id, 'ior');
  assert.equal(groupIoThreads([row('unmapped', { process_role: 'kvm', name: 'CPU 2/KVM' })])[0].id, 'protocol');
  assert.equal(groupIoThreads([row('custom-pool', { name: 'gfapi-opt-2' })])[0].id, 'unmapped');
});

test('same pool name in an unknown or different process does not establish an IO role', () => {
  const rows = ['gfapi-opt', 'gfapi-core', 'gfapi-net'].map(pool => row(pool, { process_role: 'unrecognized' }));
  rows.push(data('glfsd-net', 'local', { process_role: 'stord' }), data('libcomm', 'remote', { process_role: 'stord' }),
    data('tierd-core', 'local', { process_role: 'glusterfsd-data' }), data('tierd-aio', 'remote', { process_role: 'unrecognized' }));
  const groups = groupIoThreads(rows);
  assert.deepEqual(groups.map(group => group.id), ['unmapped']);
  assert.equal(groups[0].rows.length, rows.length);
});

test('explicit process aliases still require a DATA branch for plain glusterfsd', () => {
  for (const process_role of ['glusterfsd-data', 'data-glusterfsd', 'DATA']) {
    assert.equal(groupIoThreads([row('glfsd-net', { process_role, branch: '' })])[0].id, 'local-data');
    assert.equal(groupIoThreads([row('libcomm', { process_role, branch: '' })])[0].id, 'completion');
  }
  assert.equal(groupIoThreads([row('glfsd-net', { process_role: 'glusterfsd', branch: '' })])[0].id, 'unmapped');
  for (const process_role of ['stord', 'asan-stord']) {
    assert.equal(groupIoThreads([row('gfapi-opt', { process_role })])[0].id, 'ior');
    assert.equal(groupIoThreads([data('tierd-core', 'local', { process_role })])[0].id, 'local-data');
    assert.equal(groupIoThreads([row('gfapi-net', { process_role })])[0].id, 'completion');
  }
});

test('thread names sort numerically, with deterministic numeric PID, TID, starttime tie-breaks', () => {
  const rows = [row('gfapi-opt', { name: 'gfapi-opt-10', tid: 10 }),
    row('gfapi-opt', { name: 'gfapi-opt-2', pid: 20, tid: 3 }),
    row('gfapi-opt', { name: 'gfapi-opt-2', pid: 3, tid: 8 }),
    row('gfapi-opt', { name: 'gfapi-opt-2', pid: 3, tid: 2, starttime_ticks: 11 }),
    row('gfapi-opt', { name: 'gfapi-opt-2', pid: 3, tid: 2, starttime_ticks: 2 })];
  const sorted = flatten(groupIoThreads(rows));
  assert.deepEqual(sorted.map(value => [value.name, value.pid, value.tid, value.starttime_ticks]), [
    ['gfapi-opt-2', 3, 2, 2], ['gfapi-opt-2', 3, 2, 11], ['gfapi-opt-2', 3, 8, undefined], ['gfapi-opt-2', 20, 3, undefined], ['gfapi-opt-10', 100, 10, undefined],
  ]);
  assert.deepEqual(flatten(groupIoThreads([...rows].reverse())), sorted);
});

test('getter preserves wrapper references, original order, readonly inputs, and duplicate source entries', () => {
  const first = Object.freeze({ capture: Object.freeze(data('tierd-core', 'remote')) });
  const second = Object.freeze({ capture: Object.freeze(row('gfapi-opt')) });
  const rows = Object.freeze([first, second, second]);
  const groups = groupIoThreads(rows, value => value.capture);
  assert.deepEqual(rows, [first, second, second]);
  assert.deepEqual(flatten(groups), [second, second, first]);
  assert.equal(groups[0].rows[0], second);
  assert.equal(groups.at(-1).rows[0], first);
  assert.deepEqual(groupIoThreads([]), []);
});
