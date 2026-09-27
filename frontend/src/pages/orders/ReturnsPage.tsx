import { Button, Card, Form, Input, Space, Switch, Table, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { ordersApi } from '@/api/orders';
import { Perm, type ReturnOrderView } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { FreshnessBanner, OrderNav } from '@/pages/orders/OrderChrome';
import { formatDateTime } from '@/utils/format';

const copy = zhCN.orderPage;

const STATUS: Record<string, string> = {
  REQUESTED: '已申请',
  APPROVED: '已通过',
  REJECTED: '已拒绝',
  REFUNDED: '已退款',
};

export function ReturnsPage() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const list = useQuery({ queryKey: ['order-returns'], queryFn: ordersApi.returns });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['order-returns'] });
  };

  const create = useMutation({
    mutationFn: async (values: {
      order_id: string;
      reason: string;
      refund_amount: string;
      restock_flag: boolean;
      restock_sellable: boolean;
    }) => {
      const typed = values.order_id.trim();
      const page = await ordersApi.list({ q: typed, limit: 20 });
      const match = page.items.find((item) => item.platform_order_id === typed || item.id === typed);
      if (!match) {
        throw new ApiError({ code: 404, message: copy.orderMissing });
      }
      return ordersApi.createReturn(match.id, values.reason, values.refund_amount, values.restock_flag, values.restock_sellable);
    },
    onSuccess: async () => {
      setNotice(null);
      await refresh();
    },
    onError: (error: unknown) => setNotice(error instanceof ApiError ? error.message : '创建失败'),
  });

  const move = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' | 'refund' }) => ordersApi.transitionReturn(id, action),
    onSuccess: async (row) => {
      setNotice(row.restock_status === 'DEFERRED' ? copy.restockLater : null);
      await refresh();
    },
    onError: (error: unknown) => setNotice(error instanceof ApiError ? error.message : '操作失败'),
  });

  return (
    <Card title={zhCN.menu.ordersReturns}>
      <OrderNav />
      <FreshnessBanner />
      {notice ? <Typography.Paragraph type="secondary">{notice}</Typography.Paragraph> : null}
      <PermissionGuard permission={Perm.ORDER_WRITE}>
        <Form
          layout="inline"
          style={{ marginBottom: 16, rowGap: 8 }}
          onFinish={(values: { order_id: string; reason: string; refund_amount: string; restock_flag?: boolean; restock_sellable?: boolean }) =>
            create.mutate({
              order_id: values.order_id,
              reason: values.reason,
              refund_amount: values.refund_amount,
              restock_flag: Boolean(values.restock_flag),
              restock_sellable: Boolean(values.restock_sellable),
            })
          }
        >
          <Form.Item name="order_id" rules={[{ required: true }]}>
            <Input placeholder={copy.orderNo} style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="reason" rules={[{ required: true }]}>
            <Input placeholder={copy.returnReason} style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="refund_amount" rules={[{ required: true }]}>
            <Input placeholder={copy.returnAmount} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="restock_flag" label={copy.restock} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="restock_sellable" label={copy.restockSellable} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={create.isPending}>
            {copy.createReturn}
          </Button>
        </Form>
      </PermissionGuard>
      <Table<ReturnOrderView>
        rowKey="id"
        loading={list.isLoading}
        dataSource={list.data ?? []}
        pagination={false}
        columns={[
          { title: copy.orderNo, dataIndex: 'platform_order_id' },
          { title: copy.returnReason, dataIndex: 'reason' },
          {
            title: copy.status,
            dataIndex: 'status',
            render: (value: string) => STATUS[value] ?? value,
          },
          {
            title: copy.returnAmount,
            render: (_, row) => <MoneyText value={row.refund_amount} currency={row.currency} />,
          },
          {
            title: copy.restock,
            render: (_, row) => (row.restock_status === 'DEFERRED' ? copy.restockLater : row.restock_flag ? '已申请' : '—'),
          },
          { title: copy.paidAt, dataIndex: 'created_at', render: (value: string) => formatDateTime(value) },
          {
            title: copy.detail,
            render: (_, row) => (
              <PermissionGuard permission={Perm.ORDER_WRITE}>
                <Space>
                  {row.status === 'REQUESTED' ? (
                    <>
                      <Button type="link" onClick={() => move.mutate({ id: row.id, action: 'approve' })}>
                        {copy.approveReturn}
                      </Button>
                      <Button type="link" onClick={() => move.mutate({ id: row.id, action: 'reject' })}>
                        {copy.rejectReturn}
                      </Button>
                    </>
                  ) : null}
                  {row.status === 'APPROVED' ? (
                    <Button type="link" onClick={() => move.mutate({ id: row.id, action: 'refund' })}>
                      {copy.completeRefund}
                    </Button>
                  ) : null}
                </Space>
              </PermissionGuard>
            ),
          },
        ]}
      />
    </Card>
  );
}
