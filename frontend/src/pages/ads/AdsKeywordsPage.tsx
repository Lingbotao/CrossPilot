import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Space, Switch, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { adsApi } from '@/api/ads';
import { ApiError } from '@/api/client';
import type { AdsKeywordView } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';

import { AdsFilters } from './filters';
import { adsQuery, defaultAdsRange, type AdsFilterValues } from './query';

const copy = zhCN.adsPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function AdsKeywordsPage() {
  const { canViewCost } = usePermission();
  const [filter] = Form.useForm<AdsFilterValues>();
  const [values, setValues] = useState<AdsFilterValues>({ range: defaultAdsRange() });
  const [onlyNegative, setOnlyNegative] = useState(true);
  const query = adsQuery(values);
  const keywords = useQuery({
    queryKey: ['ads-keywords', query, onlyNegative],
    queryFn: () => adsApi.keywords({ ...query, limit: 50, only_negative: onlyNegative }),
  });

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.keywordsTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.keywordsDescription}</Typography.Paragraph>
      </div>
      <Card>
        <Form form={filter} layout="inline" initialValues={{ range: values.range }} onFinish={setValues}>
          <AdsFilters />
          <Form.Item label={copy.onlyNegative}>
            <Switch checked={onlyNegative} onChange={setOnlyNegative} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">
              {zhCN.common.search}
            </Button>
          </Form.Item>
        </Form>
      </Card>
      {keywords.isError ? <Alert type="error" message={messageOf(keywords.error)} /> : null}
      <Table<AdsKeywordView>
        rowKey="id"
        loading={keywords.isLoading}
        dataSource={keywords.data?.items ?? []}
        locale={{ emptyText: copy.emptyKeywords }}
        columns={[
          { title: copy.keyword, dataIndex: 'keyword' },
          { title: copy.name, dataIndex: 'campaign_name' },
          { title: copy.platform, dataIndex: 'platform_code' },
          { title: copy.date, dataIndex: 'stat_date' },
          { title: copy.clicks, dataIndex: 'clicks' },
          { title: copy.orders, dataIndex: 'orders' },
          ...(canViewCost
            ? [
                {
                  title: copy.spend,
                  dataIndex: 'spend',
                  render: (value: string | null, row: AdsKeywordView) => (
                    <MoneyText value={value} currency={row.currency} showSymbol={false} />
                  ),
                },
              ]
            : []),
          {
            title: copy.negative,
            dataIndex: 'suggest_negative',
            render: (value: boolean) => (value ? <Tag color="orange">{copy.negative}</Tag> : copy.keep),
          },
        ]}
      />
    </Space>
  );
}
