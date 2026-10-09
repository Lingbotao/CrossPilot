import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, DatePicker, Form, Input, Select, Space, Table, Typography } from 'antd';
import type { Dayjs } from 'dayjs';

import { ApiError } from '@/api/client';
import { profitApi } from '@/api/profit';
import { Perm, RATE_BASES, type ExchangeRateView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.exchangeRatePage;

interface RateForm {
  base_currency: string;
  quote_currency: string;
  rate: string;
  basis: string;
  effective_on: Dayjs;
  source: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

export function ExchangeRatesPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<RateForm>();
  const rates = useQuery({ queryKey: ['exchange-rates'], queryFn: () => profitApi.rates() });
  const save = useMutation({
    mutationFn: (values: RateForm) =>
      profitApi.createRate({
        base_currency: values.base_currency.trim().toUpperCase(),
        quote_currency: values.quote_currency.trim().toUpperCase(),
        rate: values.rate.trim(),
        basis: values.basis,
        effective_on: values.effective_on.format('YYYY-MM-DD'),
        source: values.source.trim(),
      }),
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['exchange-rates'] });
    },
  });
  const lock = useMutation({
    mutationFn: (rateId: string) => profitApi.lockRate(rateId),
    onSuccess: async () => {
      feedback().message.success(copy.lockedDone);
      await queryClient.invalidateQueries({ queryKey: ['exchange-rates'] });
    },
  });

  return (
    <CostGuard fallback={<Typography.Text type="secondary">无权限查看成本与利润</Typography.Text>}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Typography.Title level={3}>{copy.title}</Typography.Title>
        <Alert type="info" message={copy.hint} />
        {rates.isError ? <Alert type="error" message={messageOf(rates.error)} /> : null}
        <Table<ExchangeRateView>
          rowKey="id"
          loading={rates.isLoading}
          pagination={false}
          dataSource={rates.data ?? []}
          columns={[
            { title: copy.base, dataIndex: 'base_currency' },
            { title: copy.quote, dataIndex: 'quote_currency' },
            {
              title: copy.rate,
              render: (_, row) => <MoneyText value={row.rate} currency={row.quote_currency} decimals={6} showSymbol={false} />,
            },
            {
              title: copy.basis,
              dataIndex: 'basis',
              render: (value: string) => copy.bases[value as keyof typeof copy.bases] ?? value,
            },
            { title: copy.effectiveOn, dataIndex: 'effective_on' },
            { title: copy.source, dataIndex: 'source' },
            {
              title: copy.locked,
              render: (_, row) =>
                row.locked ? (
                  copy.locked
                ) : (
                  <PermissionGuard permission={Perm.FINANCE_WRITE}>
                    <Button type="link" onClick={() => lock.mutate(row.id)}>
                      {copy.lock}
                    </Button>
                  </PermissionGuard>
                ),
            },
          ]}
        />
        <PermissionGuard permission={Perm.FINANCE_WRITE}>
          <Card>
            <Form<RateForm> form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
              <Space wrap align="start">
                <Form.Item name="base_currency" label={copy.base} rules={[{ required: true }]}>
                  <Input style={{ width: 100 }} />
                </Form.Item>
                <Form.Item name="quote_currency" label={copy.quote} rules={[{ required: true }]}>
                  <Input style={{ width: 100 }} />
                </Form.Item>
                <Form.Item name="rate" label={copy.rate} rules={[{ required: true }]}>
                  <Input style={{ width: 140 }} />
                </Form.Item>
                <Form.Item name="basis" label={copy.basis} rules={[{ required: true }]}>
                  <Select style={{ width: 140 }} options={RATE_BASES.map((item) => ({ value: item, label: copy.bases[item] }))} />
                </Form.Item>
                <Form.Item name="effective_on" label={copy.effectiveOn} rules={[{ required: true }]}>
                  <DatePicker />
                </Form.Item>
                <Form.Item name="source" label={copy.source} rules={[{ required: true }]}>
                  <Input style={{ width: 240 }} />
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
    </CostGuard>
  );
}
