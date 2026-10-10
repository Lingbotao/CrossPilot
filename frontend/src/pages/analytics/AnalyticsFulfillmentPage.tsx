import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Space, Table, Typography } from 'antd';

import { ApiError } from '@/api/client';
import { dashboardApi } from '@/api/dashboard';
import type { DashboardFulfillmentView } from '@/api/types';
import zhCN from '@/i18n/zh-CN';

import { AnalyticsFilters } from './filters';
import { percentLabel } from './query';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

export function AnalyticsFulfillmentPage() {
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const view = useQuery({
    queryKey: ['dashboard', 'fulfillment', query],
    queryFn: () => dashboardApi.fulfillment(query),
  });
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Typography.Title level={3}>{copy.fulfillmentTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">{copy.fulfillmentDescription}</Typography.Paragraph>
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
      <Table<DashboardFulfillmentView>
        rowKey={(row) => `${row.platform_code}-${row.site_code}-${row.currency}`}
        loading={view.isLoading}
        dataSource={view.data ?? []}
        pagination={false}
        columns={[
          { title: copy.platform, dataIndex: 'platform_code' },
          { title: copy.site, dataIndex: 'site_code' },
          { title: copy.orders, dataIndex: 'order_count' },
          { title: copy.onTime, dataIndex: 'on_time' },
          { title: copy.late, dataIndex: 'late' },
          { title: copy.onTimeRate, dataIndex: 'on_time_rate', render: (value: string | null) => percentLabel(value) },
          { title: copy.returns, dataIndex: 'return_count' },
          { title: copy.returnRate, dataIndex: 'return_rate', render: (value: string | null) => percentLabel(value) },
        ]}
      />
    </Space>
  );
}
