import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Drawer, Form, Input, Select, Space, Table, Typography } from 'antd';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { csApi, type CsTicketCreate } from '@/api/cs';
import { Perm, type CsTicketView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { label } from './labels';

const copy = zhCN.csPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function CsTicketsPage() {
  const client = useQueryClient();
  const [params] = useSearchParams();
  const messageId = params.get('message_id') ?? undefined;
  const [openId, setOpenId] = useState<string>();
  const [form] = Form.useForm<CsTicketCreate>();
  const [note, setNote] = useState('');
  const [resolution, setResolution] = useState('');
  const [assignee, setAssignee] = useState<string>();
  const tickets = useQuery({
    queryKey: ['cs', 'tickets'],
    queryFn: () => csApi.tickets({ limit: 50 }),
  });
  const detail = useQuery({
    queryKey: ['cs', 'ticket', openId],
    queryFn: () => csApi.ticket(openId ?? ''),
    enabled: Boolean(openId),
  });
  const source = useQuery({
    queryKey: ['cs', 'message', messageId],
    queryFn: () => csApi.message(messageId ?? ''),
    enabled: Boolean(messageId),
  });
  const assignees = useQuery({ queryKey: ['cs', 'assignees'], queryFn: csApi.assignees });
  const invalidate = async () => {
    await client.invalidateQueries({ queryKey: ['cs', 'tickets'] });
    await client.invalidateQueries({ queryKey: ['cs', 'ticket'] });
  };
  const create = useMutation({
    mutationFn: (payload: CsTicketCreate) => csApi.createTicket(payload),
    onSuccess: async (row) => {
      form.resetFields();
      feedback().message.success(copy.ticketSaved);
      setOpenId(row.id);
      await invalidate();
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  const assign = useMutation({
    mutationFn: () => csApi.assignTicket(openId ?? '', assignee ?? ''),
    onSuccess: async () => {
      feedback().message.success(copy.assigned);
      await invalidate();
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  const addNote = useMutation({
    mutationFn: () => csApi.noteTicket(openId ?? '', note),
    onSuccess: async () => {
      setNote('');
      await invalidate();
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  const close = useMutation({
    mutationFn: () => csApi.closeTicket(openId ?? '', resolution),
    onSuccess: async () => {
      setResolution('');
      feedback().message.success(copy.closed);
      await invalidate();
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  const current = detail.data;
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.ticketsTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.ticketsDescription}</Typography.Paragraph>
      </div>
      <PermissionGuard permission={Perm.CS_WRITE}>
        <Card>
          {source.data ? (
            <Alert
              type="info"
              style={{ marginBottom: 12 }}
              message={`${copy.fromMessage} ${source.data.buyer_name || source.data.platform_message_id}`}
            />
          ) : null}
          <Form
            form={form}
            layout="inline"
            onFinish={(values) =>
              create.mutate({
                ...values,
                message_id: messageId,
                shop_id: values.shop_id || source.data?.shop_id,
                order_id: values.order_id || source.data?.order_id || undefined,
              })
            }
          >
            <Form.Item name="title" rules={[{ required: true, message: copy.ticketTitle }]}>
              <Input placeholder={copy.ticketTitle} style={{ width: 220 }} />
            </Form.Item>
            <Form.Item name="ticket_type" initialValue="INQUIRY" rules={[{ required: true }]}>
              <Select
                style={{ width: 140 }}
                options={['INQUIRY', 'RETURN', 'REFUND', 'OTHER'].map((item) => ({
                  value: item,
                  label: label(copy.ticketTypes, item),
                }))}
              />
            </Form.Item>
            <Form.Item name="return_order_id">
              <Input placeholder={copy.returnId} style={{ width: 180 }} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={create.isPending}>
              {copy.createTicket}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
      {tickets.isError ? <Alert type="error" message={messageOf(tickets.error)} /> : null}
      <Table<CsTicketView>
        rowKey="id"
        loading={tickets.isLoading}
        dataSource={tickets.data?.items ?? []}
        pagination={false}
        onRow={(row) => ({ onClick: () => setOpenId(row.id) })}
        columns={[
          { title: copy.ticketTitle, dataIndex: 'title' },
          { title: copy.ticketType, dataIndex: 'ticket_type', render: (value: string) => label(copy.ticketTypes, value) },
          { title: copy.status, dataIndex: 'status', render: (value: string) => label(copy.ticketStatus, value) },
          { title: copy.buyer, dataIndex: 'buyer_name', render: (value: string | null) => value || '—' },
        ]}
      />
      <Drawer title={copy.ticketDetail} open={Boolean(openId)} width={520} onClose={() => setOpenId(undefined)}>
        {detail.isError ? <Alert type="error" message={messageOf(detail.error)} /> : null}
        {current ? (
          <Space direction="vertical" size={12} style={{ display: 'flex' }}>
            <Typography.Title level={5}>{current.title}</Typography.Title>
            <div>{label(copy.ticketStatus, current.status)}</div>
            {current.order ? (
              <div>
                {copy.orderNo} {current.order.platform_order_id} · {current.order.unified_status}
              </div>
            ) : (
              <Typography.Text type="secondary">{copy.orderMissing}</Typography.Text>
            )}
            {current.return_order ? (
              <Card size="small" title={copy.returnLink}>
                <div>{label(copy.returnStatus, current.return_order.status)}</div>
                <div>{current.return_order.reason}</div>
                <MoneyText value={current.return_order.refund_amount} currency={current.return_order.currency} />
                <div>
                  <Link to="/orders/returns">{copy.openReturns}</Link>
                </div>
              </Card>
            ) : (
              <Link to="/orders/returns">{copy.openReturns}</Link>
            )}
            {(current.notes ?? []).map((item) => (
              <Card key={item.id} size="small">
                <Typography.Text type="secondary">{item.author_name || copy.noteAuthor}</Typography.Text>
                <div>{item.body}</div>
              </Card>
            ))}
            <PermissionGuard permission={Perm.CS_WRITE}>
              <Space direction="vertical" style={{ display: 'flex' }}>
                <Select
                  allowClear
                  placeholder={copy.assignee}
                  value={assignee}
                  options={(assignees.data ?? []).map((item) => ({
                    value: item.user_id,
                    label: item.display_name || item.user_id,
                  }))}
                  onChange={setAssignee}
                />
                <Button disabled={!assignee || !openId} loading={assign.isPending} onClick={() => assign.mutate()}>
                  {copy.assign}
                </Button>
                <Input.TextArea rows={3} value={note} placeholder={copy.note} onChange={(event) => setNote(event.target.value)} />
                <Button disabled={!note.trim() || !openId} loading={addNote.isPending} onClick={() => addNote.mutate()}>
                  {copy.addNote}
                </Button>
                <Input.TextArea
                  rows={2}
                  value={resolution}
                  placeholder={copy.resolution}
                  onChange={(event) => setResolution(event.target.value)}
                />
                <Button
                  danger
                  disabled={!resolution.trim() || !openId}
                  loading={close.isPending}
                  onClick={() => close.mutate()}
                >
                  {copy.closeTicket}
                </Button>
              </Space>
            </PermissionGuard>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}
