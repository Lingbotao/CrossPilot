import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Space, Table, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { dashboardApi } from '@/api/dashboard';
import { Perm, type DashboardSkuRankView } from '@/api/types';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';

import { DualMoney } from './DualMoney';
import { ExportDetail } from './exportDetail';
import { AnalyticsFilters } from './filters';
import { percentLabel } from './query';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

function RankTable({ title, rows, loading }: { title: string; rows: DashboardSkuRankView[]; loading: boolean }) {
  return (
    <Card title={title}>
      <Table<DashboardSkuRankView>
        rowKey={(row) => `${title}-${row.sku_id}-${row.currency}`}
        loading={loading}
        dataSource={rows}
        pagination={false}
        columns={[
          {
            title: copy.sku,
            dataIndex: 'sku_code',
            render: (value: string, row) => <Link to={`/products/${row.spu_id}`}>{value}</Link>,
          },
          { title: copy.currency, dataIndex: 'currency' },
          {
            title: copy.revenue,
            dataIndex: 'revenue',
            render: (_value: string, row) => (
              <DualMoney value={row.revenue} currency={row.currency} bookValue={row.book_revenue} bookCurrency={row.book_currency} />
            ),
          },
          {
            title: copy.netProfit,
            dataIndex: 'net_profit',
            render: (_value: string | null, row) => (
              <DualMoney
                value={row.net_profit}
                currency={row.currency}
                bookValue={row.book_net_profit}
                bookCurrency={row.book_currency}
                colorize
              />
            ),
          },
          { title: copy.netMargin, dataIndex: 'net_margin', render: (value: string | null) => percentLabel(value) },
          { title: copy.loss, dataIndex: 'loss', render: (value: boolean) => (value ? <Tag color="red">{copy.loss}</Tag> : copy.profit) },
        ]}
      />
    </Card>
  );
}

export function AnalyticsRankingPage() {
  const { canViewCost } = usePermission();
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const view = useQuery({
    queryKey: ['dashboard', 'ranking', query],
    queryFn: () => dashboardApi.ranking(query),
    enabled: canViewCost,
  });
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <div>
          <Typography.Title level={3}>{copy.rankingTitle}</Typography.Title>
          <Typography.Paragraph type="secondary">{copy.rankingDescription}</Typography.Paragraph>
        </div>
        <PermissionGuard permission={Perm.REPORT_EXPORT}>
          <ExportDetail dateFrom={query.date_from} dateTo={query.date_to} />
        </PermissionGuard>
      </Space>
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
        <Typography.Paragraph type="secondary">{copy.incomplete.replace('{{count}}', String(view.data?.incomplete_count ?? 0))}</Typography.Paragraph>
        <RankTable title={copy.top} rows={view.data?.top ?? []} loading={view.isLoading} />
        <RankTable title={copy.bottom} rows={view.data?.bottom ?? []} loading={view.isLoading} />
        <RankTable title={copy.lossList} rows={view.data?.loss ?? []} loading={view.isLoading} />
      </CostGuard>
    </Space>
  );
}
