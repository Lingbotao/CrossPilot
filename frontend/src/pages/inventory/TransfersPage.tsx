import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography } from 'antd';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { Perm, type TransferStatus, type TransferView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { formatDateTime } from '@/utils/format';

const copy = zhCN.inventoryPage;

const STATUS_LABEL: Record<TransferStatus, string> = {
  DRAFT: copy.draft,
  IN_TRANSIT: copy.inTransitStatus,
  RECEIVED: copy.received,
  CANCELLED: copy.cancelled,
};

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

interface TransferForm {
  from_warehouse_id: string;
  to_warehouse_id: string;
  lines: { sku_id: string; quantity: number }[];
}

export function TransfersPage() {
  const client = useQueryClient();
  const [form] = Form.useForm<TransferForm>();
  const transfers = useQuery({
    queryKey: ['inventory-transfers'],
    queryFn: () => inventoryApi.listTransfers({ limit: 50 }),
  });
  const warehouses = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.listWarehouses({ limit: 100 }),
  });
  const stocks = useQuery({
    queryKey: ['inventories'],
    queryFn: () => inventoryApi.list({ limit: 100 }),
  });

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['inventory-transfers'] });
    await client.invalidateQueries({ queryKey: ['inventories'] });
    await client.invalidateQueries({ queryKey: ['inventory-flows'] });
  };

  const create = useMutation({
    mutationFn: inventoryApi.createTransfer,
    onSuccess: async () => {
      feedback().message.success(copy.transferCreated);
      form.resetFields();
      await refresh();
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const ship = useMutation({
    mutationFn: inventoryApi.shipTransfer,
    onSuccess: async () => {
      feedback().message.success(copy.transferShipped);
      await refresh();
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const receive = useMutation({
    mutationFn: inventoryApi.receiveTransfer,
    onSuccess: async () => {
      feedback().message.success(copy.transferReceived);
      await refresh();
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const cancel = useMutation({
    mutationFn: inventoryApi.cancelTransfer,
    onSuccess: async () => {
      feedback().message.success(copy.transferCancelled);
      await refresh();
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const warehouseOptions = (warehouses.data?.items ?? []).map((row) => ({ value: row.id, label: row.name }));
  const seen = new Set<string>();
  const skuOptions = (stocks.data?.items ?? []).flatMap((row) => {
    if (seen.has(row.sku_id)) {
      return [];
    }
    seen.add(row.sku_id);
    return [{ value: row.sku_id, label: row.sku_code }];
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.transfersTitle}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.transfersDescription}</Typography.Paragraph>
        <PermissionGuard permission={Perm.INVENTORY_WRITE}>
          <Form<TransferForm>
            form={form}
            layout="vertical"
            initialValues={{ lines: [{ quantity: 1 }] }}
            onFinish={(values) => {
              if (values.from_warehouse_id === values.to_warehouse_id) {
                feedback().message.error(copy.sameWarehouse);
                return;
              }
              create.mutate({
                from_warehouse_id: values.from_warehouse_id,
                to_warehouse_id: values.to_warehouse_id,
                lines: values.lines.map((line) => ({ sku_id: line.sku_id, quantity: line.quantity })),
              });
            }}
          >
            <Space wrap>
              <Form.Item name="from_warehouse_id" label={copy.fromWarehouse} rules={[{ required: true }]}>
                <Select style={{ width: 180 }} options={warehouseOptions} />
              </Form.Item>
              <Form.Item name="to_warehouse_id" label={copy.toWarehouse} rules={[{ required: true }]}>
                <Select style={{ width: 180 }} options={warehouseOptions} />
              </Form.Item>
            </Space>
            <Form.List name="lines">
              {(fields, { add, remove }) => (
                <Space direction="vertical">
                  {fields.map((field) => (
                    <Space key={field.key} align="baseline">
                      <Form.Item name={[field.name, 'sku_id']} rules={[{ required: true, message: copy.sku }]}>
                        <Select style={{ width: 220 }} placeholder={copy.sku} options={skuOptions} />
                      </Form.Item>
                      <Form.Item name={[field.name, 'quantity']} rules={[{ required: true, message: copy.quantity }]}>
                        <InputNumber min={1} placeholder={copy.quantity} />
                      </Form.Item>
                      {fields.length > 1 ? (
                        <Button type="link" onClick={() => remove(field.name)}>
                          {copy.cancel}
                        </Button>
                      ) : null}
                    </Space>
                  ))}
                  <Button onClick={() => add({ quantity: 1 })}>{copy.addLine}</Button>
                </Space>
              )}
            </Form.List>
            <Button type="primary" htmlType="submit" loading={create.isPending} style={{ marginTop: 12 }}>
              {copy.createTransfer}
            </Button>
          </Form>
        </PermissionGuard>
      </Card>
      {transfers.isError ? <Alert type="error" message={messageOf(transfers.error)} /> : null}
      <Card>
        <Table<TransferView>
          rowKey="id"
          loading={transfers.isLoading}
          dataSource={transfers.data?.items ?? []}
          pagination={false}
          expandable={{
            expandedRowRender: (row) => (
              <Table
                rowKey="sku_id"
                pagination={false}
                dataSource={row.lines}
                columns={[
                  { title: copy.sku, dataIndex: 'sku_code' },
                  { title: copy.quantity, dataIndex: 'quantity' },
                ]}
              />
            ),
          }}
          columns={[
            { title: copy.fromWarehouse, dataIndex: 'from_warehouse_name' },
            { title: copy.toWarehouse, dataIndex: 'to_warehouse_name' },
            {
              title: copy.status,
              dataIndex: 'status',
              render: (value: TransferStatus) => <Tag>{STATUS_LABEL[value] ?? value}</Tag>,
            },
            { title: copy.when, dataIndex: 'created_at', render: (value: string) => formatDateTime(value) },
            {
              title: zhCN.common.actions,
              render: (_, row) => (
                <PermissionGuard permission={Perm.INVENTORY_WRITE}>
                  <Space>
                    {row.status === 'DRAFT' ? (
                      <Button type="link" loading={ship.isPending} onClick={() => ship.mutate(row.id)}>
                        {copy.ship}
                      </Button>
                    ) : null}
                    {row.status === 'IN_TRANSIT' ? (
                      <Button type="link" loading={receive.isPending} onClick={() => receive.mutate(row.id)}>
                        {copy.receive}
                      </Button>
                    ) : null}
                    {row.status === 'DRAFT' || row.status === 'IN_TRANSIT' ? (
                      <Button
                        type="link"
                        loading={cancel.isPending}
                        onClick={() => {
                          Modal.confirm({
                            title: copy.cancel,
                            onOk: () => cancel.mutate(row.id),
                          });
                        }}
                      >
                        {copy.cancel}
                      </Button>
                    ) : null}
                  </Space>
                </PermissionGuard>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
