import {describe,it,expect} from 'vitest';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import {sameSnapshotContent} from './snapshot-state';
describe('local/cloud display identity',()=>{
  it('ignores only generation time, regardless of JSON property insertion order',()=>{
    const reordered=Object.fromEntries(Object.entries(fixture).reverse());reordered.generatedAt='2026-10-17T08:00:00+08:00';
    expect(sameSnapshotContent(fixture,reordered)).toBe(true);
  });
  it('does not confirm old month coverage from the same unchanged workbook',()=>{
    expect(sameSnapshotContent(fixture,{...fixture,calendarMonth:'2026-11'})).toBe(false);
    const changed=structuredClone(fixture);changed.months[0].summary.personalCents++;
    expect(sameSnapshotContent(fixture,changed)).toBe(false);
  });
});
