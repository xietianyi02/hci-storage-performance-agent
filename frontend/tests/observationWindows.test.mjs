import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Use the project's compiler so the checks also run on supported Node 20/22.
const source = await readFile(new URL('../src/observationWindows.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { alignSampleWindows, measurementPointCount } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

const plan = (values = {}) => ({ started_at: '2026-09-29T00:00:00Z', warmup_s: 30, measurement_s: 60, interval_s: 3, ...values });
const window = (start = 30, end = 33, values = {}) => ({ start_offset_s: start, end_offset_s: end, phase: 'measurement', iops: 100, completed_ios: 300, latency_ms: 1, threads: [], cpus: [], network: [], layers: [], ...values });
const pairs = choices => choices.filter(value => value.current && value.previous);

test('small endpoint jitter pairs original records without resampling', () => {
  const current = window(30.002, 33.005), previous = window(30.003, 33.006);
  const result = alignSampleWindows([current], [previous], plan(), plan());
  assert.equal(result.length, 1);
  assert.equal(result[0].current, current);
  assert.equal(result[0].previous, previous);
  assert.equal(result[0].current.end_offset_s - result[0].current.start_offset_s, current.end_offset_s - current.start_offset_s);
});

test('formal timestamps use each run own warmup origin', () => {
  const result = alignSampleWindows([window(30.002, 33.005)], [window(30.053, 33.056)], plan(), plan({ warmup_s: 30.05 }));
  assert.equal(pairs(result).length, 1);
});

test('changed interval or duration does not pair coincident offsets', () => {
  for (const previousPlan of [plan({ interval_s: 6 }), plan({ warmup_s: 29 }), plan({ measurement_s: 61 })]) {
    const result = alignSampleWindows([window()], [window()], plan(), previousPlan);
    assert.equal(pairs(result).length, 0);
    assert.equal(result.length, 2);
  }
});

test('tiny interval representation error is accepted', () => {
  assert.equal(pairs(alignSampleWindows([window()], [window()], plan(), plan({ interval_s: 3.000000001 }))).length, 1);
});

test('ambiguous match in either direction remains unpaired', () => {
  assert.equal(pairs(alignSampleWindows([window()], [window(30.01, 33.01), window(30.02, 33.02)], plan(), plan())).length, 0);
  assert.equal(pairs(alignSampleWindows([window(30.01, 33.01), window(30.02, 33.02)], [window()], plan(), plan())).length, 0);
});

test('missing, out-of-tolerance, cross-phase and missing-plan records stay missing', () => {
  const current = window();
  const missing = alignSampleWindows([current], [], plan(), plan());
  assert.equal(missing.length, 1);
  assert.equal(missing[0].current, current);
  assert.equal(missing[0].previous, undefined);
  assert.equal(pairs(alignSampleWindows([current], [window(30.2, 33.2)], plan(), plan())).length, 0);
  assert.equal(pairs(alignSampleWindows([current], [window(30, 33, { phase: 'warmup' })], plan(), plan())).length, 0);
  assert.equal(pairs(alignSampleWindows([current], [window()], plan(), null)).length, 0);
});

test('expected point count tolerates runtime jitter but retains a real final partial interval', () => {
  assert.equal(measurementPointCount(plan({ measurement_s: 60.004 })), 20);
  assert.equal(measurementPointCount(plan({ measurement_s: 59.995 })), 20);
  assert.equal(measurementPointCount(plan({ measurement_s: 60.2 })), 21);
  assert.equal(measurementPointCount(null), null);
});
