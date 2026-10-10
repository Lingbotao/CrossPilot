import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Switch, Table, Typography } from 'antd';

import { ApiError } from '@/api/client';
import { purchaseApi } from '@/api/purchase';
import { Perm, type SettlementType, type SupplierView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.purchasePage;

const SETTLEMENTS: { value: SettlementType; label: string }[] = [
  { value: 'PREPAY', label: copy.prepay },
  { value: 'CREDIT', label: copy.credit },
  { value: 'COD', label: copy.cod },
];

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

interface SupplierForm {
  name: string;
  contact?: string;
  settlement_type: SettlementType;
  credit_days?: number;
  rating?: number;
}

interface LinkForm {
  supplier_id: string;
  sku_id: string;
  is_default: boolean;
}

export function SuppliersPage() {
  const client = useQueryClient();
  const [form] = Form.useForm<SupplierForm>();
  const [linkForm] = Form.useForm<LinkForm>();
  const suppliers = useQuery({
    queryKey: ['suppliers'],
    queryFn: () => purchaseApi.listSuppliers({ limit: 50 }),
  });
  const create = useMutation({
    mutationFn: purchaseApi.createSupplier,
    onSuccess: async () => {
      feedback().message.success(copy.created);
      form.resetFields();
      await client.invalidateQueries({ queryKey: ['suppliers'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });
  const bind = useMutation({
    mutationFn: (values: LinkForm) => {
      const current = suppliers.data?.items.find((row) => row.id === values.supplier_id);
      const links = (current?.skus ?? [])
        .filter((row) => row.sku_id !== values.sku_id)
        .map((row) => ({ sku_id: row.sku_id, is_default: values.is_default ? false : row.is_default }));
      links.push({ sku_id: values.sku_id, is_default: values.is_default });
      return purchaseApi.replaceSkus(values.supplier_id, links);
    },
    onSuccess: async () => {
      feedback().message.success(copy.created);
      linkForm.resetFields();
      await client.invalidateQueries({ queryKey: ['suppliers'] });
    },
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.suppliersTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.suppliersDescription}</Typography.Paragraph>
      </div>
      {suppliers.isError ? <Alert type="error" message={messageOf(suppliers.error)} /> : null}
      <PermissionGuard permission={Perm.PURCHASE_WRITE}>
        <Card title={copy.create}>
          <Form<SupplierForm> form={form} layout="inline" onFinish={(values) => create.mutate(values)}>
            <Form.Item name="name" label={copy.name} rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name="contact" label={copy.contact}>
              <Input />
            </Form.Item>
            <Form.Item name="settlement_type" label={copy.settlement} rules={[{ required: true }]}>
              <Select options={SETTLEMENTS} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="credit_days" label={copy.creditDays}>
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item name="rating" label={copy.rating}>
              <InputNumber min={1} max={5} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={create.isPending}>
              {copy.create}
            </Button>
          </Form>
        </Card>
        <Card title={copy.bindSku}>
          <Form<LinkForm> form={linkForm} layout="inline" onFinish={(values) => bind.mutate(values)}>
            <Form.Item name="supplier_id" label={copy.supplier} rules={[{ required: true }]}>
              <Select
                style={{ width: 180 }}
                options={(suppliers.data?.items ?? []).map((row) => ({ value: row.id, label: row.name }))}
              />
            </Form.Item>
            <Form.Item name="sku_id" label={copy.sku} rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name="is_default" label={copy.defaultSupplier} valuePropName="checked">
              <Switch />
            </Form.Item>
            <Button htmlType="submit" loading={bind.isPending}>
              {copy.bindSku}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
      <Table<SupplierView>
        rowKey="id"
        loading={suppliers.isLoading}
        dataSource={suppliers.data?.items ?? []}
        pagination={false}
        locale={{ emptyText: copy.empty }}
        columns={[
          { title: copy.name, dataIndex: 'name' },
          { title: copy.contact, dataIndex: 'contact' },
          {
            title: copy.settlement,
            dataIndex: 'settlement_type',
            render: (value: SettlementType) => SETTLEMENTS.find((item) => item.value === value)?.label ?? value,
          },
          { title: copy.creditDays, dataIndex: 'credit_days' },
          { title: copy.rating, dataIndex: 'rating' },
          {
            title: copy.sku,
            render: (_, row) =>
              row.skus.map((sku) => `${sku.sku_code}${sku.is_default ? copy.defaultTag : ''}`).join('、') || '—',
          },
        ]}
      />
    </Space>
  );
}
