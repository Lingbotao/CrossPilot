import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Table, Tag, Typography } from 'antd';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { purchaseApi } from '@/api/purchase';
import { Perm, type AllocMethod, type FirstMileChannel, type ShipmentView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.purchasePage;

const CHANNELS: { value: FirstMileChannel; label: string }[] = [
  { value: 'AIR', label: '空运' },
  { value: 'SEA_FCL', label: '海运整柜' },
  { value: 'SEA_LCL', label: '海运拼柜' },
  { value: 'EXPRESS', label: '快递' },
  { value: 'PACKET', label: '小包' },
];

const METHODS: { value: AllocMethod; label: string }[] = [
  { value: 'WEIGHT', label: copy.weight },
  { value: 'VOLUME', label: copy.volume },
  { value: 'VALUE', label: copy.value },
];

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

interface ShipmentForm {
  forwarder: string;
  channel: FirstMileChannel;
  container_no?: string;
  destination_market: string;
  cost_total: string;
  currency: string;
  alloc_method: AllocMethod;
  storage_days?: number;
  purchase_order_id?: string;
  from_warehouse_id?: string;
  to_warehouse_id?: string;
  sku_id: string;
  quantity: number;
}

export function FirstMilePage() {
  const client = useQueryClient();
  const [form] = Form.useForm<ShipmentForm>();
  const shipments = useQuery({
    queryKey: ['first-mile-shipments'],
    queryFn: () => purchaseApi.listShipments({ limit: 50 }),
  });
  const orders = useQuery({
    queryKey: ['purchase-orders'],
    queryFn: () => purchaseApi.listOrders({ limit: 50 }),
  });
  const warehouses = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.listWarehouses({ limit: 100 }),
  });
  const create = useMutation({
    mutationFn: (values: ShipmentForm) =>
      purchaseApi.createShipment({
        forwarder: values.forwarder,
        channel: values.channel,
        container_no: values.container_no,
        destination_market: values.destination_market,
        cost_total: values.cost_total,
        currency: values.currency,
        alloc_method: values.alloc_method,
        storage_days: values.storage_days,
        purchase_order_id: values.purchase_order_id || null,
        from_warehouse_id: values.from_warehouse_id || null,
        to_warehouse_id: values.to_warehouse_id || null,
        lines: [{ sku_id: values.sku_id, quantity: values.quantity }],
      }),
    onSuccess: async () => {
      feedback().message.success(copy.created);
      form.resetFields();
      await client.invalidateQueries({ queryKey: ['first-mile-shipments'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });
  const post = useMutation({
    mutationFn: purchaseApi.postShipment,
    onSuccess: async () => {
      feedback().message.success(copy.posted);
      await client.invalidateQueries({ queryKey: ['first-mile-shipments'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });
  const warehouseOptions = (warehouses.data?.items ?? [])
    .filter((row) => row.warehouse_type !== 'PLATFORM')
    .map((row) => ({ value: row.id, label: row.name }));

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.firstMileTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.firstMileDescription}</Typography.Paragraph>
      </div>
      {shipments.isError ? <Alert type="error" message={messageOf(shipments.error)} /> : null}
      <PermissionGuard permission={Perm.PURCHASE_WRITE}>
        <Card title={copy.create}>
          <Form<ShipmentForm>
            form={form}
            layout="vertical"
            initialValues={{ currency: 'USD', alloc_method: 'WEIGHT', channel: 'AIR', storage_days: 0, quantity: 1 }}
            onFinish={(values) => create.mutate(values)}
          >
            <Space wrap>
              <Form.Item name="forwarder" label={copy.forwarder} rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item name="channel" label={copy.channel} rules={[{ required: true }]}>
                <Select options={CHANNELS} style={{ width: 140 }} />
              </Form.Item>
              <Form.Item name="container_no" label={copy.container}>
                <Input />
              </Form.Item>
              <Form.Item name="destination_market" label={copy.market} rules={[{ required: true }]}>
                <Input maxLength={2} style={{ width: 80 }} />
              </Form.Item>
              <Form.Item name="alloc_method" label={copy.allocMethod} rules={[{ required: true }]}>
                <Select options={METHODS} style={{ width: 120 }} />
              </Form.Item>
              <Form.Item name="cost_total" label={copy.costTotal} rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item name="currency" label={copy.currency} rules={[{ required: true }]}>
                <Input maxLength={3} style={{ width: 80 }} />
              </Form.Item>
              <Form.Item name="storage_days" label={copy.storageDays}>
                <InputNumber min={0} />
              </Form.Item>
              <Form.Item name="purchase_order_id" label={copy.ordersTitle}>
                <Select
                  allowClear
                  style={{ width: 180 }}
                  options={(orders.data?.items ?? []).map((row) => ({ value: row.id, label: row.supplier_name }))}
                />
              </Form.Item>
              <Form.Item name="from_warehouse_id" label={copy.fromWarehouse}>
                <Select allowClear style={{ width: 160 }} options={warehouseOptions} />
              </Form.Item>
              <Form.Item name="to_warehouse_id" label={copy.toWarehouse}>
                <Select allowClear style={{ width: 160 }} options={warehouseOptions} />
              </Form.Item>
              <Form.Item name="sku_id" label={copy.sku} rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item name="quantity" label={copy.quantity} rules={[{ required: true }]}>
                <InputNumber min={1} />
              </Form.Item>
            </Space>
            <Button type="primary" htmlType="submit" loading={create.isPending}>
              {copy.create}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
      <Table<ShipmentView>
        rowKey="id"
        loading={shipments.isLoading}
        dataSource={shipments.data?.items ?? []}
        pagination={false}
        locale={{ emptyText: copy.empty }}
        expandable={{
          expandedRowRender: (row) => (
            <Space direction="vertical">
              {row.allocations.map((line) => (
                <div key={line.id}>
                  {line.sku_code} · {line.quantity}
                  <CostGuard>
                    <>
                      {' '}
                      <MoneyText value={line.allocated_cost} currency={line.currency ?? undefined} />
                      <Typography.Paragraph type="secondary">{line.formula}</Typography.Paragraph>
                    </>
                  </CostGuard>
                </div>
              ))}
              {row.pools.map((pool) => (
                <CostGuard key={pool.id}>
                  <Typography.Text>
                    {copy.landed} <MoneyText value={pool.landed_unit} currency={pool.currency ?? undefined} />
                  </Typography.Text>
                </CostGuard>
              ))}
            </Space>
          ),
        }}
        columns={[
          { title: copy.forwarder, dataIndex: 'forwarder' },
          { title: copy.channel, dataIndex: 'channel' },
          { title: copy.container, dataIndex: 'container_no' },
          { title: copy.market, dataIndex: 'destination_market' },
          {
            title: copy.allocMethod,
            dataIndex: 'alloc_method',
            render: (value: AllocMethod) => METHODS.find((item) => item.value === value)?.label ?? value,
          },
          {
            title: copy.costTotal,
            render: (_, row) => (
              <CostGuard>
                <MoneyText value={row.cost_total} currency={row.currency ?? undefined} />
              </CostGuard>
            ),
          },
          { title: copy.status, dataIndex: 'status', render: (value: string) => <Tag>{value === 'POSTED' ? copy.posted : copy.draft}</Tag> },
          {
            title: copy.post,
            render: (_, row) =>
              row.status === 'DRAFT' ? (
                <PermissionGuard permission={Perm.PURCHASE_WRITE}>
                  <Button size="small" loading={post.isPending} onClick={() => post.mutate(row.id)}>
                    {copy.post}
                  </Button>
                </PermissionGuard>
              ) : null,
          },
        ]}
      />
    </Space>
  );
}
