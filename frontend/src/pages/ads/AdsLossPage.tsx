import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { adsApi } from '@/api/ads';
import { ApiError } from '@/api/client';
import type { AdsLossView } from '@/api/types';
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

export function AdsLossPage() {
  const { canViewCost } = usePermission();
  const [filter] = Form.useForm<AdsFilterValues>();
  const [values, setValues] = useState<AdsFilterValues>({ range: defaultAdsRange() });
  const query = adsQuery(values);
  const loss = useQuery({
    queryKey: ['ads-loss', query],
    queryFn: () => adsApi.loss({ ...query, limit: 50 }),
    enabled: canViewCost,
  });

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.lossTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.lossDescription}</Typography.Paragraph>
      </div>
      <CostGuard fallback={<Alert type="info" message={copy.costHidden} />}>
        <Space direction="vertical" size={16} style={{ display: 'flex' }}>
          <Card>
            <Form form={filter} layout="inline" initialValues={{ range: values.range }} onFinish={setValues}>
              <AdsFilters />
              <Form.Item>
                <Button type="primary" htmlType="submit">
                  {zhCN.common.search}
                </Button>
              </Form.Item>
            </Form>
          </Card>
          {loss.isError ? <Alert type="error" message={messageOf(loss.error)} /> : null}
          <Table<AdsLossView>
            rowKey="id"
            loading={loss.isLoading}
            dataSource={loss.data?.items ?? []}
            locale={{ emptyText: copy.emptyLoss }}
            columns={[
              { title: copy.name, dataIndex: 'campaign_name' },
              { title: copy.platform, dataIndex: 'platform_code' },
              { title: copy.date, dataIndex: 'stat_date' },
              {
                title: copy.spend,
                dataIndex: 'spend',
                render: (value: string, row) => <MoneyText value={value} currency={row.currency} showSymbol={false} />,
              },
              {
                title: copy.sales,
                dataIndex: 'sales',
                render: (value: string, row) => <MoneyText value={value} currency={row.currency} showSymbol={false} />,
              },
              {
                title: copy.acos,
                dataIndex: 'acos',
                render: (value: string | null) => (
                  <Tag color="red">
                    <MoneyText value={value} showSymbol={false} suffix="%" />
                  </Tag>
                ),
              },
              {
                title: copy.grossMargin,
                dataIndex: 'gross_margin',
                render: (value: string | null) => <MoneyText value={value} showSymbol={false} suffix="%" />,
              },
              {
                title: copy.suggestion,
                dataIndex: 'suggestion_code',
                render: (value: string) => copy.suggestions[value as keyof typeof copy.suggestions] ?? value,
              },
            ]}
          />
        </Space>
      </CostGuard>
    </Space>
  );
}
