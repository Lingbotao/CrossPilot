import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { adsApi } from '@/api/ads';
import { ApiError } from '@/api/client';
import type { AdsCampaignView, AdsDayView } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';

import { AdsFilters } from './filters';
import { adsQuery, defaultAdsRange, type AdsFilterValues } from './query';

const copy = zhCN.adsPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

function labelOf(map: Record<string, string>, value: string): string {
  return map[value] ?? value;
}

export function AdsCampaignsPage() {
  const { canViewCost } = usePermission();
  const [filter] = Form.useForm<AdsFilterValues>();
  const [values, setValues] = useState<AdsFilterValues>({ range: defaultAdsRange() });
  const [openId, setOpenId] = useState<string | null>(null);
  const query = adsQuery(values);
  const campaigns = useQuery({
    queryKey: ['ads-campaigns', query],
    queryFn: () => adsApi.campaigns({ ...query, limit: 50 }),
  });
  const detail = useQuery({
    queryKey: ['ads-campaign', openId, query],
    queryFn: () => adsApi.campaign(openId ?? '', query),
    enabled: openId !== null,
  });

  const moneyColumns = canViewCost
    ? [
        {
          title: copy.spend,
          dataIndex: 'spend',
          render: (value: string | null, row: AdsCampaignView) => (
            <MoneyText value={value} currency={row.currency} showSymbol={false} />
          ),
        },
        {
          title: copy.acos,
          dataIndex: 'acos',
          render: (value: string | null) => <MoneyText value={value} showSymbol={false} suffix="%" />,
        },
        {
          title: copy.loss,
          dataIndex: 'loss_flag',
          render: (value: boolean) => (value ? <Tag color="red">{copy.loss}</Tag> : copy.healthy),
        },
      ]
    : [];

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.campaignsTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.campaignsDescription}</Typography.Paragraph>
      </div>
      <Card>
        <Form
          form={filter}
          layout="inline"
          initialValues={{ range: values.range }}
          onFinish={(next: AdsFilterValues) => {
            setOpenId(null);
            setValues(next);
          }}
        >
          <AdsFilters />
          <Form.Item>
            <Button type="primary" htmlType="submit">
              {zhCN.common.search}
            </Button>
          </Form.Item>
        </Form>
      </Card>
      {campaigns.isError ? <Alert type="error" message={messageOf(campaigns.error)} /> : null}
      <Table<AdsCampaignView>
        rowKey="id"
        loading={campaigns.isLoading}
        dataSource={campaigns.data?.items ?? []}
        columns={[
          { title: copy.name, dataIndex: 'name' },
          { title: copy.platform, dataIndex: 'platform_code' },
          {
            title: copy.type,
            dataIndex: 'campaign_type',
            render: (value: string) => labelOf(copy.types, value),
          },
          {
            title: copy.status,
            dataIndex: 'status',
            render: (value: string) => labelOf(copy.statuses, value),
          },
          { title: copy.impressions, dataIndex: 'impressions' },
          { title: copy.clicks, dataIndex: 'clicks' },
          { title: copy.ctr, dataIndex: 'ctr', render: (value: string | null) => (value ? `${value}%` : '—') },
          ...moneyColumns,
          {
            title: zhCN.common.actions,
            render: (_value, row) => (
              <Button type="link" onClick={() => setOpenId(row.id)}>
                {copy.days}
              </Button>
            ),
          },
        ]}
      />
      {openId ? (
        <Card title={detail.data?.campaign.name ?? copy.days}>
          {detail.isError ? <Alert type="error" message={messageOf(detail.error)} /> : null}
          <Table<AdsDayView>
            rowKey="stat_date"
            loading={detail.isLoading}
            dataSource={detail.data?.days ?? []}
            pagination={false}
            columns={[
              { title: copy.date, dataIndex: 'stat_date' },
              { title: copy.impressions, dataIndex: 'impressions' },
              { title: copy.clicks, dataIndex: 'clicks' },
              { title: copy.orders, dataIndex: 'orders' },
              ...(canViewCost
                ? [
                    {
                      title: copy.spend,
                      dataIndex: 'spend',
                      render: (value: string | null) => (
                        <MoneyText value={value} currency={detail.data?.campaign.currency} showSymbol={false} />
                      ),
                    },
                    {
                      title: copy.acos,
                      dataIndex: 'acos',
                      render: (value: string | null) => <MoneyText value={value} showSymbol={false} suffix="%" />,
                    },
                    {
                      title: copy.grossMargin,
                      dataIndex: 'gross_margin',
                      render: (value: string | null) => <MoneyText value={value} showSymbol={false} suffix="%" />,
                    },
                    {
                      title: copy.suggestion,
                      dataIndex: 'suggestion_code',
                      render: (value: string) => labelOf(copy.suggestions, value),
                    },
                  ]
                : []),
            ]}
          />
        </Card>
      ) : null}
    </Space>
  );
}
