import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Select, Space, Switch, Table, Tag, Typography } from 'antd';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { Perm, type WarehouseType, type WarehouseView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.inventoryPage;

const TYPE_OPTIONS: { value: WarehouseType; label: string }[] = [
  { value: 'LOCAL', label: '本地仓' },
  { value: 'OVERSEAS', label: '海外仓' },
  { value: 'FBA', label: 'FBA' },
  { value: 'PLATFORM', label: '平台仓' },
];

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function WarehousesPage() {
  const client = useQueryClient();
  const [form] = Form.useForm();
  const warehouses = useQuery({
    queryKey: ['warehouses'],
    queryFn: () => inventoryApi.listWarehouses({ limit: 100 }),
  });

  const create = useMutation({
    mutationFn: inventoryApi.createWarehouse,
    onSuccess: async () => {
      feedback().message.success(copy.created);
      form.resetFields();
      await client.invalidateQueries({ queryKey: ['warehouses'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  const remove = useMutation({
    mutationFn: inventoryApi.removeWarehouse,
    onSuccess: async () => {
      feedback().message.success(copy.removed);
      await client.invalidateQueries({ queryKey: ['warehouses'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.warehousesTitle}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.warehousesDescription}</Typography.Paragraph>
        <PermissionGuard permission={Perm.INVENTORY_WRITE}>
          <Form
            form={form}
            layout="inline"
            initialValues={{ warehouse_type: 'LOCAL', is_default: false }}
            onFinish={(values: {
              name: string;
              warehouse_type: WarehouseType;
              country: string;
              address?: string;
              external_code?: string;
              is_default?: boolean;
            }) => {
              create.mutate({
                name: values.name,
                warehouse_type: values.warehouse_type,
                country: values.country,
                address: values.address,
                external_code: values.external_code || null,
                is_default: values.is_default,
              });
            }}
          >
            <Form.Item name="name" rules={[{ required: true, message: copy.name }]}>
              <Input placeholder={copy.name} />
            </Form.Item>
            <Form.Item name="warehouse_type">
              <Select style={{ width: 120 }} options={TYPE_OPTIONS} />
            </Form.Item>
            <Form.Item name="country" rules={[{ required: true, len: 2, message: copy.country }]}>
              <Input placeholder={copy.country} style={{ width: 80 }} maxLength={2} />
            </Form.Item>
            <Form.Item name="external_code">
              <Input placeholder={copy.externalCode} />
            </Form.Item>
            <Form.Item name="address">
              <Input placeholder={copy.address} />
            </Form.Item>
            <Form.Item name="is_default" valuePropName="checked" label={copy.defaultWarehouse}>
              <Switch />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={create.isPending}>
                {copy.createWarehouse}
              </Button>
            </Form.Item>
          </Form>
        </PermissionGuard>
      </Card>
      {warehouses.isError ? <Alert type="error" message={messageOf(warehouses.error)} /> : null}
      <Card>
        <Table<WarehouseView>
          rowKey="id"
          loading={warehouses.isLoading}
          dataSource={warehouses.data?.items ?? []}
          pagination={false}
          columns={[
            { title: copy.name, dataIndex: 'name' },
            { title: copy.type, dataIndex: 'warehouse_type' },
            { title: copy.country, dataIndex: 'country' },
            { title: copy.externalCode, dataIndex: 'external_code', render: (value: string | null) => value || '—' },
            {
              title: copy.defaultWarehouse,
              dataIndex: 'is_default',
              render: (value: boolean) => (value ? <Tag color="blue">{copy.defaultWarehouse}</Tag> : '—'),
            },
            {
              title: zhCN.common.actions,
              render: (_, row) => (
                <PermissionGuard permission={Perm.INVENTORY_WRITE}>
                  <Button size="small" danger loading={remove.isPending} onClick={() => remove.mutate(row.id)}>
                    {copy.deleteWarehouse}
                  </Button>
                </PermissionGuard>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
