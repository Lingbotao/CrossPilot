import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Select, Space, Table, Typography } from 'antd';

import { ApiError } from '@/api/client';
import { complianceApi } from '@/api/compliance';
import { CONTENT_MARKETS, FILING_CYCLES, Perm, TAX_TYPES, type TaxRegistrationView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.taxRegistrationPage;

interface RegistrationForm {
  country: string;
  tax_type: string;
  tax_no: string;
  entity: string;
  agent?: string;
  filing_cycle: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

export function TaxRegistrationsPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<RegistrationForm>();
  const rows = useQuery({ queryKey: ['tax-registrations'], queryFn: () => complianceApi.taxRegistrations() });
  const save = useMutation({
    mutationFn: (values: RegistrationForm) =>
      complianceApi.createTaxRegistration({
        country: values.country,
        tax_type: values.tax_type,
        tax_no: values.tax_no.trim(),
        entity: values.entity.trim(),
        agent: values.agent?.trim() ?? '',
        filing_cycle: values.filing_cycle,
      }),
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['tax-registrations'] });
    },
  });
  const retire = useMutation({
    mutationFn: (id: string) => complianceApi.retireTaxRegistration(id),
    onSuccess: async () => {
      feedback().message.success(copy.retired);
      await queryClient.invalidateQueries({ queryKey: ['tax-registrations'] });
    },
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {copy.title}
      </Typography.Title>
      <Alert type="info" showIcon message={copy.hint} />
      <Card>
        <Table<TaxRegistrationView>
          rowKey="id"
          loading={rows.isLoading}
          dataSource={rows.data ?? []}
          pagination={false}
          locale={{ emptyText: copy.empty }}
          columns={[
            { title: copy.country, dataIndex: 'country', width: 80 },
            { title: copy.taxType, dataIndex: 'tax_type', width: 120 },
            { title: copy.taxNo, dataIndex: 'tax_no' },
            { title: copy.entity, dataIndex: 'entity' },
            { title: copy.agent, dataIndex: 'agent', render: (value: string) => value || '—' },
            {
              title: copy.cycle,
              dataIndex: 'filing_cycle',
              width: 100,
              render: (value: keyof typeof copy.cycles) => copy.cycles[value] ?? value,
            },
            {
              title: zhCN.common.actions,
              width: 90,
              render: (_, row) => (
                <PermissionGuard permission={Perm.COMPLIANCE_WRITE}>
                  <Button size="small" loading={retire.isPending} onClick={() => retire.mutate(row.id)}>
                    {copy.retire}
                  </Button>
                </PermissionGuard>
              ),
            },
          ]}
        />
      </Card>
      <PermissionGuard permission={Perm.COMPLIANCE_WRITE}>
        <Card title={copy.save}>
          <Form<RegistrationForm> form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
            <Space wrap align="start">
              <Form.Item name="country" label={copy.country} rules={[{ required: true }]}>
                <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((item) => ({ value: item, label: item }))} />
              </Form.Item>
              <Form.Item name="tax_type" label={copy.taxType} rules={[{ required: true }]}>
                <Select style={{ width: 140 }} options={TAX_TYPES.map((item) => ({ value: item, label: item }))} />
              </Form.Item>
              <Form.Item name="filing_cycle" label={copy.cycle} rules={[{ required: true }]}>
                <Select
                  style={{ width: 140 }}
                  options={FILING_CYCLES.map((item) => ({ value: item, label: copy.cycles[item] }))}
                />
              </Form.Item>
              <Form.Item name="tax_no" label={copy.taxNo} rules={[{ required: true }]}>
                <Input style={{ width: 200 }} />
              </Form.Item>
              <Form.Item name="entity" label={copy.entity} rules={[{ required: true }]}>
                <Input style={{ width: 220 }} />
              </Form.Item>
              <Form.Item name="agent" label={copy.agent}>
                <Input style={{ width: 180 }} />
              </Form.Item>
            </Space>
            {save.isError ? <Alert type="error" message={messageOf(save.error)} /> : null}
            <Button type="primary" htmlType="submit" loading={save.isPending}>
              {copy.save}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
    </Space>
  );
}
