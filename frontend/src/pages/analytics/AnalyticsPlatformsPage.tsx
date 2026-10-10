import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Segmented, Space, Table, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { dashboardApi } from '@/api/dashboard';
import type { DashboardPlatformView } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { CompareChart } from './charts';
import { AnalyticsFilters } from './filters';
import { percentLabel } from './query';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

export function AnalyticsPlatformsPage() {
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const [mode, setMode] = useState<'bar' | 'line' | 'pie'>('bar');
  const view = useQuery({
    queryKey: ['dashboard', 'platforms', query],
    queryFn: () => dashboardApi.platforms(query),
  });
  const currencies = [...new Set((view.data ?? []).map((row) => row.currency))];
  const [currency, setCurrency] = useState<string>();
  const selected = currency && currencies.includes(currency) ? currency : currencies[0];
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Typography.Title level={3}>{copy.platformTitle}</Typography.Title>
      <Card>
        <AnalyticsFilters
          initial={values}
          onSearch={setValues}
          onRebuild={(next) => {
            setValues(next);
            rebuild.mutate(next);
          }}
          rebuilding={rebuild.isPending}
        />
      </Card>
      {view.isError ? <Alert type="error" message={view.error instanceof ApiError ? view.error.message : copy.requestFailed} /> : null}
      <Space>
        <Segmented
          value={mode}
          onChange={(next) => setMode(next as 'bar' | 'line' | 'pie')}
          options={[
            { label: copy.bar, value: 'bar' },
            { label: copy.line, value: 'line' },
            { label: copy.pie, value: 'pie' },
          ]}
        />
        <Segmented value={selected} onChange={(next) => setCurrency(String(next))} options={currencies.map((item) => ({ label: item, value: item }))} />
      </Space>
      {selected ? <CompareChart rows={view.data ?? []} currency={selected} mode={mode} /> : null}
      <Table<DashboardPlatformView>
        rowKey={(row) => `${row.platform_code}-${row.site_code}-${row.currency}`}
        loading={view.isLoading}
        dataSource={view.data ?? []}
        pagination={false}
        columns={[
          { title: copy.platform, dataIndex: 'platform_code' },
          { title: copy.site, dataIndex: 'site_code' },
          { title: copy.orders, dataIndex: 'order_count' },
          { title: copy.gmv, dataIndex: 'gmv', render: (value: string, row) => <MoneyText value={value} currency={row.currency} /> },
          {
            title: copy.netProfit,
            dataIndex: 'net_profit',
            render: (value: string | null, row) => (
              <CostGuard fallback="—">
                <MoneyText value={value} currency={row.currency} colorize />
              </CostGuard>
            ),
          },
          { title: copy.onTimeRate, dataIndex: 'on_time_rate', render: (value: string | null) => percentLabel(value) },
        ]}
      />
    </Space>
  );
}
