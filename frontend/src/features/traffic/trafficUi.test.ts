import { describe, expect, it } from 'vitest';
import { trafficResponse } from '../../test/trafficFixtures';
import {
  buildTrendData,
  buildTrendSeries,
  bucketRate,
  freshnessKind,
  protocolBreakdown,
} from './trafficUi';

describe('traffic presentation calculations', () => {
  it('converts bucket totals to real per-second rates using the response resolution', () => {
    const point = trafficResponse().series[0];
    expect(bucketRate(point, 'minute', 'bytes')).toMatchObject({ tx: 20, rx: 10 });
    expect(bucketRate(point, '5minute', 'bytes')).toMatchObject({ tx: 4, rx: 2 });
    expect(bucketRate(point, 'hour', 'packets')).toMatchObject({
      tx: 12 / 3600, rx: 6 / 3600,
    });
  });

  it('keeps only server buckets and never fills a missing interval with zero', () => {
    const response = trafficResponse({
      series: [
        trafficResponse().series[0],
        { ...trafficResponse().series[1], bucket_start: '2026-09-21T10:05:00Z' },
      ],
    });
    const points = buildTrendData(response, 'bytes');
    expect(points).toHaveLength(2);
    expect(points.map((point) => point.bucket_start)).toEqual([
      '2026-09-21T09:58:00Z', '2026-09-21T10:05:00Z',
    ]);
    expect(buildTrendSeries(response, 'bytes', 'tx')).toEqual([
      ['2026-09-21T09:58:00Z', 20],
      ['2026-09-21T09:59:00.000Z', null],
      ['2026-09-21T10:05:00Z', 30],
    ]);
  });

  it('calculates protocol percentages without hiding unknown or inferred entries', () => {
    const result = protocolBreakdown([
      ...trafficResponse().protocols,
      {
        ...trafficResponse().protocols[0],
        protocol: 'MQTT?', evidence: 'inferred_application_protocol',
        tx_bytes: 500, rx_bytes: 0,
      },
    ], 'bytes');
    expect(result.map((item) => item.protocol)).toEqual(['TCP', 'UNKNOWN', 'MQTT?']);
    expect(result.reduce((sum, item) => sum + (item.percentage ?? 0), 0)).toBeCloseTo(100);
    expect(result[2].evidence).toBe('inferred_application_protocol');
  });

  it('distinguishes unknown, fresh, and stale sample times', () => {
    const now = new Date('2026-09-21T10:00:00Z');
    expect(freshnessKind(null, now)).toBe('unknown');
    expect(freshnessKind('2026-09-21T09:59:55Z', now)).toBe('fresh');
    expect(freshnessKind('2026-09-21T09:59:00Z', now)).toBe('stale');
  });
});
