import type { ObservationPlan, SampleWindow } from './versionTypes';

export interface WindowChoice {
  key: string;
  current?: SampleWindow;
  previous?: SampleWindow;
}

const numericError = 1e-8;
export const endpointTolerance = (plan?: ObservationPlan | null) => plan && plan.interval_s > 0 ? Math.min(0.1, plan.interval_s * 0.03) : 0;

export function plansComparable(current?: ObservationPlan | null, previous?: ObservationPlan | null) {
  return !!current && !!previous && current.interval_s > 0 && previous.interval_s > 0
    && Math.abs(current.interval_s - previous.interval_s) <= Math.max(numericError, current.interval_s * 1e-9)
    && Math.abs(current.warmup_s - previous.warmup_s) <= 0.1 + numericError
    && Math.abs(current.measurement_s - previous.measurement_s) <= 0.1 + numericError;
}

export function phaseOffset(sample: SampleWindow, plan?: ObservationPlan | null) {
  const origin = sample.phase === 'measurement' ? plan?.warmup_s ?? 0 : 0;
  return { start: sample.start_offset_s - origin, end: sample.end_offset_s - origin };
}

export function measurementPointCount(plan?: ObservationPlan | null) {
  if (!plan || plan.interval_s <= 0 || plan.measurement_s <= 0) return null;
  const exact = plan.measurement_s / plan.interval_s;
  const rounded = Math.round(exact);
  return rounded > 0 && Math.abs(plan.measurement_s - rounded * plan.interval_s) <= endpointTolerance(plan) + numericError
    ? rounded : Math.ceil(exact);
}

export function alignSampleWindows(current: SampleWindow[], previous: SampleWindow[], currentPlan?: ObservationPlan | null, previousPlan?: ObservationPlan | null): WindowChoice[] {
  const comparable = plansComparable(currentPlan, previousPlan);
  const tolerance = Math.min(endpointTolerance(currentPlan), endpointTolerance(previousPlan));
  const matching = current.map(after => previous.flatMap((before, index) => {
    if (!comparable || after.phase !== before.phase) return [];
    const a = phaseOffset(after, currentPlan), b = phaseOffset(before, previousPlan);
    return Math.abs(a.start - b.start) <= tolerance + numericError && Math.abs(a.end - b.end) <= tolerance + numericError ? [index] : [];
  }));
  const previousCandidateCounts = previous.map((_, index) => matching.filter(indices => indices.includes(index)).length);
  const paired = new Set<number>();
  const choices: WindowChoice[] = current.map((sample, index) => {
    const candidates = matching[index];
    const previousIndex = candidates.length === 1 && previousCandidateCounts[candidates[0]] === 1 ? candidates[0] : undefined;
    if (previousIndex !== undefined) paired.add(previousIndex);
    return { key: 'current/' + index + '/' + sample.phase + '/' + sample.start_offset_s + '/' + sample.end_offset_s, current: sample, previous: previousIndex === undefined ? undefined : previous[previousIndex] };
  });
  previous.forEach((sample, index) => {
    if (!paired.has(index)) choices.push({ key: 'previous/' + index + '/' + sample.phase + '/' + sample.start_offset_s + '/' + sample.end_offset_s, previous: sample });
  });
  return choices.sort((a, b) => {
    const aSample = a.current ?? a.previous!, bSample = b.current ?? b.previous!;
    const aOffset = phaseOffset(aSample, a.current ? currentPlan : previousPlan);
    const bOffset = phaseOffset(bSample, b.current ? currentPlan : previousPlan);
    return (aSample.phase === 'warmup' ? 0 : 1) - (bSample.phase === 'warmup' ? 0 : 1) || aOffset.start - bOffset.start || aOffset.end - bOffset.end;
  });
}
