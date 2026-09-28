import { Button, Card, Form, Input, Select, Switch, Table, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { orderStatusApi } from '@/api/orderStatus';
import { ordersApi } from '@/api/orders';
import { rateLimitApi } from '@/api/rateLimits';
import { Perm, UnifiedStatus, type OrderStatusMapping, type PlatformRateLimit, type UnifiedStatusValue } from '@/api/types';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { FreshnessBanner, OrderNav } from '@/pages/orders/OrderChrome';

const copy = zhCN.orderPage;
const statusCopy = zhCN.order.unifiedStatus;
const platforms = ['amazon', 'shopee', 'lazada', 'tiktok'] as const;
const dimensions = ['shop', 'per_shop', 'app', 'per_app'] as const;

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function requiredWhole(value: unknown): number | null {
  const text = asText(value);
  if (!/^[1-9]\d*$/.test(text)) return null;
  return Number(text);
}

function optionalWhole(value: unknown): number | null | undefined {
  const text = asText(value);
  if (!text) return null;
  if (!/^[1-9]\d*$/.test(text)) return undefined;
  return Number(text);
}

function messageOf(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

function ReviewRules() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const rules = useQuery({ queryKey: ['order-review-rules'], queryFn: ordersApi.reviewRules });
  const save = useMutation({
    mutationFn: (values: { currency: string; amount_gt: string; enabled: boolean }) =>
      ordersApi.saveReviewRule(values.currency.trim().toUpperCase(), values.amount_gt, values.enabled),
    onSuccess: async () => {
      setNotice(copy.ruleSaved);
      await queryClient.invalidateQueries({ queryKey: ['order-review-rules'] });
    },
    onError: (error: unknown) => setNotice(messageOf(error, '保存失败')),
  });

  return (
    <>
      <Typography.Title level={5}>{copy.ruleTitle}</Typography.Title>
      <Typography.Paragraph type="secondary">{copy.ruleHint}</Typography.Paragraph>
      {notice ? <Typography.Paragraph>{notice}</Typography.Paragraph> : null}
      <PermissionGuard permission={Perm.ORDER_RULE}>
        <Form
          layout="inline"
          style={{ marginBottom: 16 }}
          initialValues={{ currency: 'USD', enabled: true }}
          onFinish={(values: { currency: string; amount_gt: string; enabled?: boolean }) =>
            save.mutate({ currency: values.currency, amount_gt: values.amount_gt, enabled: values.enabled !== false })
          }
        >
          <Form.Item name="currency" rules={[{ required: true }]}>
            <Input placeholder="USD" style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="amount_gt" rules={[{ required: true }]}>
            <Input placeholder={copy.ruleAmount} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="enabled" label={copy.ruleEnabled} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            {zhCN.common.save}
          </Button>
        </Form>
      </PermissionGuard>
      {(rules.data ?? []).map((rule) => (
        <div key={rule.id}>
          {rule.currency} &gt; {rule.amount_gt} · {rule.enabled ? copy.ruleEnabled : '停用'}
        </div>
      ))}
    </>
  );
}

function StatusMappings() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const mappings = useQuery({ queryKey: ['order-status-mappings'], queryFn: () => orderStatusApi.listMappings() });
  const save = useMutation({
    mutationFn: (values: { platform_code: string; platform_status: string; unified_status: UnifiedStatusValue }) =>
      orderStatusApi.upsertMapping({
        platform_code: values.platform_code,
        platform_status: values.platform_status.trim(),
        unified_status: values.unified_status,
      }),
    onSuccess: async () => {
      setNotice(copy.mappingSaved);
      await queryClient.invalidateQueries({ queryKey: ['order-status-mappings'] });
    },
    onError: (error: unknown) => setNotice(messageOf(error, '保存失败')),
  });

  return (
    <>
      <Typography.Title level={5} style={{ marginTop: 28 }}>
        {copy.mappingTitle}
      </Typography.Title>
      <Typography.Paragraph type="secondary">{copy.mappingHint}</Typography.Paragraph>
      {notice ? <Typography.Paragraph>{notice}</Typography.Paragraph> : null}
      <PermissionGuard permission={Perm.SYSTEM_WRITE}>
        <Form
          layout="inline"
          style={{ marginBottom: 16 }}
          onFinish={(values: { platform_code: string; platform_status: string; unified_status: UnifiedStatusValue }) =>
            save.mutate(values)
          }
        >
          <Form.Item name="platform_code" rules={[{ required: true }]}>
            <Select
              placeholder={zhCN.shopPage.platform}
              style={{ width: 140 }}
              options={platforms.map((value) => ({ value, label: value }))}
            />
          </Form.Item>
          <Form.Item name="platform_status" rules={[{ required: true }]}>
            <Input placeholder={copy.mappingPlatformStatus} style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="unified_status" rules={[{ required: true }]}>
            <Select
              placeholder={copy.status}
              style={{ width: 160 }}
              options={Object.values(UnifiedStatus).map((value) => ({ value, label: statusCopy[value] }))}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            {zhCN.common.save}
          </Button>
        </Form>
      </PermissionGuard>
      <Table<OrderStatusMapping>
        size="small"
        rowKey="id"
        loading={mappings.isLoading}
        dataSource={mappings.data ?? []}
        pagination={{ pageSize: 8, hideOnSinglePage: true }}
        columns={[
          { title: zhCN.shopPage.platform, dataIndex: 'platform_code', width: 120 },
          { title: copy.mappingPlatformStatus, dataIndex: 'platform_status' },
          {
            title: copy.status,
            dataIndex: 'unified_status',
            render: (value: UnifiedStatusValue) => statusCopy[value],
          },
        ]}
      />
    </>
  );
}

function RateLimits() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [notice, setNotice] = useState<string | null>(null);
  const rows = useQuery({ queryKey: ['platform-rate-limits'], queryFn: rateLimitApi.list });
  const save = useMutation({
    mutationFn: rateLimitApi.save,
    onSuccess: async () => {
      setNotice(copy.quotaSaved);
      await queryClient.invalidateQueries({ queryKey: ['platform-rate-limits'] });
    },
    onError: (error: unknown) => setNotice(messageOf(error, '保存失败')),
  });

  return (
    <>
      <Typography.Title level={5} style={{ marginTop: 28 }}>
        {copy.quotaTitle}
      </Typography.Title>
      <Typography.Paragraph type="secondary">{copy.quotaHint}</Typography.Paragraph>
      {notice ? <Typography.Paragraph>{notice}</Typography.Paragraph> : null}
      <PermissionGuard permission={Perm.SYSTEM_WRITE}>
        <Form
          form={form}
          layout="inline"
          style={{ marginBottom: 16 }}
          onFinish={(values: {
            platform_code: string;
            dimension: string;
            qps: string;
            burst: string;
            batch_limit: string;
            daily_quota?: string;
            concurrency?: string;
          }) => {
            const qps = requiredWhole(values.qps);
            const burst = requiredWhole(values.burst);
            const batchLimit = requiredWhole(values.batch_limit);
            const daily = optionalWhole(values.daily_quota);
            const concurrency = optionalWhole(values.concurrency);
            if (qps === null || burst === null || batchLimit === null || daily === undefined || concurrency === undefined) {
              setNotice(copy.quotaInvalid);
              return;
            }
            save.mutate({
              platform_code: values.platform_code,
              dimension: values.dimension,
              qps,
              burst,
              batch_limit: batchLimit,
              daily_quota: daily,
              concurrency,
            });
          }}
        >
          <Form.Item name="platform_code" rules={[{ required: true }]}>
            <Select
              placeholder={zhCN.shopPage.platform}
              style={{ width: 140 }}
              options={platforms.map((value) => ({ value, label: value }))}
              onChange={(code: string) => {
                const row = (rows.data ?? []).find((item) => item.platform_code === code);
                if (!row) return;
                form.setFieldsValue({
                  dimension: row.dimension,
                  qps: String(row.qps),
                  burst: String(row.burst),
                  batch_limit: String(row.batch_limit),
                  daily_quota: row.daily_quota === null ? '' : String(row.daily_quota),
                  concurrency: row.concurrency === null ? '' : String(row.concurrency),
                });
              }}
            />
          </Form.Item>
          <Form.Item name="dimension" rules={[{ required: true }]}>
            <Select
              placeholder={copy.quotaDimension}
              style={{ width: 120 }}
              options={dimensions.map((value) => ({ value, label: value }))}
            />
          </Form.Item>
          <Form.Item name="qps" rules={[{ required: true }]}>
            <Input placeholder={copy.quotaQps} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="burst" rules={[{ required: true }]}>
            <Input placeholder={copy.quotaBurst} style={{ width: 90 }} />
          </Form.Item>
          <Form.Item name="batch_limit" rules={[{ required: true }]}>
            <Input placeholder={copy.quotaBatch} style={{ width: 130 }} />
          </Form.Item>
          <Form.Item name="daily_quota">
            <Input placeholder={copy.quotaDaily} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="concurrency">
            <Input placeholder={copy.quotaConcurrency} style={{ width: 100 }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            {zhCN.common.save}
          </Button>
        </Form>
      </PermissionGuard>
      <Table<PlatformRateLimit>
        size="small"
        rowKey="platform_code"
        loading={rows.isLoading}
        dataSource={rows.data ?? []}
        pagination={false}
        columns={[
          { title: zhCN.shopPage.platform, dataIndex: 'platform_code', width: 110 },
          { title: copy.quotaDimension, dataIndex: 'dimension', width: 110 },
          { title: copy.quotaQps, dataIndex: 'qps', width: 100 },
          { title: copy.quotaBurst, dataIndex: 'burst', width: 80 },
          { title: copy.quotaBatch, dataIndex: 'batch_limit', width: 120 },
          {
            title: copy.quotaDaily,
            dataIndex: 'daily_quota',
            width: 90,
            render: (value: number | null) => value ?? '—',
          },
          {
            title: copy.quotaConcurrency,
            dataIndex: 'concurrency',
            width: 100,
            render: (value: number | null) => value ?? '—',
          },
          {
            title: copy.status,
            dataIndex: 'source',
            width: 90,
            render: (value: PlatformRateLimit['source']) => (value === 'table' ? copy.quotaOverride : copy.quotaDefault),
          },
        ]}
      />
    </>
  );
}

export function OrderSettingsPage() {
  return (
    <Card title={zhCN.menu.ordersSettings}>
      <OrderNav />
      <FreshnessBanner />
      <ReviewRules />
      <StatusMappings />
      <PermissionGuard permission={Perm.SYSTEM_READ}>
        <RateLimits />
      </PermissionGuard>
    </Card>
  );
}
