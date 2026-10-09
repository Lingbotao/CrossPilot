import { useMutation, useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Space, Table, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { inventoryApi } from '@/api/inventory';
import { downloadBase64 } from '@/api/orders';
import type { InventoryFlowView } from '@/api/types';
import { feedback } from '@/app/feedback';
import zhCN from '@/i18n/zh-CN';
import { formatDateTime } from '@/utils/format';

const copy = zhCN.inventoryPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function FlowsPage() {
  const [filters, setFilters] = useState<{ ref_type?: string; ref_id?: string }>({});
  const flows = useQuery({
    queryKey: ['inventory-flows', filters],
    queryFn: () => inventoryApi.listFlows({ limit: 100, ...filters }),
  });
  const exportFile = useMutation({
    mutationFn: () => inventoryApi.exportFlows(filters),
    onSuccess: (file) => downloadBase64(file.filename, file.content_type, file.content_base64),
    onError: (error) => feedback().message.error(messageOf(error)),
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.flowsTitle}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.flowsDescription}</Typography.Paragraph>
        <Form
          layout="inline"
          onFinish={(values: { ref_type?: string; ref_id?: string }) => {
            setFilters({
              ref_type: values.ref_type?.trim() || undefined,
              ref_id: values.ref_id?.trim() || undefined,
            });
          }}
        >
          <Form.Item name="ref_type">
            <Input placeholder={copy.refType} />
          </Form.Item>
          <Form.Item name="ref_id">
            <Input placeholder={copy.refId} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">
              {zhCN.common.search}
            </Button>
          </Form.Item>
          <Form.Item>
            <Button loading={exportFile.isPending} onClick={() => exportFile.mutate()}>
              {zhCN.common.export}
            </Button>
          </Form.Item>
        </Form>
      </Card>
      {flows.isError ? <Alert type="error" message={messageOf(flows.error)} /> : null}
      <Card>
        <Table<InventoryFlowView>
          rowKey="id"
          loading={flows.isLoading}
          dataSource={flows.data?.items ?? []}
          pagination={false}
          columns={[
            { title: copy.when, dataIndex: 'created_at', render: (value: string) => formatDateTime(value) },
            { title: copy.sku, dataIndex: 'sku_id' },
            { title: copy.warehouse, dataIndex: 'warehouse_id' },
            { title: copy.flowType, dataIndex: 'flow_type' },
            { title: copy.quantity, dataIndex: 'quantity' },
            { title: copy.before, dataIndex: 'before_qty' },
            { title: copy.after, dataIndex: 'after_qty' },
            { title: copy.refType, dataIndex: 'ref_type', render: (value: string) => value || '—' },
            { title: copy.refId, dataIndex: 'ref_id', render: (value: string | null) => value || '—' },
          ]}
        />
      </Card>
    </Space>
  );
}
