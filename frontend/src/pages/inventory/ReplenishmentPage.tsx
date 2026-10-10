import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Table, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { purchaseApi } from '@/api/purchase';
import { Perm, type ReplenishmentView, type SafetyStockView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { formatDate } from '@/utils/format';

const copy = zhCN.inventoryPage;

const PLATFORMS = [
  { value: 'amazon', label: 'Amazon' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'lazada', label: 'Lazada' },
  { value: 'tiktok', label: 'TikTok' },
];

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

interface DraftForm {
  warehouse_id: string;
  supplier_id?: string;
  currency: string;
  unit_price?: string;
}

export function ReplenishmentPage() {
  const client = useQueryClient();
  const [windowDays, setWindowDays] = useState(30);
  const [selected, setSelected] = useState<string[]>([]);
  const [draftForm] = Form.useForm<DraftForm>();
  const warehouses = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.listWarehouses({ limit: 100 }),
  });
  const convert = useMutation({
    mutationFn: (values: DraftForm) =>
      purchaseApi.fromReplenishment({
        sku_ids: selected,
        warehouse_id: values.warehouse_id,
        supplier_id: values.supplier_id || null,
        currency: values.currency,
        window_days: windowDays,
        unit_price: values.unit_price || null,
      }),
    onSuccess: () => feedback().message.success(copy.purchaseDraftCreated),
    onError: (error) => feedback().message.error(messageOf(error)),
  });
  const safety = useQuery({
    queryKey: ['safety-stocks'],
    queryFn: () => inventoryApi.listSafety({ limit: 100 }),
  });
  const suggestions = useQuery({
    queryKey: ['replenishments', windowDays],
    queryFn: () => inventoryApi.replenishments(windowDays),
  });
  const save = useMutation({
    mutationFn: inventoryApi.saveSafety,
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      await client.invalidateQueries({ queryKey: ['safety-stocks'] });
      await client.invalidateQueries({ queryKey: ['replenishments'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.replenishmentTitle}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.replenishmentDescription}</Typography.Paragraph>
        <PermissionGuard permission={Perm.INVENTORY_WRITE}>
          <Form
            layout="inline"
            initialValues={{ platform_code: 'shopee', quantity: 2, lead_time_days: 7, cover_days: 14 }}
            onFinish={(values: {
              sku_id: string;
              platform_code: string;
              quantity: number;
              lead_time_days: number;
              cover_days: number;
            }) => {
              save.mutate({
                sku_id: values.sku_id.trim(),
                platform_code: values.platform_code,
                quantity: values.quantity,
                lead_time_days: values.lead_time_days,
                cover_days: values.cover_days,
              });
            }}
          >
            <Form.Item name="sku_id" rules={[{ required: true, message: copy.sku }]}>
              <Input placeholder={copy.sku} style={{ width: 220 }} />
            </Form.Item>
            <Form.Item name="platform_code">
              <Select style={{ width: 140 }} options={PLATFORMS} />
            </Form.Item>
            <Form.Item name="quantity" rules={[{ required: true }]}>
              <InputNumber min={0} placeholder={copy.platformSafety} />
            </Form.Item>
            <Form.Item name="lead_time_days" rules={[{ required: true }]}>
              <InputNumber min={0} placeholder={copy.leadTime} />
            </Form.Item>
            <Form.Item name="cover_days" rules={[{ required: true }]}>
              <InputNumber min={0} placeholder={copy.coverDays} />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={save.isPending}>
                {copy.saveSafety}
              </Button>
            </Form.Item>
          </Form>
        </PermissionGuard>
      </Card>
      {safety.isError ? <Alert type="error" message={messageOf(safety.error)} /> : null}
      <Card title={copy.platformSafety}>
        <Table<SafetyStockView>
          rowKey="id"
          loading={safety.isLoading}
          dataSource={safety.data?.items ?? []}
          pagination={false}
          columns={[
            { title: copy.sku, dataIndex: 'sku_code' },
            { title: copy.platform, dataIndex: 'platform_code' },
            { title: copy.platformSafety, dataIndex: 'quantity' },
            { title: copy.leadTime, dataIndex: 'lead_time_days' },
            { title: copy.coverDays, dataIndex: 'cover_days' },
          ]}
        />
      </Card>
      <Card
        title={copy.suggested}
        extra={
          <Space>
            <Typography.Text>{copy.window}</Typography.Text>
            <InputNumber min={1} max={365} value={windowDays} onChange={(value) => setWindowDays(value ?? 30)} />
          </Space>
        }
      >
        {suggestions.isError ? <Alert type="error" message={messageOf(suggestions.error)} /> : null}
        <PermissionGuard permission={Perm.PURCHASE_WRITE}>
          <Form<DraftForm> form={draftForm} layout="inline" onFinish={(values) => convert.mutate(values)}>
            <Form.Item name="warehouse_id" label={copy.warehouse} rules={[{ required: true }]}>
              <Select
                style={{ width: 160 }}
                options={(warehouses.data?.items ?? [])
                  .filter((row) => row.warehouse_type !== 'PLATFORM')
                  .map((row) => ({ value: row.id, label: row.name }))}
              />
            </Form.Item>
            <Form.Item name="supplier_id" label={copy.purchaseSupplierOptional}>
              <Input />
            </Form.Item>
            <Form.Item name="currency" label={copy.purchaseCurrency} rules={[{ required: true }]}>
              <Input maxLength={3} style={{ width: 80 }} />
            </Form.Item>
            <Form.Item name="unit_price" label={copy.purchaseUnitPriceOptional}>
              <Input />
            </Form.Item>
            <Button htmlType="submit" disabled={selected.length === 0} loading={convert.isPending}>
              {copy.createPurchaseDraft}
            </Button>
          </Form>
        </PermissionGuard>
        <Table<ReplenishmentView>
          rowKey={(row) => `${row.sku_id}-${row.platform_code}`}
          loading={suggestions.isLoading}
          dataSource={suggestions.data ?? []}
          pagination={false}
          rowSelection={{
            selectedRowKeys: (suggestions.data ?? [])
              .filter((row) => selected.includes(row.sku_id))
              .map((row) => `${row.sku_id}-${row.platform_code}`),
            onChange: (_, rows) => setSelected([...new Set(rows.map((row) => row.sku_id))]),
          }}
          columns={[
            { title: copy.sku, dataIndex: 'sku_code' },
            { title: copy.platform, dataIndex: 'platform_code' },
            { title: copy.sold, dataIndex: 'sold' },
            { title: copy.daily, dataIndex: 'daily_sales' },
            { title: copy.movable, dataIndex: 'movable' },
            { title: copy.inTransit, dataIndex: 'in_transit' },
            { title: copy.suggested, dataIndex: 'suggested_qty' },
            {
              title: copy.orderOn,
              dataIndex: 'order_on',
              render: (value: string | null) => formatDate(value),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
