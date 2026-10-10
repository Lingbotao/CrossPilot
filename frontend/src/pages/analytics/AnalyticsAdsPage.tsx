import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Space, Table, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { dashboardApi } from '@/api/dashboard';
import type { DashboardAdsRoiView } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { AnalyticsFilters } from './filters';
import { percentLabel } from './query';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

export function AnalyticsAdsPage() {
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const view = useQuery({
    queryKey: ['dashboard', 'ads', query],
    queryFn: () => dashboardApi.adsRoi(query),
  });
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Typography.Title level={3}>{copy.adsTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">
        {copy.adsDescription} <Link to="/ads/loss">{copy.lossAds}</Link>
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
      {view.isError ? <Alert type="error" message={view.error instanceof ApiError ? view.error.message : copy.requestFailed} /> : null}
      <CostGuard fallback={<Alert type="info" message={copy.costHidden} />}>
        <Table<DashboardAdsRoiView>
          rowKey="currency"
          loading={view.isLoading}
          dataSource={view.data ?? []}
          pagination={false}
          columns={[
            { title: copy.currency, dataIndex: 'currency' },
            { title: copy.adSpend, dataIndex: 'ad_spend', render: (value: string | null, row) => <MoneyText value={value} currency={row.currency} /> },
            { title: copy.adSales, dataIndex: 'ad_sales', render: (value: string | null, row) => <MoneyText value={value} currency={row.currency} /> },
            { title: copy.acos, dataIndex: 'acos', render: (value: string | null) => percentLabel(value) },
            { title: copy.roas, dataIndex: 'roas', render: (value: string | null) => value ?? '—' },
            { title: copy.profitRoi, dataIndex: 'profit_roi', render: (value: string | null) => percentLabel(value) },
            { title: copy.loss, dataIndex: 'ad_loss_count' },
          ]}
        />
        <Typography.Paragraph type="secondary">{view.data?.[0]?.roi_formula}</Typography.Paragraph>
      </CostGuard>
    </Space>
  );
}
