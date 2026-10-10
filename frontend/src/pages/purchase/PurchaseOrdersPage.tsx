import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { purchaseApi } from '@/api/purchase';
import { Perm, type PurchaseOrderView, type PurchaseStatus, type ReceiptDisposition } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.purchasePage;

const STATUS_LABEL: Record<PurchaseStatus, string> = {
  DRAFT: copy.draft,
  PENDING: copy.pending,
  APPROVED: copy.approved,
  PARTIAL: copy.partial,
  RECEIVED: copy.receivedStatus,
  CLOSED: copy.closed,
  CANCELLED: copy.cancelled,
};

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

interface OrderForm {
  supplier_id: string;
  warehouse_id: string;
  currency: string;
  lines: { sku_id: string; quantity: number; unit_price: string }[];
}

interface ReceiptForm {
  item_id: string;
  quantity: number;
  disposition: ReceiptDisposition;
  allow_over: boolean;
}

export function PurchaseOrdersPage() {
  const client = useQueryClient();
  const [form] = Form.useForm<OrderForm>();
  const [receiptForm] = Form.useForm<ReceiptForm>();
  const [receiving, setReceiving] = useState<PurchaseOrderView | null>(null);
  const orders = useQuery({
    queryKey: ['purchase-orders'],
    queryFn: () => purchaseApi.listOrders({ limit: 50 }),
  });
  const suppliers = useQuery({
    queryKey: ['suppliers'],
    queryFn: () => purchaseApi.listSuppliers({ limit: 100 }),
  });
  const warehouses = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.listWarehouses({ limit: 100 }),
  });
  const transit = useQuery({
    queryKey: ['purchase-in-transit'],
    queryFn: () => purchaseApi.inTransit(),
  });
  const invalidate = async () => {
    await client.invalidateQueries({ queryKey: ['purchase-orders'] });
    await client.invalidateQueries({ queryKey: ['purchase-in-transit'] });
  };
  const create = useMutation({
    mutationFn: purchaseApi.createOrder,
    onSuccess: async () => {
      feedback().message.success(copy.created);
      form.resetFields();
      await invalidate();
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'submit' | 'approve' | 'reject' | 'close' | 'cancel' }) =>
      purchaseApi.transition(id, action),
    onSuccess: async () => {
      feedback().message.success(copy.created);
      await invalidate();
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });
  const receive = useMutation({
    mutationFn: (values: ReceiptForm) =>
      purchaseApi.receive(receiving?.id ?? '', {
        disposition: values.disposition,
        allow_over: values.allow_over,
        lines: [{ item_id: values.item_id, quantity: values.disposition === 'SHORT' ? 0 : values.quantity }],
      }),
    onSuccess: async () => {
      feedback().message.success(copy.received);
      setReceiving(null);
      receiptForm.resetFields();
      await invalidate();
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });
  const warehouseOptions = (warehouses.data?.items ?? [])
    .filter((row) => row.warehouse_type !== 'PLATFORM')
    .map((row) => ({ value: row.id, label: row.name }));

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.ordersTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.ordersDescription}</Typography.Paragraph>
      </div>
      <Alert
        type="info"
        message={`${copy.domesticTransit} ${transit.data?.domestic_qty ?? 0} · ${copy.overseasTransit} ${transit.data?.overseas_qty ?? 0}`}
      />
      {orders.isError ? <Alert type="error" message={messageOf(orders.error)} /> : null}
      <PermissionGuard permission={Perm.PURCHASE_WRITE}>
        <Card title={copy.create}>
          <Typography.Paragraph type="secondary">{copy.lineHint}</Typography.Paragraph>
          <Form<OrderForm>
            form={form}
            layout="vertical"
            initialValues={{ currency: 'CNY', lines: [{ quantity: 1, unit_price: '0.000000' }] }}
            onFinish={(values) => create.mutate(values)}
          >
            <Space wrap>
              <Form.Item name="supplier_id" label={copy.supplier} rules={[{ required: true }]}>
                <Select
                  style={{ width: 180 }}
                  options={(suppliers.data?.items ?? []).map((row) => ({ value: row.id, label: row.name }))}
                />
              </Form.Item>
              <Form.Item name="warehouse_id" label={copy.warehouse} rules={[{ required: true }]}>
                <Select style={{ width: 180 }} options={warehouseOptions} />
              </Form.Item>
              <Form.Item name="currency" label={copy.currency} rules={[{ required: true }]}>
                <Input maxLength={3} style={{ width: 80 }} />
              </Form.Item>
            </Space>
            <Form.List name="lines">
              {(fields, { add, remove }) => (
                <Space direction="vertical">
                  {fields.map((field) => (
                    <Space key={field.key} wrap>
                      <Form.Item name={[field.name, 'sku_id']} label={copy.sku} rules={[{ required: true }]}>
                        <Input />
                      </Form.Item>
                      <Form.Item name={[field.name, 'quantity']} label={copy.quantity} rules={[{ required: true }]}>
                        <InputNumber min={1} />
                      </Form.Item>
                      <Form.Item name={[field.name, 'unit_price']} label={copy.unitPrice} rules={[{ required: true }]}>
                        <Input />
                      </Form.Item>
                      {fields.length > 1 ? <Button onClick={() => remove(field.name)}>{copy.cancel}</Button> : null}
                    </Space>
                  ))}
                  <Button onClick={() => add({ quantity: 1, unit_price: '0.000000' })}>{copy.sku}</Button>
                </Space>
              )}
            </Form.List>
            <Button type="primary" htmlType="submit" loading={create.isPending}>
              {copy.create}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
      <Table<PurchaseOrderView>
        rowKey="id"
        loading={orders.isLoading}
        dataSource={orders.data?.items ?? []}
        pagination={false}
        locale={{ emptyText: copy.empty }}
        expandable={{
          expandedRowRender: (row) => (
            <Table
              rowKey="id"
              pagination={false}
              dataSource={row.lines}
              columns={[
                { title: copy.sku, dataIndex: 'sku_code' },
                { title: copy.quantity, dataIndex: 'quantity' },
                { title: copy.openQty, dataIndex: 'open_qty' },
                {
                  title: copy.unitPrice,
                  render: (_, line) => (
                    <CostGuard>
                      <MoneyText value={line.unit_price} currency={line.currency ?? undefined} />
                    </CostGuard>
                  ),
                },
              ]}
            />
          ),
        }}
        columns={[
          { title: copy.supplier, dataIndex: 'supplier_name' },
          { title: copy.warehouse, dataIndex: 'warehouse_name' },
          { title: copy.status, dataIndex: 'status', render: (value: PurchaseStatus) => <Tag>{STATUS_LABEL[value]}</Tag> },
          {
            title: copy.total,
            render: (_, row) => (
              <CostGuard>
                <MoneyText value={row.total_amount} currency={row.currency ?? undefined} />
              </CostGuard>
            ),
          },
          {
            title: copy.status,
            render: (_, row) => (
              <PermissionGuard permission={Perm.PURCHASE_WRITE}>
                <Space>
                  {row.status === 'DRAFT' ? (
                    <Button size="small" onClick={() => act.mutate({ id: row.id, action: 'submit' })}>
                      {copy.submit}
                    </Button>
                  ) : null}
                  {row.status === 'PENDING' ? (
                    <>
                      <Button size="small" type="primary" onClick={() => act.mutate({ id: row.id, action: 'approve' })}>
                        {copy.approve}
                      </Button>
                      <Button size="small" onClick={() => act.mutate({ id: row.id, action: 'reject' })}>
                        {copy.reject}
                      </Button>
                    </>
                  ) : null}
                  {row.status === 'APPROVED' || row.status === 'PARTIAL' ? (
                    <Button size="small" onClick={() => setReceiving(row)}>
                      {copy.receive}
                    </Button>
                  ) : null}
                  {row.status === 'RECEIVED' || row.status === 'PARTIAL' ? (
                    <Button size="small" onClick={() => act.mutate({ id: row.id, action: 'close' })}>
                      {copy.close}
                    </Button>
                  ) : null}
                  {row.status === 'DRAFT' || row.status === 'PENDING' || row.status === 'APPROVED' ? (
                    <Button size="small" danger onClick={() => act.mutate({ id: row.id, action: 'cancel' })}>
                      {copy.cancel}
                    </Button>
                  ) : null}
                </Space>
              </PermissionGuard>
            ),
          },
        ]}
      />
      <Modal
        title={copy.receive}
        open={receiving !== null}
        onCancel={() => setReceiving(null)}
        onOk={() => receiptForm.submit()}
        confirmLoading={receive.isPending}
      >
        <Form<ReceiptForm>
          form={receiptForm}
          layout="vertical"
          initialValues={{ disposition: 'RECEIVE', quantity: 1, allow_over: false }}
          onFinish={(values) => receive.mutate(values)}
        >
          <Form.Item name="item_id" label={copy.sku} rules={[{ required: true }]}>
            <Select
              options={(receiving?.lines ?? []).map((line) => ({
                value: line.id,
                label: `${line.sku_code} / ${copy.openQty} ${line.open_qty}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="disposition" label={copy.disposition} rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'RECEIVE', label: copy.receive },
                { value: 'SHORT', label: copy.shortClose },
              ]}
            />
          </Form.Item>
          <Form.Item name="quantity" label={copy.quantity}>
            <InputNumber min={0} />
          </Form.Item>
          <Form.Item name="allow_over" label={copy.allowOver} valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
