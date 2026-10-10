import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Space, Table, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { dashboardApi } from '@/api/dashboard';
import type { DashboardCostLineView } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard } from '@/components/PermissionGuard';
import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';

import { AnalyticsFilters } from './filters';
import { percentLabel } from './query';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

export function AnalyticsCostsPage() {
  const { canViewCost } = usePermission();
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const view = useQuery({
    queryKey: ['dashboard', 'costs', query],
    queryFn: () => dashboardApi.costs(query),
    enabled: canViewCost,
  });
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Typography.Title level={3}>{copy.costTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">
        {copy.costDescription} <Link to="/finance/waterfall">{copy.waterfall}</Link>
      </Typography.Paragraph>
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
      <CostGuard fallback={<Alert type="info" message={copy.costHidden} />}>
        {view.isError ? <Alert type="error" message={view.error instanceof ApiError ? view.error.message : copy.requestFailed} /> : null}
        <Table<DashboardCostLineView>
          rowKey={(row) => `${row.currency}-${row.code}`}
          loading={view.isLoading}
          dataSource={view.data ?? []}
          pagination={false}
          columns={[
            { title: copy.currency, dataIndex: 'currency' },
            { title: copy.costLine, dataIndex: 'code', render: (value: string) => copy.lines[value as keyof typeof copy.lines] ?? value },
            { title: copy.amount, dataIndex: 'amount', render: (value: string | null, row) => <MoneyText value={value} currency={row.currency} /> },
            { title: copy.share, dataIndex: 'share', render: (value: string | null) => percentLabel(value) },
            { title: copy.complete, dataIndex: 'complete', render: (value: boolean) => (value ? copy.completeYes : copy.completeNo) },
          ]}
        />
      </CostGuard>
    </Space>
  );
}
