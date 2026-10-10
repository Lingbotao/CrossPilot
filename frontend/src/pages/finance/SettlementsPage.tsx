import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, DatePicker, Form, Input, Select, Space, Table, Typography, Upload } from 'antd';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { listingsApi } from '@/api/listings';
import { profitApi } from '@/api/profit';
import { Perm, type SettlementDetail, type SettlementGapView, type SettlementSummary } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.settlementPage;

interface ImportForm {
  shop_id: string;
  platform_settlement_id: string;
  period_start: Dayjs;
  period_end: Dayjs;
  currency: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

export function SettlementsPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<ImportForm>();
  const [file, setFile] = useState<File | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const shops = useQuery({ queryKey: ['listing-shops'], queryFn: listingsApi.shops });
  const rows = useQuery({ queryKey: ['settlements'], queryFn: profitApi.settlements });
  const detail = useQuery({
    queryKey: ['settlement', selected],
    queryFn: () => profitApi.settlement(selected ?? ''),
    enabled: Boolean(selected),
  });
  const upload = useMutation({
    mutationFn: (values: ImportForm) => {
      if (!file) {
        return Promise.reject(new Error(copy.file));
      }
      return profitApi.importSettlement({
        shop_id: values.shop_id,
        platform_settlement_id: values.platform_settlement_id.trim(),
        period_start: values.period_start.format('YYYY-MM-DD'),
        period_end: values.period_end.format('YYYY-MM-DD'),
        currency: values.currency.trim().toUpperCase(),
        file,
      });
    },
    onSuccess: async (data) => {
      feedback().message.success(copy.imported);
      setSelected(data.id);
      setFile(null);
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['settlements'] });
    },
  });

  return (
    <CostGuard fallback={<Typography.Text type="secondary">{zhCN.common.noPermission}</Typography.Text>}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {copy.title}
        </Typography.Title>
        <Alert type="info" showIcon message={copy.hint} />
        <Card>
          <Table<SettlementSummary>
            rowKey="id"
            loading={rows.isLoading}
            dataSource={rows.data ?? []}
            pagination={false}
            locale={{ emptyText: copy.empty }}
            columns={[
              { title: copy.settlementNo, dataIndex: 'platform_settlement_id' },
              { title: copy.period, render: (_, row) => `${row.period_start} – ${row.period_end}` },
              {
                title: copy.amount,
                render: (_, row) => <MoneyText value={row.amount} currency={row.currency} />,
              },
              {
                title: copy.matchRate,
                dataIndex: 'match_rate',
                render: (value: string) => value,
              },
              {
                title: copy.lines,
                render: (_, row) => `${row.matched_count}/${row.line_count}`,
              },
              { title: copy.source, dataIndex: 'source' },
              {
                title: zhCN.common.actions,
                render: (_, row) => (
                  <Button size="small" type={selected === row.id ? 'primary' : 'default'} onClick={() => setSelected(row.id)}>
                    {copy.detail}
                  </Button>
                ),
              },
            ]}
          />
        </Card>
        {detail.data ? <DetailCard data={detail.data} /> : null}
        <PermissionGuard permission={Perm.FINANCE_WRITE}>
          <Card title={copy.import}>
            <Form<ImportForm> form={form} layout="vertical" onFinish={(values) => upload.mutate(values)}>
              <Space wrap align="start">
                <Form.Item name="shop_id" label={copy.shop} rules={[{ required: true }]}>
                  <Select
                    style={{ width: 240 }}
                    options={(shops.data ?? []).map((shop) => ({
                      value: shop.id,
                      label: `${shop.shop_name} · ${shop.platform_code}`,
                    }))}
                  />
                </Form.Item>
                <Form.Item name="platform_settlement_id" label={copy.settlementNo} rules={[{ required: true }]}>
                  <Input style={{ width: 200 }} />
                </Form.Item>
                <Form.Item name="period_start" label={copy.from} rules={[{ required: true }]}>
                  <DatePicker />
                </Form.Item>
                <Form.Item name="period_end" label={copy.to} rules={[{ required: true }]}>
                  <DatePicker />
                </Form.Item>
                <Form.Item name="currency" label={copy.currency} rules={[{ required: true }]}>
                  <Input style={{ width: 100 }} />
                </Form.Item>
              </Space>
              <Form.Item label={copy.file} required>
                <Upload
                  maxCount={1}
                  beforeUpload={(next) => {
                    setFile(next);
                    return false;
                  }}
                  onRemove={() => setFile(null)}
                  fileList={file ? [{ uid: file.name, name: file.name }] : []}
                >
                  <Button>{copy.file}</Button>
                </Upload>
              </Form.Item>
              {upload.isError ? <Alert type="error" message={messageOf(upload.error)} /> : null}
              <Button type="primary" htmlType="submit" loading={upload.isPending}>
                {copy.import}
              </Button>
            </Form>
          </Card>
        </PermissionGuard>
      </Space>
    </CostGuard>
  );
}

function DetailCard({ data }: { data: SettlementDetail }) {
  return (
    <Card title={copy.unmatchedTitle}>
      <Typography.Paragraph>
        {copy.matchRate} {data.match_rate} · {data.matched_count}/{data.line_count}
      </Typography.Paragraph>
      <Table<SettlementGapView>
        rowKey="platform_order_id"
        pagination={false}
        dataSource={data.gaps}
        columns={[
          { title: copy.order, dataIndex: 'platform_order_id' },
          {
            title: copy.status,
            render: (_, row) => (row.order_id ? copy.matchedLabel : copy.unmatchedLabel),
          },
          {
            title: copy.orderAmount,
            render: (_, row) => <MoneyText value={row.order_amount} currency={row.order_currency ?? undefined} />,
          },
          {
            title: copy.settlementAmount,
            render: (_, row) => (
              <MoneyText value={row.settlement_amount} currency={row.settlement_currency ?? undefined} />
            ),
          },
          {
            title: copy.deviation,
            render: (_, row) => (
              <MoneyText value={row.deviation} currency={row.order_currency ?? row.settlement_currency ?? undefined} />
            ),
          },
          { title: copy.note, dataIndex: 'note' },
        ]}
      />
    </Card>
  );
}
