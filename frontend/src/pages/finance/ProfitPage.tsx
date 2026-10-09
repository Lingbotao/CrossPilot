import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, DatePicker, Form, Input, Select, Space, Table, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import dayjs from 'dayjs';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { profitApi } from '@/api/profit';
import {
  CONTENT_MARKETS,
  FIRST_MILE_METHODS,
  LANDED_CHANNELS,
  PROFIT_GRAINS,
  Perm,
  type ProfitRowView,
} from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.profitPage;
const landed = zhCN.landedCostPage;

interface FilterForm {
  range: [Dayjs, Dayjs];
  grain: string;
  currency?: string;
  market: string;
  channel: string;
  first_mile_method: string;
  book_currency: string;
  storage_days?: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

export function ProfitPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FilterForm>();
  const [query, setQuery] = useState({
    date_from: dayjs().subtract(6, 'day').format('YYYY-MM-DD'),
    date_to: dayjs().format('YYYY-MM-DD'),
    grain: 'day',
    currency: '',
  });
  const rows = useQuery({
    queryKey: ['sku-profit', query],
    queryFn: () =>
      profitApi.sku({
        date_from: query.date_from,
        date_to: query.date_to,
        grain: query.grain,
        currency: query.currency || undefined,
      }),
  });
  const rebuild = useMutation({
    mutationFn: (values: FilterForm) => {
      const days = values.storage_days?.trim() ?? '';
      return profitApi.materialize({
        market: values.market,
        channel: values.channel,
        first_mile_method: values.first_mile_method,
        date_from: values.range[0].format('YYYY-MM-DD'),
        date_to: values.range[1].format('YYYY-MM-DD'),
        book_currency: values.book_currency.trim().toUpperCase(),
        storage_days: days === '' ? null : Number(days),
      });
    },
    onSuccess: async (data) => {
      feedback().message.success(`${copy.rebuilt}：${data.rows}，${copy.unmatched} ${data.unmatched_items}，${copy.incomplete} ${data.incomplete_rows}`);
      await queryClient.invalidateQueries({ queryKey: ['sku-profit'] });
    },
  });

  return (
    <CostGuard fallback={<Typography.Text type="secondary">无权限查看成本与利润</Typography.Text>}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Typography.Title level={3}>{copy.title}</Typography.Title>
        <Alert type="info" message={copy.hint} />
        <Form<FilterForm>
          form={form}
          layout="vertical"
          initialValues={{
            range: [dayjs().subtract(6, 'day'), dayjs()],
            grain: 'day',
            book_currency: 'CNY',
            channel: 'AIR',
            first_mile_method: 'CHARGEABLE',
          }}
          onFinish={(values) => {
            setQuery({
              date_from: values.range[0].format('YYYY-MM-DD'),
              date_to: values.range[1].format('YYYY-MM-DD'),
              grain: values.grain,
              currency: values.currency?.trim().toUpperCase() ?? '',
            });
          }}
        >
          <Space wrap align="start">
            <Form.Item name="range" label={`${copy.from} / ${copy.to}`} rules={[{ required: true }]}>
              <DatePicker.RangePicker />
            </Form.Item>
            <Form.Item name="grain" label={copy.grain} rules={[{ required: true }]}>
              <Select style={{ width: 120 }} options={PROFIT_GRAINS.map((item) => ({ value: item, label: copy.grains[item] }))} />
            </Form.Item>
            <Form.Item name="currency" label={landed.currency}>
              <Input style={{ width: 100 }} />
            </Form.Item>
            <Form.Item name="market" label={landed.market} rules={[{ required: true }]}>
              <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((item) => ({ value: item, label: item }))} />
            </Form.Item>
            <Form.Item name="channel" label={landed.channel} rules={[{ required: true }]}>
              <Select style={{ width: 160 }} options={LANDED_CHANNELS.map((item) => ({ value: item, label: landed.channels[item] }))} />
            </Form.Item>
            <Form.Item name="first_mile_method" label={landed.method} rules={[{ required: true }]}>
              <Select style={{ width: 180 }} options={FIRST_MILE_METHODS.map((item) => ({ value: item, label: landed.methods[item] }))} />
            </Form.Item>
            <Form.Item name="book_currency" label={copy.bookCurrency} rules={[{ required: true }]}>
              <Input style={{ width: 100 }} />
            </Form.Item>
            <Form.Item name="storage_days" label={landed.storageDays}>
              <Input style={{ width: 120 }} />
            </Form.Item>
          </Space>
          <Space>
            <Button type="primary" htmlType="submit">
              {zhCN.common.search ?? '查询'}
            </Button>
            <PermissionGuard permission={Perm.FINANCE_WRITE}>
              <Button loading={rebuild.isPending} onClick={() => rebuild.mutate(form.getFieldsValue(true) as FilterForm)}>
                {copy.rebuild}
              </Button>
            </PermissionGuard>
          </Space>
        </Form>
        {rows.isError ? <Alert type="error" message={messageOf(rows.error)} /> : null}
        {rebuild.isError ? <Alert type="error" message={messageOf(rebuild.error)} /> : null}
        <Table<ProfitRowView>
          rowKey={(row) => `${row.sku_id}-${row.shop_id}-${row.period_start}-${row.currency}`}
          loading={rows.isLoading}
          pagination={false}
          dataSource={rows.data ?? []}
          locale={{ emptyText: copy.empty }}
          columns={[
            { title: copy.sku, dataIndex: 'sku_id' },
            { title: copy.shop, dataIndex: 'shop_id' },
            {
              title: copy.period,
              render: (_, row) => `${row.period_start} ~ ${row.period_end}`,
            },
            { title: copy.quantity, dataIndex: 'quantity' },
            { title: copy.revenue, render: (_, row) => <MoneyText value={row.revenue} currency={row.currency} /> },
            { title: copy.cost, render: (_, row) => <MoneyText value={row.cost_total} currency={row.currency} /> },
            {
              title: copy.net,
              render: (_, row) => <MoneyText value={row.net_profit} currency={row.currency} colorize />,
            },
            {
              title: copy.margin,
              dataIndex: 'net_margin_percent',
              render: (value: string | null) => (value ? `${value}%` : '—'),
            },
            {
              title: copy.bookNet,
              render: (_, row) => <MoneyText value={row.book_net_profit} currency={row.book_currency} colorize />,
            },
            { title: copy.fx, render: (_, row) => <MoneyText value={row.fx_gain} currency={row.book_currency} colorize /> },
          ]}
        />
      </Space>
    </CostGuard>
  );
}
