import { useQuery } from '@tanstack/react-query';
import { Button, Card, DatePicker, Form, Input, Select, Space, Table, Tag, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';

import { auditApi } from '@/api/audit';
import type { AuditLog, AuditLogQuery } from '@/api/types';
import zhCN from '@/i18n/zh-CN';
import { formatDateTime } from '@/utils/format';

interface FilterForm {
  action?: string;
  resource?: string;
  user_id?: string;
  range?: [Dayjs, Dayjs];
}

const ACTIONS = [
  'LOGIN',
  'LOGOUT',
  'LOGIN_FAILED',
  'TENANT_REGISTER',
  'MEMBER_INVITE',
  'MEMBER_ACCEPT',
  'MEMBER_ROLE_CHANGE',
  'MEMBER_SCOPE_CHANGE',
  'MEMBER_REMOVE',
  'SHOP_GRANT',
  'SHOP_REVOKE',
  'BATCH_SHIP',
  'BATCH_UPDATE',
  'DATA_DELETE',
  'DATA_EXPORT',
  'COST_UPDATE',
  'ROLE_CHANGE',
  'SYSTEM_CONFIG',
];

export function AuditPage() {
  const [form] = Form.useForm<FilterForm>();
  const [query, setQuery] = useState<AuditLogQuery>({ limit: 200 });
  const logs = useQuery({ queryKey: ['audit-logs', query], queryFn: () => auditApi.list(query) });

  const search = (values: FilterForm) => {
    setQuery({
      action: values.action,
      resource: values.resource?.trim() || undefined,
      user_id: values.user_id?.trim() || undefined,
      created_from: values.range?.[0].startOf('day').toISOString(),
      created_to: values.range?.[1].endOf('day').toISOString(),
      limit: 200,
    });
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ margin: 0 }}>{zhCN.audit.title}</Typography.Title>
        <Typography.Text type="secondary">{zhCN.audit.description}</Typography.Text>
      </Card>
      <Card>
        <Form form={form} layout="inline" onFinish={search}>
          <Form.Item name="action" label={zhCN.audit.action}>
            <Select allowClear showSearch style={{ width: 190 }} options={ACTIONS.map((value) => ({ value }))} />
          </Form.Item>
          <Form.Item name="resource" label={zhCN.audit.resource}>
            <Input allowClear />
          </Form.Item>
          <Form.Item name="user_id" label={zhCN.audit.userId}>
            <Input allowClear />
          </Form.Item>
          <Form.Item name="range" label={zhCN.audit.timeRange}>
            <DatePicker.RangePicker showTime />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">{zhCN.common.search}</Button>
              <Button onClick={() => { form.resetFields(); setQuery({ limit: 200 }); }}>
                {zhCN.common.reset}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
      <Card>
        <Table
          rowKey="id"
          loading={logs.isLoading}
          dataSource={logs.data?.items ?? []}
          scroll={{ x: 1000 }}
          columns={[
            { title: zhCN.audit.time, render: (_, row) => formatDateTime(row.created_at), width: 180 },
            { title: zhCN.audit.action, render: (_, row) => <Tag color="blue">{row.action}</Tag> },
            { title: zhCN.audit.resource, dataIndex: 'resource' },
            { title: zhCN.audit.resourceId, render: (_, row: AuditLog) => row.resource_id || '—' },
            { title: zhCN.audit.userId, render: (_, row: AuditLog) => row.user_id || '—' },
            { title: 'IP', render: (_, row: AuditLog) => row.ip || '—' },
            { title: zhCN.audit.requestId, render: (_, row: AuditLog) => row.request_id || '—' },
          ]}
          pagination={false}
        />
      </Card>
    </Space>
  );
}
