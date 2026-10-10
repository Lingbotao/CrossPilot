import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Col, Row, Space, Statistic, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { dashboardApi } from '@/api/dashboard';
import { ApiError } from '@/api/client';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { AnalyticsFilters } from './filters';
import { percentLabel } from './query';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function AnalyticsOverviewPage() {
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const overview = useQuery({
    queryKey: ['dashboard', 'overview', query],
    queryFn: () => dashboardApi.overview(query),
  });
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.overviewTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.overviewDescription}</Typography.Paragraph>
      </div>
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
      {overview.isError ? <Alert type="error" message={messageOf(overview.error)} /> : null}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title={<Link to="/analytics/fulfillment">{copy.orders}</Link>} value={overview.data?.order_count ?? 0} />
            <Typography.Text type="secondary">{copy.change} {percentLabel(overview.data?.order_change)}</Typography.Text>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card title={<Link to="/analytics/trends">{copy.gmv}</Link>}>
            {(overview.data?.currencies ?? []).length === 0 ? '—' : null}
            {(overview.data?.currencies ?? []).map((row) => (
              <div key={row.currency}>
                <MoneyText value={row.gmv} currency={row.currency} />
                <Typography.Text type="secondary"> {copy.change} {percentLabel(row.gmv_change)}</Typography.Text>
              </div>
            ))}
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title={<Link to="/analytics/fulfillment">{copy.onTimeRate}</Link>} value={percentLabel(overview.data?.on_time_rate)} />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <CostGuard fallback={<Typography.Text type="secondary">{copy.costHidden}</Typography.Text>}>
              <Typography.Text strong>
                <Link to="/analytics/ranking">{copy.netProfit}</Link>
              </Typography.Text>
              {(overview.data?.currencies ?? []).map((row) => (
                <div key={row.currency}>
                  <MoneyText value={row.net_profit} currency={row.currency} colorize />
                  <Typography.Text type="secondary"> {copy.change} {percentLabel(row.profit_change)}</Typography.Text>
                </div>
              ))}
            </CostGuard>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <CostGuard fallback={<Typography.Text type="secondary">{copy.costHidden}</Typography.Text>}>
              <Typography.Text strong>
                <Link to="/analytics/costs">{copy.netMargin}</Link>
              </Typography.Text>
              {(overview.data?.currencies ?? []).map((row) => (
                <div key={row.currency}>{row.currency} {percentLabel(row.net_margin)}</div>
              ))}
            </CostGuard>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <CostGuard fallback={<Typography.Text type="secondary">{copy.costHidden}</Typography.Text>}>
              <Typography.Text strong>
                <Link to="/analytics/ads">{copy.roas}</Link>
              </Typography.Text>
              {(overview.data?.currencies ?? []).map((row) => (
                <div key={row.currency}>{row.currency} {row.roas ?? '—'}</div>
              ))}
            </CostGuard>
          </Card>
        </Col>
      </Row>
      <Typography.Paragraph type="secondary">
        {copy.yoy} {percentLabel(overview.data?.order_yoy)} · {copy.returnRate} {percentLabel(overview.data?.return_rate)}
      </Typography.Paragraph>
    </Space>
  );
}
