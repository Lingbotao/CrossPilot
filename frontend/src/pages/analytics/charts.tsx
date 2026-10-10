import * as echarts from 'echarts';
import { useEffect, useRef } from 'react';

import type { DashboardDayView, DashboardPlatformView, DashboardPromoView } from '@/api/types';

export function CompareChart({
  rows,
  currency,
  mode,
}: {
  rows: DashboardPlatformView[];
  currency: string;
  mode: 'bar' | 'line' | 'pie';
}) {
  const node = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!node.current) {
      return undefined;
    }
    const chart = echarts.init(node.current);
    const picked = rows.filter((row) => row.currency === currency);
    const names = picked.map((row) => `${row.platform_code} ${row.site_code}`);
    if (mode === 'pie') {
      chart.setOption({
        tooltip: { trigger: 'item' },
        series: [{ type: 'pie', data: picked.map((row) => ({ name: `${row.platform_code} ${row.site_code}`, value: row.gmv })) }],
      });
    } else {
      chart.setOption({
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: names },
        yAxis: { type: 'value' },
        series: [{ type: mode, data: picked.map((row) => row.gmv) }],
      });
    }
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [rows, currency, mode]);
  return <div ref={node} style={{ height: 320, width: '100%' }} />;
}

export function TrendChart({
  days,
  promos,
  currency,
  promoLabels,
}: {
  days: DashboardDayView[];
  promos: DashboardPromoView[];
  currency: string;
  promoLabels: Record<string, string>;
}) {
  const node = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!node.current) {
      return undefined;
    }
    const chart = echarts.init(node.current);
    const picked = days.filter((row) => row.currency === currency);
    const dates = [...new Set(picked.map((row) => row.stat_date))];
    const platforms = [...new Set(picked.map((row) => row.platform_code))];
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: platforms },
      xAxis: { type: 'category', data: dates },
      yAxis: { type: 'value' },
      series: platforms.map((platform) => ({
        name: platform,
        type: 'line',
        data: dates.map((day) => picked.find((row) => row.stat_date === day && row.platform_code === platform)?.gmv ?? null),
        markLine: {
          data: promos
            .filter((item) => dates.includes(item.stat_date))
            .map((item) => ({ xAxis: item.stat_date, label: { formatter: promoLabels[item.code] ?? item.code } })),
        },
      })),
    });
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [days, promos, currency, promoLabels]);
  return <div ref={node} style={{ height: 360, width: '100%' }} />;
}
