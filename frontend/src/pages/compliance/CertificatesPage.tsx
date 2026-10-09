import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, DatePicker, Form, Input, Select, Space, Statistic, Table, Typography, Upload } from 'antd';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { complianceApi } from '@/api/compliance';
import { productsApi } from '@/api/products';
import {
  CERT_TYPES,
  CONTENT_MARKETS,
  Perm,
  type CertRequirementView,
  type CertificateView,
  type SkuView,
} from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.certPage;

interface CertForm {
  sku_id: string;
  market: string;
  cert_type: string;
  cert_no: string;
  issued_at: Dayjs;
  expires_at: Dayjs;
}

interface RequirementForm {
  market: string;
  category_code: string;
  cert_type: string;
  source: string;
}

interface GapForm {
  sku_id: string;
  market: string;
  category_code: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

function reasonLabel(reason: string): string {
  if (reason === 'EXPIRED') {
    return copy.gapExpired;
  }
  return copy.gapMissing;
}

export function CertificatesPage() {
  const queryClient = useQueryClient();
  const [certForm] = Form.useForm<CertForm>();
  const [ruleForm] = Form.useForm<RequirementForm>();
  const [gapForm] = Form.useForm<GapForm>();
  const [spuKeyword, setSpuKeyword] = useState('');
  const [spuId, setSpuId] = useState<string | undefined>();
  const [gapQuery, setGapQuery] = useState<GapForm | null>(null);

  const certificates = useQuery({
    queryKey: ['certificates'],
    queryFn: () => complianceApi.certificates(),
  });
  const requirements = useQuery({
    queryKey: ['cert-requirements'],
    queryFn: () => complianceApi.requirements(),
  });
  const alerts = useQuery({
    queryKey: ['compliance-alerts'],
    queryFn: () => complianceApi.alerts(),
  });
  const spus = useQuery({
    queryKey: ['cert-spu-options', spuKeyword],
    queryFn: () => productsApi.list({ q: spuKeyword || undefined, limit: 20 }),
  });
  const spu = useQuery({
    queryKey: ['cert-spu', spuId],
    enabled: Boolean(spuId),
    queryFn: () => productsApi.detail(spuId ?? ''),
  });
  const gaps = useQuery({
    queryKey: ['cert-gaps', gapQuery],
    enabled: gapQuery !== null,
    queryFn: () => complianceApi.gaps(gapQuery ?? { sku_id: '', market: '', category_code: '' }),
  });

  const skuOptions = (spu.data?.skus ?? []).map((sku: SkuView) => ({ value: sku.id, label: sku.sku_code }));
  const certAlerts = (alerts.data ?? []).filter((row) => row.kind === 'CERT_EXPIRY');
  const count = (level: string) => certAlerts.filter((row) => row.level === level).length;

  const refreshCerts = async () => {
    await queryClient.invalidateQueries({ queryKey: ['certificates'] });
    await queryClient.invalidateQueries({ queryKey: ['compliance-alerts'] });
    await queryClient.invalidateQueries({ queryKey: ['cert-gaps'] });
  };

  const save = useMutation({
    mutationFn: (values: CertForm) =>
      complianceApi.createCertificate({
        sku_id: values.sku_id,
        market: values.market,
        cert_type: values.cert_type,
        cert_no: values.cert_no.trim(),
        issued_at: values.issued_at.format('YYYY-MM-DD'),
        expires_at: values.expires_at.format('YYYY-MM-DD'),
      }),
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      certForm.resetFields();
      await refreshCerts();
    },
  });

  const upload = useMutation({
    mutationFn: (input: { id: string; file: File }) => complianceApi.uploadCertificate(input.id, input.file),
    onSuccess: async () => {
      feedback().message.success(copy.uploaded);
      await refreshCerts();
    },
  });

  const importFile = useMutation({
    mutationFn: (file: File) => complianceApi.importCertificates(file),
    onSuccess: async (result) => {
      feedback().message.success(`${copy.imported}：${result.imported + result.updated}`);
      await refreshCerts();
    },
  });

  const saveRule = useMutation({
    mutationFn: (values: RequirementForm) =>
      complianceApi.createRequirement({
        market: values.market,
        category_code: values.category_code.trim(),
        cert_type: values.cert_type,
        source: values.source.trim(),
      }),
    onSuccess: async () => {
      feedback().message.success(copy.requirementSaved);
      ruleForm.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['cert-requirements'] });
    },
  });

  const retireRule = useMutation({
    mutationFn: (ruleId: string) => complianceApi.retireRequirement(ruleId),
    onSuccess: async () => {
      feedback().message.success(copy.retired);
      await queryClient.invalidateQueries({ queryKey: ['cert-requirements'] });
    },
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {copy.title}
      </Typography.Title>
      <Alert type="warning" showIcon message={copy.disclaimer} />
      <Card title={copy.alerts} size="small">
        {alerts.isError ? <Alert type="error" message={messageOf(alerts.error)} /> : null}
        <Space size={32} wrap>
          <Statistic title={copy.window60} value={count('D60')} />
          <Statistic title={copy.window30} value={count('D30')} />
          <Statistic title={copy.window7} value={count('D7')} />
          <Statistic title={copy.expired} value={count('EXPIRED')} />
        </Space>
      </Card>
      <Card>
        {certificates.isError ? <Alert type="error" message={messageOf(certificates.error)} /> : null}
        <Table<CertificateView>
          rowKey="id"
          loading={certificates.isLoading}
          dataSource={certificates.data ?? []}
          pagination={false}
          locale={{ emptyText: copy.empty }}
          columns={[
            { title: copy.sku, dataIndex: 'sku_code' },
            { title: copy.market, dataIndex: 'market', width: 80 },
            { title: copy.certType, dataIndex: 'cert_type', width: 120 },
            { title: copy.certNo, dataIndex: 'cert_no' },
            { title: copy.issued, dataIndex: 'issued_at', width: 120 },
            { title: copy.expires, dataIndex: 'expires_at', width: 120 },
            {
              title: copy.file,
              width: 160,
              render: (_, row) => (
                <Space>
                  <span>{row.object_key ? copy.attached : copy.missingFile}</span>
                  <PermissionGuard permission={Perm.COMPLIANCE_WRITE}>
                    <Upload
                      showUploadList={false}
                      accept=".pdf,.png,.jpg,.jpeg"
                      beforeUpload={(file) => {
                        upload.mutate({ id: row.id, file });
                        return false;
                      }}
                    >
                      <Button size="small">{copy.upload}</Button>
                    </Upload>
                  </PermissionGuard>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <PermissionGuard permission={Perm.COMPLIANCE_WRITE}>
        <Card title={copy.save}>
          <Form<CertForm> form={certForm} layout="vertical" onFinish={(values) => save.mutate(values)}>
            <Space wrap align="start">
              <Form.Item label={copy.sku}>
                <Select
                  showSearch
                  filterOption={false}
                  placeholder={copy.skuPlaceholder}
                  style={{ width: 220 }}
                  onSearch={setSpuKeyword}
                  options={(spus.data?.items ?? []).map((item) => ({ value: item.id, label: item.title }))}
                  onChange={(value: string) => setSpuId(value)}
                />
              </Form.Item>
              <Form.Item name="sku_id" label=" " rules={[{ required: true }]}>
                <Select style={{ width: 180 }} options={skuOptions} />
              </Form.Item>
              <Form.Item name="market" label={copy.market} rules={[{ required: true }]}>
                <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))} />
              </Form.Item>
              <Form.Item name="cert_type" label={copy.certType} rules={[{ required: true }]}>
                <Select style={{ width: 160 }} options={CERT_TYPES.map((code) => ({ value: code, label: code }))} />
              </Form.Item>
              <Form.Item name="cert_no" label={copy.certNo} rules={[{ required: true }]}>
                <Input style={{ width: 180 }} />
              </Form.Item>
              <Form.Item name="issued_at" label={copy.issued} rules={[{ required: true }]}>
                <DatePicker />
              </Form.Item>
              <Form.Item name="expires_at" label={copy.expires} rules={[{ required: true }]}>
                <DatePicker />
              </Form.Item>
            </Space>
            {save.isError ? <Alert type="error" message={messageOf(save.error)} style={{ marginBottom: 12 }} /> : null}
            <Space>
              <Button type="primary" htmlType="submit" loading={save.isPending}>
                {copy.save}
              </Button>
              <Upload
                showUploadList={false}
                accept=".csv,.xlsx"
                beforeUpload={(file) => {
                  importFile.mutate(file);
                  return false;
                }}
              >
                <Button loading={importFile.isPending}>{copy.import}</Button>
              </Upload>
            </Space>
            {importFile.isError ? (
              <Alert type="error" message={messageOf(importFile.error)} style={{ marginTop: 12 }} />
            ) : null}
          </Form>
        </Card>
        <Card title={copy.requirements}>
          <Table<CertRequirementView>
            rowKey="id"
            loading={requirements.isLoading}
            dataSource={requirements.data ?? []}
            pagination={false}
            locale={{ emptyText: copy.requirementEmpty }}
            columns={[
              { title: copy.market, dataIndex: 'market', width: 80 },
              { title: copy.category, dataIndex: 'category_code' },
              { title: copy.certType, dataIndex: 'cert_type', width: 140 },
              { title: copy.source, dataIndex: 'source' },
              {
                title: copy.status,
                width: 90,
                render: (_, row) => (row.status === 'ACTIVE' ? copy.active : copy.disabled),
              },
              {
                title: copy.retire,
                width: 90,
                render: (_, row) =>
                  row.status === 'ACTIVE' ? (
                    <Button size="small" onClick={() => retireRule.mutate(row.id)}>
                      {copy.retire}
                    </Button>
                  ) : (
                    '—'
                  ),
              },
            ]}
          />
          <Form<RequirementForm>
            form={ruleForm}
            layout="vertical"
            style={{ marginTop: 16 }}
            onFinish={(values) => saveRule.mutate(values)}
          >
            <Space wrap align="start">
              <Form.Item name="market" label={copy.market} rules={[{ required: true }]}>
                <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))} />
              </Form.Item>
              <Form.Item name="category_code" label={copy.category} rules={[{ required: true }]}>
                <Input style={{ width: 180 }} />
              </Form.Item>
              <Form.Item name="cert_type" label={copy.certType} rules={[{ required: true }]}>
                <Select style={{ width: 160 }} options={CERT_TYPES.map((code) => ({ value: code, label: code }))} />
              </Form.Item>
              <Form.Item name="source" label={copy.source} rules={[{ required: true }]}>
                <Input style={{ width: 280 }} />
              </Form.Item>
            </Space>
            {saveRule.isError ? (
              <Alert type="error" message={messageOf(saveRule.error)} style={{ marginBottom: 12 }} />
            ) : null}
            <Button type="primary" htmlType="submit" loading={saveRule.isPending}>
              {copy.saveRequirement}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
      <Card title={copy.gaps}>
        <Form<GapForm>
          form={gapForm}
          layout="vertical"
          onFinish={(values) => setGapQuery({ ...values, category_code: values.category_code.trim() })}
        >
          <Space wrap align="start">
            <Form.Item name="sku_id" label={copy.sku} rules={[{ required: true }]}>
              <Select style={{ width: 180 }} options={skuOptions} />
            </Form.Item>
            <Form.Item name="market" label={copy.market} rules={[{ required: true }]}>
              <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))} />
            </Form.Item>
            <Form.Item name="category_code" label={copy.category} rules={[{ required: true }]}>
              <Input style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Button htmlType="submit">{copy.gapCheck}</Button>
        </Form>
        {gaps.isError ? <Alert type="error" message={messageOf(gaps.error)} style={{ marginTop: 12 }} /> : null}
        {gapQuery && gaps.isSuccess && (gaps.data ?? []).length === 0 ? (
          <Typography.Paragraph type="secondary">{copy.gapEmpty}</Typography.Paragraph>
        ) : null}
        {gapQuery && (gaps.data ?? []).length > 0 ? (
          <Table
            style={{ marginTop: 12 }}
            rowKey="cert_type"
            pagination={false}
            dataSource={gaps.data ?? []}
            columns={[
              { title: copy.certType, dataIndex: 'cert_type' },
              { title: copy.reason, dataIndex: 'reason', render: (value: string) => reasonLabel(value) },
            ]}
          />
        ) : null}
      </Card>
    </Space>
  );
}
