import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { Perm, type InventoryAdjustKind, type InventoryPushStatus, type InventoryView, type TurnoverDimension, type TurnoverView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { formatDateTime } from '@/utils/format';

const copy = zhCN.inventoryPage;

const KIND_OPTIONS: { value: InventoryAdjustKind; label: string }[] = [
  { value: 'INBOUND', label: copy.inbound },
  { value: 'OUTBOUND', label: copy.outbound },
  { value: 'TO_DEFECTIVE', label: copy.toDefective },
  { value: 'ADJUST', label: copy.manualAdjust },
];

const PUSH_LABEL: Record<InventoryPushStatus, string> = {
  SUCCESS: copy.success,
  FAILED: copy.failed,
  LAGGED_ZERO: copy.lagged,
};

const PUSH_COLOR: Record<InventoryPushStatus, string> = {
  SUCCESS: 'green',
  FAILED: 'red',
  LAGGED_ZERO: 'orange',
};

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function OverviewPage() {
  const client = useQueryClient();
  const [skuId, setSkuId] = useState('');
  const [kind, setKind] = useState<InventoryAdjustKind>('INBOUND');
  const [dimension, setDimension] = useState<TurnoverDimension>('sku');
  const [windowDays, setWindowDays] = useState(30);
  const stocks = useQuery({
    queryKey: ['inventories'],
    queryFn: () => inventoryApi.list({ limit: 100 }),
  });
  const warehouses = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.listWarehouses({ limit: 100 }),
  });
  const logs = useQuery({
    queryKey: ['inventory-push-logs'],
    queryFn: () => inventoryApi.listPushLogs({ limit: 50 }),
  });
  const turnover = useQuery({
    queryKey: ['inventory-turnover', dimension, windowDays],
    queryFn: () => inventoryApi.turnover(dimension, windowDays),
  });

  const adjust = useMutation({
    mutationFn: inventoryApi.adjust,
    onSuccess: async () => {
      feedback().message.success(copy.adjusted);
      await client.invalidateQueries({ queryKey: ['inventories'] });
      await client.invalidateQueries({ queryKey: ['inventory-flows'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const push = useMutation({
    mutationFn: inventoryApi.push,
    onSuccess: async () => {
      feedback().message.success(copy.pushed);
      await client.invalidateQueries({ queryKey: ['inventory-push-logs'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const laggedSkus = new Set(
    (logs.data?.items ?? []).filter((row) => row.status === 'LAGGED_ZERO').map((row) => row.sku_id),
  );

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.overviewTitle}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.overviewDescription}</Typography.Paragraph>
        <PermissionGuard permission={Perm.INVENTORY_WRITE}>
          <Form
            layout="inline"
            onFinish={(values: { sku_id: string; warehouse_id: string; quantity: number }) => {
              adjust.mutate({
                sku_id: values.sku_id.trim(),
                warehouse_id: values.warehouse_id,
                kind,
                quantity: values.quantity,
              });
            }}
          >
            <Form.Item name="sku_id" rules={[{ required: true, message: copy.sku }]}>
              <Input
                placeholder={copy.sku}
                style={{ width: 220 }}
                onChange={(event) => setSkuId(event.target.value)}
              />
            </Form.Item>
            <Form.Item name="warehouse_id" rules={[{ required: true, message: copy.warehouse }]}>
              <Select
                placeholder={copy.warehouse}
                style={{ width: 180 }}
                options={(warehouses.data?.items ?? []).map((row) => ({ value: row.id, label: row.name }))}
              />
            </Form.Item>
            <Form.Item>
              <Select value={kind} style={{ width: 140 }} options={KIND_OPTIONS} onChange={setKind} />
            </Form.Item>
            <Form.Item name="quantity" rules={[{ required: true, message: copy.quantity }]}>
              <InputNumber placeholder={copy.quantity} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={adjust.isPending}>
                {copy.adjust}
              </Button>
            </Form.Item>
            <Form.Item>
              <Button
                disabled={skuId.trim().length === 0}
                loading={push.isPending}
                onClick={() => push.mutate(skuId.trim())}
              >
                {copy.push}
              </Button>
            </Form.Item>
          </Form>
        </PermissionGuard>
      </Card>
      {stocks.isError ? <Alert type="error" message={messageOf(stocks.error)} /> : null}
      <Card>
        <Table<InventoryView>
          rowKey="id"
          loading={stocks.isLoading}
          dataSource={stocks.data?.items ?? []}
          pagination={false}
          columns={[
            { title: copy.sku, dataIndex: 'sku_code' },
            { title: copy.warehouse, dataIndex: 'warehouse_name' },
            { title: copy.available, dataIndex: 'available' },
            { title: copy.occupied, dataIndex: 'occupied' },
            { title: copy.inTransit, dataIndex: 'in_transit' },
            { title: copy.defective, dataIndex: 'defective' },
            { title: copy.safeStock, dataIndex: 'safe_stock' },
            { title: copy.sellable, dataIndex: 'sellable' },
            {
              title: copy.pushStatus,
              render: (_, row) => (laggedSkus.has(row.sku_id) ? <Tag color="orange">{copy.lagged}</Tag> : '—'),
            },
          ]}
        />
      </Card>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.turnoverTitle}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.turnoverDescription}</Typography.Paragraph>
        <Space style={{ marginBottom: 16 }}>
          <Select<TurnoverDimension>
            value={dimension}
            style={{ width: 140 }}
            onChange={setDimension}
            options={[
              { value: 'sku', label: copy.dimensionSku },
              { value: 'warehouse', label: copy.dimensionWarehouse },
              { value: 'platform', label: copy.dimensionPlatform },
            ]}
          />
          <InputNumber min={1} max={365} value={windowDays} onChange={(value) => setWindowDays(value ?? 30)} />
        </Space>
        {turnover.isError ? <Alert type="error" message={messageOf(turnover.error)} /> : null}
        <Table<TurnoverView>
          rowKey={(row) => `${row.dimension}-${row.sku_id}-${row.warehouse_id ?? ''}-${row.platform_code ?? ''}`}
          loading={turnover.isLoading}
          dataSource={turnover.data ?? []}
          pagination={false}
          columns={[
            { title: copy.sku, dataIndex: 'sku_code' },
            { title: copy.warehouse, dataIndex: 'warehouse_name', render: (value: string | null) => value || '—' },
            { title: copy.platform, dataIndex: 'platform_code', render: (value: string | null) => value || '—' },
            { title: copy.onHand, dataIndex: 'on_hand' },
            { title: copy.sold, dataIndex: 'sold' },
            { title: copy.turnoverDays, dataIndex: 'turnover_days', render: (value: string | null) => value ?? '—' },
            {
              title: copy.dead,
              dataIndex: 'dead',
              render: (value: boolean) => (value ? <Tag color="orange">{copy.dead}</Tag> : '—'),
            },
            {
              title: copy.deadAmount,
              render: (_, row) => (
                <CostGuard>
                  {row.dead_stock_amount ? (
                    <MoneyText value={row.dead_stock_amount} currency={row.currency ?? 'CNY'} decimals={4} />
                  ) : (
                    '—'
                  )}
                </CostGuard>
              ),
            },
          ]}
        />
      </Card>
      <Card title={copy.pushStatus}>
        <Table
          rowKey="id"
          loading={logs.isLoading}
          dataSource={logs.data?.items ?? []}
          pagination={false}
          columns={[
            { title: copy.platform, dataIndex: 'platform_code' },
            { title: copy.sku, dataIndex: 'sku_id' },
            { title: copy.quantity, dataIndex: 'quantity' },
            {
              title: copy.pushStatus,
              dataIndex: 'status',
              render: (status: InventoryPushStatus) => <Tag color={PUSH_COLOR[status]}>{PUSH_LABEL[status]}</Tag>,
            },
            { title: copy.when, dataIndex: 'created_at', render: (value: string) => formatDateTime(value) },
          ]}
        />
      </Card>
    </Space>
  );
}
