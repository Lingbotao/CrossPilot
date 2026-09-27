import { Button, Card, Form, Input, Switch, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { ordersApi } from '@/api/orders';
import { Perm } from '@/api/types';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { FreshnessBanner, OrderNav } from '@/pages/orders/OrderChrome';

const copy = zhCN.orderPage;

export function OrderSettingsPage() {
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
    onError: (error: unknown) => setNotice(error instanceof ApiError ? error.message : '保存失败'),
  });

  return (
    <Card title={zhCN.menu.ordersSettings}>
      <OrderNav />
      <FreshnessBanner />
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
    </Card>
  );
}
