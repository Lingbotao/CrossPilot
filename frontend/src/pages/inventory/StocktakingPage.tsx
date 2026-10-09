import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { Perm, type StockTakingStatus, type StockTakingView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { formatDateTime } from '@/utils/format';

const copy = zhCN.inventoryPage;

const STATUS_LABEL: Record<StockTakingStatus, string> = {
  DRAFT: copy.draft,
  POSTED: copy.posted,
  CANCELLED: copy.cancelled,
};

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

function CountEditor({ row }: { row: StockTakingView }) {
  const client = useQueryClient();
  const [counts, setCounts] = useState<Record<string, number | null>>(
    Object.fromEntries(row.lines.map((line) => [line.sku_id, line.counted_qty])),
  );

  const countedLines = () => {
    const lines = [];
    for (const line of row.lines) {
      const counted = counts[line.sku_id];
      if (counted === null || counted === undefined) {
        return null;
      }
      lines.push({ sku_id: line.sku_id, counted_qty: counted });
    }
    return lines;
  };

  const save = useMutation({
    mutationFn: () => {
      const lines = countedLines();
      if (lines === null) {
        throw new Error(copy.countsRequired);
      }
      return inventoryApi.recordCounts(row.id, lines);
    },
    onSuccess: async () => {
      feedback().message.success(copy.countsSaved);
      await client.invalidateQueries({ queryKey: ['inventory-takings'] });
    },
    onError: (error) => feedback().message.error(error instanceof Error ? error.message : messageOf(error)),
  });

  const post = useMutation({
    mutationFn: () => inventoryApi.postTaking(row.id),
    onSuccess: async () => {
      feedback().message.success(copy.takingPosted);
      await client.invalidateQueries({ queryKey: ['inventory-takings'] });
      await client.invalidateQueries({ queryKey: ['inventories'] });
      await client.invalidateQueries({ queryKey: ['inventory-flows'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Table
        rowKey="sku_id"
        pagination={false}
        dataSource={row.lines}
        columns={[
          { title: copy.sku, dataIndex: 'sku_code' },
          { title: copy.bookQty, dataIndex: 'book_qty' },
          {
            title: copy.countedQty,
            render: (_, line) => (
              <InputNumber
                min={0}
                value={counts[line.sku_id]}
                disabled={row.status !== 'DRAFT'}
                onChange={(value) => setCounts((current) => ({ ...current, [line.sku_id]: value }))}
              />
            ),
          },
        ]}
      />
      {row.status === 'DRAFT' ? (
        <PermissionGuard permission={Perm.INVENTORY_WRITE}>
          <Space>
            <Button loading={save.isPending} onClick={() => save.mutate()}>
              {copy.saveCounts}
            </Button>
            <Button
              type="primary"
              loading={post.isPending}
              onClick={() => {
                Modal.confirm({
                  title: copy.postConfirm,
                  onOk: async () => {
                    await save.mutateAsync();
                    await post.mutateAsync();
                  },
                });
              }}
            >
              {copy.postTaking}
            </Button>
          </Space>
        </PermissionGuard>
      ) : null}
      {row.status === 'POSTED' ? (
        <Typography.Text type="secondary">
          {copy.gain} {row.diff_summary.gain_qty ?? 0} · {copy.loss} {row.diff_summary.loss_qty ?? 0}
        </Typography.Text>
      ) : null}
    </Space>
  );
}

export function StocktakingPage() {
  const client = useQueryClient();
  const [form] = Form.useForm<{ warehouse_id: string }>();
  const takings = useQuery({
    queryKey: ['inventory-takings'],
    queryFn: () => inventoryApi.listTakings({ limit: 50 }),
  });
  const warehouses = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.listWarehouses({ limit: 100 }),
  });

  const create = useMutation({
    mutationFn: (warehouseId: string) => inventoryApi.createTaking(warehouseId),
    onSuccess: async () => {
      feedback().message.success(copy.takingCreated);
      form.resetFields();
      await client.invalidateQueries({ queryKey: ['inventory-takings'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const cancel = useMutation({
    mutationFn: inventoryApi.cancelTaking,
    onSuccess: async () => {
      feedback().message.success(copy.takingCancelled);
      await client.invalidateQueries({ queryKey: ['inventory-takings'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.stocktakingTitle}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.stocktakingDescription}</Typography.Paragraph>
        <PermissionGuard permission={Perm.INVENTORY_WRITE}>
          <Form
            form={form}
            layout="inline"
            onFinish={(values: { warehouse_id: string }) => create.mutate(values.warehouse_id)}
          >
            <Form.Item name="warehouse_id" rules={[{ required: true, message: copy.warehouse }]}>
              <Select
                placeholder={copy.warehouse}
                style={{ width: 200 }}
                options={(warehouses.data?.items ?? []).map((row) => ({ value: row.id, label: row.name }))}
              />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={create.isPending}>
                {copy.createTaking}
              </Button>
            </Form.Item>
          </Form>
        </PermissionGuard>
      </Card>
      {takings.isError ? <Alert type="error" message={messageOf(takings.error)} /> : null}
      <Card>
        <Table<StockTakingView>
          rowKey="id"
          loading={takings.isLoading}
          dataSource={takings.data?.items ?? []}
          pagination={false}
          expandable={{
            expandedRowRender: (row) => <CountEditor key={`${row.id}-${row.status}`} row={row} />,
          }}
          columns={[
            { title: copy.warehouse, dataIndex: 'warehouse_name' },
            {
              title: copy.status,
              dataIndex: 'status',
              render: (value: StockTakingStatus) => <Tag>{STATUS_LABEL[value] ?? value}</Tag>,
            },
            { title: copy.lines, dataIndex: 'lines', render: (lines: StockTakingView['lines']) => lines.length },
            { title: copy.when, dataIndex: 'created_at', render: (value: string) => formatDateTime(value) },
            {
              title: zhCN.common.actions,
              render: (_, row) =>
                row.status === 'DRAFT' ? (
                  <PermissionGuard permission={Perm.INVENTORY_WRITE}>
                    <Button type="link" loading={cancel.isPending} onClick={() => cancel.mutate(row.id)}>
                      {copy.cancel}
                    </Button>
                  </PermissionGuard>
                ) : null,
            },
          ]}
        />
      </Card>
    </Space>
  );
}
