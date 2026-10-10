import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Form, Input, Row, Space, Statistic, Table, Typography } from 'antd';
import { useState } from 'react';

import { adsApi } from '@/api/ads';
import { ApiError } from '@/api/client';
import type { AdsSkuProfitView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard } from '@/components/PermissionGuard';
import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';

import { AdsFilters } from './filters';
import { adsQuery, defaultAdsRange, type AdsFilterValues } from './query';

const copy = zhCN.adsPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function AdsOverviewPage() {
  const client = useQueryClient();
  const { canViewCost } = usePermission();
  const [filter] = Form.useForm<AdsFilterValues>();
  const [syncForm] = Form.useForm<{ shop_id: string }>();
  const [values, setValues] = useState<AdsFilterValues>({ range: defaultAdsRange() });
  const query = adsQuery(values);
  const overview = useQuery({
    queryKey: ['ads-overview', query],
    queryFn: () => adsApi.overview(query),
  });
  const skuProfit = useQuery({
    queryKey: ['ads-sku-profit', query],
    queryFn: () => adsApi.skuProfit(query),
    enabled: canViewCost,
  });
  const sync = useMutation({
    mutationFn: (shopId: string) =>
      adsApi.sync({ shop_id: shopId, date_from: query.date_from, date_to: query.date_to }),
    onSuccess: async (result) => {
      feedback().message.success(copy.synced.replace('{{count}}', String(result.campaigns)));
      await client.invalidateQueries({ queryKey: ['ads-overview'] });
      await client.invalidateQueries({ queryKey: ['ads-campaigns'] });
      await client.invalidateQueries({ queryKey: ['ads-keywords'] });
      await client.invalidateQueries({ queryKey: ['ads-loss'] });
      await client.invalidateQueries({ queryKey: ['ads-sku-profit'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.overviewTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.overviewDescription}</Typography.Paragraph>
      </div>
      <Card>
        <Form
          form={filter}
          layout="inline"
          initialValues={{ range: values.range }}
          onFinish={(next: AdsFilterValues) => setValues(next)}
        >
          <AdsFilters />
          <Form.Item>
            <Button type="primary" htmlType="submit">
              {zhCN.common.search}
            </Button>
          </Form.Item>
        </Form>
      </Card>
      <Card title={copy.sync}>
        <Form form={syncForm} layout="inline" onFinish={(next) => sync.mutate(next.shop_id.trim())}>
          <Form.Item name="shop_id" label={copy.shopId} rules={[{ required: true, message: copy.shopId }]}>
            <Input style={{ width: 220 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={sync.isPending}>
              {copy.sync}
            </Button>
          </Form.Item>
        </Form>
      </Card>
      {overview.isError ? <Alert type="error" message={messageOf(overview.error)} /> : null}
      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title={copy.impressions} value={overview.data?.impressions ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title={copy.clicks} value={overview.data?.clicks ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title={copy.ctr} value={overview.data?.ctr ?? '—'} suffix={overview.data?.ctr ? '%' : undefined} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title={copy.cvr} value={overview.data?.cvr ?? '—'} suffix={overview.data?.cvr ? '%' : undefined} />
          </Card>
        </Col>
      </Row>
      <CostGuard fallback={<Alert type="info" message={copy.costHidden} />}>
        <Table
          rowKey="currency"
          loading={overview.isLoading}
          dataSource={overview.data?.totals ?? []}
          pagination={false}
          columns={[
            { title: copy.currency, dataIndex: 'currency' },
            {
              title: copy.spend,
              dataIndex: 'spend',
              render: (value: string | null, row) => <MoneyText value={value} currency={row.currency} showSymbol={false} />,
            },
            {
              title: copy.sales,
              dataIndex: 'sales',
              render: (value: string | null, row) => <MoneyText value={value} currency={row.currency} showSymbol={false} />,
            },
            {
              title: copy.acos,
              dataIndex: 'acos',
              render: (value: string | null) => <MoneyText value={value} showSymbol={false} suffix="%" />,
            },
            {
              title: copy.roas,
              dataIndex: 'roas',
              render: (value: string | null) => <MoneyText value={value} showSymbol={false} />,
            },
          ]}
        />
      </CostGuard>
      {canViewCost ? (
        <Card title={copy.skuProfitTitle}>
          <Typography.Paragraph type="secondary">{copy.skuProfitDescription}</Typography.Paragraph>
          {skuProfit.isError ? <Alert type="error" message={messageOf(skuProfit.error)} /> : null}
          <Table<AdsSkuProfitView>
            rowKey={(row) => `${row.sku_id ?? 'none'}-${row.currency}`}
            loading={skuProfit.isLoading}
            dataSource={skuProfit.data ?? []}
            pagination={false}
            columns={[
              { title: copy.sku, dataIndex: 'sku_id', render: (value: string | null) => value ?? copy.unlinked },
              { title: copy.currency, dataIndex: 'currency' },
              {
                title: copy.actualSpend,
                dataIndex: 'actual_spend',
                render: (value: string, row) => <MoneyText value={value} currency={row.currency} showSymbol={false} />,
              },
              {
                title: copy.netAfterAds,
                dataIndex: 'net_after_ads',
                render: (value: string | null, row) => (
                  <MoneyText value={value} currency={row.currency} showSymbol={false} colorize />
                ),
              },
              { title: copy.formula, dataIndex: 'formula' },
              {
                title: copy.complete,
                dataIndex: 'complete',
                render: (value: boolean) => (value ? copy.completeYes : copy.completeNo),
              },
            ]}
          />
        </Card>
      ) : null}
    </Space>
  );
}
