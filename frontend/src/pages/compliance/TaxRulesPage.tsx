import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, DatePicker, Form, Input, Select, Space, Table, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { complianceApi } from '@/api/compliance';
import { CONTENT_MARKETS, Perm, TAX_TYPES, type TaxRuleView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import { MoneyText } from '@/components/MoneyText';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.taxPage;

interface TaxForm {
  country: string;
  tax_type: string;
  hs_code_pattern: string;
  rate: string;
  basis_numerator: string;
  basis_denominator: string;
  threshold_amount?: string;
  threshold_currency?: string;
  effective_from: Dayjs;
  effective_to?: Dayjs;
  source: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

function formatWhen(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString('zh-CN');
}

function wholeNumber(value: string): number | null {
  const text = value.trim();
  if (!/^[1-9]\d*$/.test(text)) {
    return null;
  }
  return Number(text);
}

export function TaxRulesPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<TaxForm>();
  const rules = useQuery({
    queryKey: ['tax-rules'],
    queryFn: () => complianceApi.taxRules(),
  });
  const alerts = useQuery({
    queryKey: ['compliance-alerts'],
    queryFn: () => complianceApi.alerts(),
  });
  const [country, setCountry] = useState<string | undefined>();
  const filtered = (rules.data ?? []).filter((row) => (country ? row.country === country : true));
  const upcoming = (alerts.data ?? []).filter((row) => row.kind === 'TAX_EFFECTIVE');

  const save = useMutation({
    mutationFn: (values: TaxForm) => {
      const numerator = wholeNumber(values.basis_numerator);
      const denominator = wholeNumber(values.basis_denominator);
      if (numerator === null || denominator === null) {
        return Promise.reject(new Error(copy.basisHint));
      }
      const amount = values.threshold_amount?.trim() ?? '';
      const currency = values.threshold_currency?.trim() ?? '';
      if ((amount === '') !== (currency === '')) {
        return Promise.reject(new Error(copy.threshold));
      }
      return complianceApi.createTaxRule({
        country: values.country,
        tax_type: values.tax_type,
        hs_code_pattern: values.hs_code_pattern.trim() || '*',
        rate: values.rate.trim(),
        basis_numerator: numerator,
        basis_denominator: denominator,
        threshold_amount: amount || null,
        threshold_currency: currency || null,
        effective_from: values.effective_from.format('YYYY-MM-DD'),
        effective_to: values.effective_to ? values.effective_to.format('YYYY-MM-DD') : null,
        source: values.source.trim(),
      });
    },
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['tax-rules'] });
      await queryClient.invalidateQueries({ queryKey: ['compliance-alerts'] });
    },
  });

  const retire = useMutation({
    mutationFn: (ruleId: string) => complianceApi.retireTaxRule(ruleId),
    onSuccess: async () => {
      feedback().message.success(copy.retired);
      await queryClient.invalidateQueries({ queryKey: ['tax-rules'] });
      await queryClient.invalidateQueries({ queryKey: ['compliance-alerts'] });
    },
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {copy.title}
      </Typography.Title>
      <Alert type="warning" showIcon message={copy.disclaimer} />
      <Card title={copy.upcoming} size="small">
        {alerts.isError ? <Alert type="error" message={messageOf(alerts.error)} /> : null}
        {upcoming.length === 0 ? (
          <Typography.Text type="secondary">{copy.noUpcoming}</Typography.Text>
        ) : (
          upcoming.map((row) => (
            <Typography.Paragraph key={`${row.ref_id}-${row.due_on}`} style={{ marginBottom: 4 }}>
              {row.summary}
            </Typography.Paragraph>
          ))
        )}
      </Card>
      <Card>
        <Space style={{ marginBottom: 12 }}>
          <Select
            allowClear
            placeholder={copy.country}
            style={{ width: 160 }}
            value={country}
            onChange={setCountry}
            options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))}
          />
        </Space>
        {rules.isError ? <Alert type="error" message={messageOf(rules.error)} /> : null}
        <Table<TaxRuleView>
          rowKey="id"
          loading={rules.isLoading}
          dataSource={filtered}
          pagination={false}
          locale={{ emptyText: copy.empty }}
          scroll={{ x: 1200 }}
          columns={[
            { title: copy.country, dataIndex: 'country', width: 100 },
            { title: copy.taxType, dataIndex: 'tax_type', width: 120 },
            { title: copy.pattern, dataIndex: 'hs_code_pattern', width: 100 },
            { title: copy.rate, dataIndex: 'rate_percent', width: 120 },
            {
              title: copy.basis,
              width: 110,
              render: (_, row) => `${row.basis_numerator}/${row.basis_denominator}`,
            },
            {
              title: copy.threshold,
              width: 160,
              render: (_, row) =>
                row.threshold_amount ? (
                  <MoneyText value={row.threshold_amount} currency={row.threshold_currency ?? undefined} />
                ) : (
                  '—'
                ),
            },
            { title: copy.effectiveFrom, dataIndex: 'effective_from', width: 120 },
            {
              title: copy.effectiveTo,
              width: 120,
              render: (_, row) => row.effective_to ?? copy.openEnded,
            },
            { title: copy.version, dataIndex: 'version', width: 70 },
            {
              title: copy.status,
              width: 90,
              render: (_, row) => (row.status === 'ACTIVE' ? copy.active : copy.disabled),
            },
            { title: copy.source, dataIndex: 'source' },
            { title: copy.verifier, dataIndex: 'verified_by', width: 140 },
            {
              title: copy.verifiedAt,
              dataIndex: 'verified_at',
              width: 180,
              render: (value: string) => formatWhen(value),
            },
            {
              title: copy.retire,
              width: 90,
              render: (_, row) =>
                row.status === 'ACTIVE' ? (
                  <PermissionGuard permission={Perm.COMPLIANCE_WRITE}>
                    <Button size="small" onClick={() => retire.mutate(row.id)}>
                      {copy.retire}
                    </Button>
                  </PermissionGuard>
                ) : (
                  '—'
                ),
            },
          ]}
        />
      </Card>
      <PermissionGuard permission={Perm.COMPLIANCE_WRITE}>
        <Card title={copy.save}>
          <Form<TaxForm>
            form={form}
            layout="vertical"
            initialValues={{ hs_code_pattern: '*', basis_numerator: '1', basis_denominator: '1' }}
            onFinish={(values) => save.mutate(values)}
          >
            <Space wrap align="start">
              <Form.Item name="country" label={copy.country} rules={[{ required: true }]}>
                <Select style={{ width: 140 }} options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))} />
              </Form.Item>
              <Form.Item name="tax_type" label={copy.taxType} rules={[{ required: true }]}>
                <Select style={{ width: 160 }} options={TAX_TYPES.map((code) => ({ value: code, label: code }))} />
              </Form.Item>
              <Form.Item name="hs_code_pattern" label={copy.pattern} extra={copy.patternHint} rules={[{ required: true }]}>
                <Input style={{ width: 180 }} />
              </Form.Item>
              <Form.Item name="rate" label={copy.rate} extra={copy.rateHint} rules={[{ required: true }]}>
                <Input style={{ width: 140 }} />
              </Form.Item>
              <Form.Item name="basis_numerator" label={copy.basis} extra={copy.basisHint} rules={[{ required: true }]}>
                <Input style={{ width: 100 }} />
              </Form.Item>
              <Form.Item name="basis_denominator" label=" " rules={[{ required: true }]}>
                <Input style={{ width: 100 }} />
              </Form.Item>
              <Form.Item name="threshold_amount" label={copy.threshold}>
                <Input style={{ width: 140 }} />
              </Form.Item>
              <Form.Item name="threshold_currency" label={copy.currency}>
                <Input style={{ width: 100 }} maxLength={3} />
              </Form.Item>
              <Form.Item name="effective_from" label={copy.effectiveFrom} rules={[{ required: true }]}>
                <DatePicker />
              </Form.Item>
              <Form.Item name="effective_to" label={copy.effectiveTo}>
                <DatePicker />
              </Form.Item>
            </Space>
            <Form.Item name="source" label={copy.source} rules={[{ required: true, message: copy.sourceRequired }]}>
              <Input.TextArea rows={2} />
            </Form.Item>
            {save.isError ? <Alert type="error" message={messageOf(save.error)} style={{ marginBottom: 12 }} /> : null}
            <Button type="primary" htmlType="submit" loading={save.isPending}>
              {copy.save}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
    </Space>
  );
}
