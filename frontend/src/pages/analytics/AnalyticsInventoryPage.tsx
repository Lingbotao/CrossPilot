import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Col, Row, Space, Statistic, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { dashboardApi } from '@/api/dashboard';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { AnalyticsFilters } from './filters';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

export function AnalyticsInventoryPage() {
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const view = useQuery({
    queryKey: ['dashboard', 'inventory', query.date_from, query.date_to],
    queryFn: () => dashboardApi.inventory({ date_from: query.date_from, date_to: query.date_to }),
  });
  const data = view.data;
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Typography.Title level={3}>{copy.inventoryTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">
        {copy.inventoryDescription} <Link to="/inventory/replenishment">{copy.replenishment}</Link>
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
      {!data?.as_of ? <Alert type="info" message={copy.inventoryEmpty} /> : <Typography.Text type="secondary">{copy.asOf} {data.as_of}</Typography.Text>}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}><Card><Statistic title={copy.onHand} value={data?.on_hand_qty ?? 0} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title={copy.stockout} value={data?.stockout_sku_count ?? 0} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title={copy.belowSafe} value={data?.below_safe_sku_count ?? 0} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title={copy.stale} value={data?.stale_sku_count ?? 0} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title={copy.turnover} value={data?.turnover_days ?? '—'} /></Card></Col>
      </Row>
      <Typography.Paragraph type="secondary">{data?.turnover_formula}</Typography.Paragraph>
      <CostGuard fallback={<Alert type="info" message={copy.costHidden} />}>
        {(data?.stale_amounts ?? []).map((row) => (
          <div key={row.currency}>
            {copy.staleAmount} <MoneyText value={row.amount} currency={row.currency} />
            {row.complete ? '' : ` ${copy.completeNo}`}
          </div>
        ))}
        {(data?.stale_sku_count ?? 0) > 0 && (data?.stale_amounts ?? []).length === 0 ? (
          <Alert type="info" message={copy.staleUnpriced} />
        ) : null}
      </CostGuard>
    </Space>
  );
}
