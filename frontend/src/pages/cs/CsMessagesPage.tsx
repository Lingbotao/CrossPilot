import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Drawer, Select, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { csApi } from '@/api/cs';
import { Perm, type CsMessageView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { label } from './labels';

const copy = zhCN.csPage;
const SLA_COLOR: Record<string, string> = { OK: 'green', WARN: 'gold', OVERDUE: 'red' };

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function CsMessagesPage() {
  const client = useQueryClient();
  const [shopId, setShopId] = useState<string>();
  const [sla, setSla] = useState<string>();
  const [status, setStatus] = useState<string>();
  const [openId, setOpenId] = useState<string>();
  const [templateId, setTemplateId] = useState<string>();
  const shops = useQuery({ queryKey: ['cs', 'shops'], queryFn: csApi.shops });
  const messages = useQuery({
    queryKey: ['cs', 'messages', shopId, sla, status],
    queryFn: () => csApi.messages({ shop_id: shopId, sla, status, limit: 50 }),
  });
  const detail = useQuery({
    queryKey: ['cs', 'message', openId],
    queryFn: () => csApi.message(openId ?? ''),
    enabled: Boolean(openId),
  });
  const templates = useQuery({ queryKey: ['cs', 'templates'], queryFn: csApi.templates });
  const preview = useQuery({
    queryKey: ['cs', 'preview', templateId, openId],
    queryFn: () => csApi.previewTemplate(templateId ?? '', openId),
    enabled: Boolean(templateId && openId),
  });
  const sync = useMutation({
    mutationFn: (id: string) => csApi.syncMessages(id),
    onSuccess: async (result) => {
      feedback().message.success(copy.synced.replace('{{count}}', String(result.messages)));
      await client.invalidateQueries({ queryKey: ['cs', 'messages'] });
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  const markRead = useMutation({
    mutationFn: (id: string) => csApi.markRead(id),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['cs'] });
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  const rows = messages.data?.items ?? [];
  const current = detail.data;
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.messagesTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.messagesDescription}</Typography.Paragraph>
      </div>
      <Card>
        <Space wrap>
          <Select
            allowClear
            placeholder={copy.shop}
            style={{ width: 220 }}
            value={shopId}
            options={(shops.data ?? []).map((shop) => ({
              value: shop.id,
              label: `${shop.shop_name} · ${shop.platform_code}`,
            }))}
            onChange={setShopId}
          />
          <Select
            allowClear
            placeholder={copy.slaFilter}
            style={{ width: 140 }}
            value={sla}
            options={['OK', 'WARN', 'OVERDUE'].map((item) => ({ value: item, label: label(copy.sla, item) }))}
            onChange={setSla}
          />
          <Select
            allowClear
            placeholder={copy.statusFilter}
            style={{ width: 140 }}
            value={status}
            options={['UNREAD', 'READ'].map((item) => ({ value: item, label: label(copy.messageStatus, item) }))}
            onChange={setStatus}
          />
          <PermissionGuard permission={Perm.CS_WRITE}>
            <Button
              type="primary"
              loading={sync.isPending}
              disabled={!shopId}
              onClick={() => {
                if (shopId) {
                  sync.mutate(shopId);
                }
              }}
            >
              {copy.sync}
            </Button>
          </PermissionGuard>
        </Space>
      </Card>
      {messages.isError ? <Alert type="error" message={messageOf(messages.error)} /> : null}
      <Table<CsMessageView>
        rowKey="id"
        loading={messages.isLoading}
        dataSource={rows}
        pagination={false}
        onRow={(row) => ({ onClick: () => setOpenId(row.id) })}
        columns={[
          { title: copy.buyer, dataIndex: 'buyer_name', render: (value: string | null) => value || '—' },
          { title: copy.platform, dataIndex: 'platform_code' },
          {
            title: copy.content,
            dataIndex: 'content',
            ellipsis: true,
          },
          {
            title: copy.slaTitle,
            dataIndex: 'sla_level',
            render: (value: string) => <Tag color={SLA_COLOR[value]}>{label(copy.sla, value)}</Tag>,
          },
          {
            title: copy.status,
            dataIndex: 'status',
            render: (value: string) => label(copy.messageStatus, value),
          },
        ]}
      />
      <Drawer
        title={copy.messageDetail}
        open={Boolean(openId)}
        width={520}
        onClose={() => {
          setOpenId(undefined);
          setTemplateId(undefined);
        }}
      >
        {detail.isError ? <Alert type="error" message={messageOf(detail.error)} /> : null}
        {current ? (
          <Space direction="vertical" size={12} style={{ display: 'flex' }}>
            <Tag color={SLA_COLOR[current.sla_level]}>{label(copy.sla, current.sla_level)}</Tag>
            <Typography.Paragraph>{current.content}</Typography.Paragraph>
            <Typography.Text type="secondary">
              {copy.deadline} {current.sla_deadline}
            </Typography.Text>
            {current.order ? (
              <Card size="small" title={copy.orderContext}>
                <div>{copy.orderNo} {current.order.platform_order_id}</div>
                <div>
                  {copy.orderStatus} {label(zhCN.order.unifiedStatus, current.order.unified_status)}
                </div>
                <div>{copy.buyer} {current.order.buyer_name || current.buyer_name || '—'}</div>
                <div>{copy.tracking} {current.order.tracking_no || '—'}</div>
                <div>
                  {copy.orderAmount} <MoneyText value={current.order.total_amount} currency={current.order.currency} />
                </div>
              </Card>
            ) : (
              <Alert type="info" message={copy.orderMissing} />
            )}
            <Space wrap>
              <a href={current.console_url} target="_blank" rel="noreferrer">
                {copy.openConsole}
              </a>
              <Link to={`/cs/tickets?message_id=${current.id}`}>{copy.toTicket}</Link>
              <PermissionGuard permission={Perm.CS_WRITE}>
                <Button size="small" loading={markRead.isPending} onClick={() => markRead.mutate(current.id)}>
                  {copy.markRead}
                </Button>
              </PermissionGuard>
            </Space>
            <Select
              allowClear
              placeholder={copy.pickTemplate}
              style={{ width: '100%' }}
              value={templateId}
              options={(templates.data ?? []).map((item) => ({
                value: item.id,
                label: `${label(copy.scenes, item.scene)} · ${item.lang} · ${item.name}`,
              }))}
              onChange={setTemplateId}
            />
            {preview.data ? (
              <Card
                size="small"
                title={copy.rendered}
                extra={
                  <Button
                    size="small"
                    onClick={() => {
                      void navigator.clipboard.writeText(preview.data?.text ?? '').then(() => {
                        feedback().message.success(copy.copied);
                      });
                    }}
                  >
                    {copy.copy}
                  </Button>
                }
              >
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{preview.data.text}</Typography.Paragraph>
                {preview.data.missing.length > 0 ? (
                  <Typography.Text type="secondary">
                    {copy.missingVars} {preview.data.missing.join('、')}
                  </Typography.Text>
                ) : null}
              </Card>
            ) : null}
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}
