import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Select, Space, Table, Typography } from 'antd';

import { ApiError } from '@/api/client';
import { csApi, type CsTemplateWrite } from '@/api/cs';
import { Perm, type CsTemplateView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { label } from './labels';

const copy = zhCN.csPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

export function CsTemplatesPage() {
  const client = useQueryClient();
  const [form] = Form.useForm<CsTemplateWrite>();
  const templates = useQuery({ queryKey: ['cs', 'templates'], queryFn: csApi.templates });
  const create = useMutation({
    mutationFn: (payload: CsTemplateWrite) => csApi.createTemplate(payload),
    onSuccess: async () => {
      form.resetFields();
      feedback().message.success(copy.templateSaved);
      await client.invalidateQueries({ queryKey: ['cs', 'templates'] });
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => csApi.deleteTemplate(id),
    onSuccess: async () => {
      feedback().message.success(copy.templateRemoved);
      await client.invalidateQueries({ queryKey: ['cs', 'templates'] });
    },
    onError: (error: unknown) => feedback().message.error(messageOf(error)),
  });
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Typography.Title level={3}>{copy.templatesTitle}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.templatesDescription}</Typography.Paragraph>
      </div>
      <PermissionGuard permission={Perm.CS_WRITE}>
        <Card>
          <Form
            form={form}
            layout="vertical"
            onFinish={(values) => create.mutate(values)}
          >
            <Space wrap align="start">
              <Form.Item name="scene" label={copy.scene} rules={[{ required: true, message: copy.scene }]}>
                <Select
                  style={{ width: 160 }}
                  options={['SHIPPING', 'REFUND', 'DELAY', 'OTHER'].map((item) => ({
                    value: item,
                    label: label(copy.scenes, item),
                  }))}
                />
              </Form.Item>
              <Form.Item name="lang" label={copy.lang} rules={[{ required: true, message: copy.lang }]}>
                <Input placeholder="zh-CN" style={{ width: 120 }} />
              </Form.Item>
              <Form.Item name="name" label={copy.templateName} rules={[{ required: true, message: copy.templateName }]}>
                <Input style={{ width: 180 }} />
              </Form.Item>
            </Space>
            <Form.Item name="body" label={copy.templateBody} rules={[{ required: true, message: copy.templateBody }]}>
              <Input.TextArea rows={4} placeholder={copy.templateHint} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={create.isPending}>
              {copy.saveTemplate}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>
      {templates.isError ? <Alert type="error" message={messageOf(templates.error)} /> : null}
      <Table<CsTemplateView>
        rowKey="id"
        loading={templates.isLoading}
        dataSource={templates.data ?? []}
        pagination={false}
        columns={[
          { title: copy.scene, dataIndex: 'scene', render: (value: string) => label(copy.scenes, value) },
          { title: copy.lang, dataIndex: 'lang' },
          { title: copy.templateName, dataIndex: 'name' },
          { title: copy.templateBody, dataIndex: 'body', ellipsis: true },
          {
            title: copy.actions,
            render: (_value, row) => (
              <PermissionGuard permission={Perm.CS_WRITE}>
                <Button size="small" danger loading={remove.isPending} onClick={() => remove.mutate(row.id)}>
                  {copy.removeTemplate}
                </Button>
              </PermissionGuard>
            ),
          },
        ]}
      />
    </Space>
  );
}
