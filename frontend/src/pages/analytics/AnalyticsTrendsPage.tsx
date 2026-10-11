import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Segmented, Space, Table, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { dashboardApi } from '@/api/dashboard';
import type { DashboardDayView } from '@/api/types';
import { CostGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { TrendChart } from './charts';
import { DualMoney } from './DualMoney';
import { AnalyticsFilters } from './filters';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

export function AnalyticsTrendsPage() {
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const view = useQuery({
    queryKey: ['dashboard', 'trends', query],
    queryFn: () => dashboardApi.trends(query),
  });
  const currencies = [...new Set((view.data?.days ?? []).map((row) => row.currency))];
  const [picked, setPicked] = useState<string>();
  const currency = picked && currencies.includes(picked) ? picked : currencies[0];
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Typography.Title level={3}>{copy.trendTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">{copy.trendDescription}</Typography.Paragraph>
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
      {currencies.length > 1 ? (
        <Segmented value={currency} onChange={(next) => setPicked(String(next))} options={currencies.map((item) => ({ label: item, value: item }))} />
      ) : null}
      {currency ? (
        <TrendChart days={view.data?.days ?? []} promos={view.data?.promos ?? []} currency={currency} promoLabels={copy.promos} />
      ) : null}
      <Table<DashboardDayView>
        rowKey={(row) => `${row.stat_date}-${row.platform_code}-${row.currency}`}
        loading={view.isLoading}
        dataSource={view.data?.days ?? []}
        pagination={false}
        columns={[
          { title: copy.date, dataIndex: 'stat_date' },
          { title: copy.platform, dataIndex: 'platform_code' },
          { title: copy.orders, dataIndex: 'order_count' },
          {
            title: copy.gmv,
            dataIndex: 'gmv',
            render: (_value: string, row) => (
              <DualMoney value={row.gmv} currency={row.currency} bookValue={row.book_gmv} bookCurrency={row.book_currency} />
            ),
          },
          {
            title: copy.netProfit,
            dataIndex: 'net_profit',
            render: (_value: string | null, row) => (
              <CostGuard fallback="—">
                <DualMoney
                  value={row.net_profit}
                  currency={row.currency}
                  bookValue={row.book_net_profit}
                  bookCurrency={row.book_currency}
                  colorize
                />
              </CostGuard>
            ),
          },
        ]}
      />
    </Space>
  );
}
