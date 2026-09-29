import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Form, Input, Modal, Select, Space, Switch, Table, Typography } from 'antd';
import { useMemo, useState } from 'react';

import { listingsApi } from '@/api/listings';
import { Perm, type AttrTemplateItem, type CategoryMappingView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.categoryPage;

interface AttrForm {
  key: string;
  label: string;
  required?: boolean;
}

interface TemplateForm {
  name: string;
  platform_code: string;
  site_code: string;
  platform_category_id: string;
  local_category_code: string;
  attrs: AttrForm[];
}

function toAttrs(rows: AttrForm[] | undefined): AttrTemplateItem[] {
  return (rows ?? [])
    .filter((row) => row.key?.trim() && row.label?.trim())
    .map((row) => ({
      key: row.key.trim(),
      label: row.label.trim(),
      required: Boolean(row.required),
    }));
}

export function CategoryTemplatesPage() {
  const queryClient = useQueryClient();
  const [extra, setExtra] = useState<CategoryMappingView[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<CategoryMappingView | null>(null);
  const [form] = Form.useForm<TemplateForm>();
  const platform = Form.useWatch('platform_code', form);

  const catalog = useQuery({ queryKey: ['category-catalog'], queryFn: listingsApi.catalog });
  const list = useQuery({
    queryKey: ['category-mappings'],
    queryFn: async () => {
      const page = await listingsApi.listTemplates({ limit: 20 });
      setExtra([]);
      setCursor(page.page_info.cursor);
      setHasMore(page.page_info.has_more);
      return page;
    },
  });

  const sites = useMemo(() => {
    const match = catalog.data?.find((item) => item.code === platform);
    return match?.sites ?? [];
  }, [catalog.data, platform]);

  const rows = useMemo(() => [...(list.data?.items ?? []), ...extra], [extra, list.data]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['category-mappings'] });
  };

  const save = useMutation({
    mutationFn: (values: TemplateForm) => {
      const payload = {
        name: values.name.trim(),
        platform_code: values.platform_code,
        site_code: values.site_code,
        platform_category_id: values.platform_category_id.trim(),
        local_category_code: values.local_category_code.trim(),
        attrs_template: toAttrs(values.attrs),
      };
      if (editing) {
        return listingsApi.updateTemplate(editing.id, payload);
      }
      return listingsApi.createTemplate(payload);
    },
    onSuccess: async () => {
      setOpen(false);
      setEditing(null);
      feedback().message.success(editing ? copy.saved : copy.created);
      await refresh();
    },
  });

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ attrs: [{ required: true }] });
    setOpen(true);
  };

  const openEdit = (row: CategoryMappingView) => {
    setEditing(row);
    form.setFieldsValue({
      name: row.name,
      platform_code: row.platform_code,
      site_code: row.site_code,
      platform_category_id: row.platform_category_id,
      local_category_code: row.local_category_code,
      attrs: row.attrs_template,
    });
    setOpen(true);
  };

  const loadMore = async () => {
    if (!cursor) {
      return;
    }
    const page = await listingsApi.listTemplates({ limit: 20, cursor });
    setExtra((current) => [...current, ...page.items]);
    setCursor(page.page_info.cursor);
    setHasMore(page.page_info.has_more);
  };

  return (
    <Card title={copy.title}>
      <Typography.Paragraph type="secondary">{copy.description}</Typography.Paragraph>
      <PermissionGuard permission={Perm.PRODUCT_WRITE}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} style={{ marginBottom: 16 }}>
          {copy.create}
        </Button>
      </PermissionGuard>
      <Table
        rowKey="id"
        loading={list.isLoading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: copy.empty }}
        columns={[
          { title: copy.name, dataIndex: 'name' },
          { title: copy.platform, dataIndex: 'platform_code' },
          { title: copy.site, dataIndex: 'site_code' },
          { title: copy.localCode, dataIndex: 'local_category_code' },
          { title: copy.platformCategory, dataIndex: 'platform_category_id' },
          {
            title: copy.attrs,
            dataIndex: 'attrs_template',
            render: (value: AttrTemplateItem[]) =>
              value.map((item) => `${item.label}${item.required ? '*' : ''}`).join('、') || '—',
          },
          {
            title: zhCN.common.actions,
            render: (_, row) => (
              <PermissionGuard permission={Perm.PRODUCT_WRITE}>
                <Button type="link" onClick={() => openEdit(row)}>
                  {copy.edit}
                </Button>
              </PermissionGuard>
            ),
          },
        ]}
      />
      {hasMore ? (
        <Button style={{ marginTop: 16 }} onClick={() => void loadMore()}>
          {copy.loadMore}
        </Button>
      ) : null}
      <Modal
        title={editing ? copy.edit : copy.create}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
          <Form.Item name="name" label={copy.name} rules={[{ required: true, message: copy.nameRequired }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="platform_code" label={copy.platform} rules={[{ required: true }]}>
              <Select
                style={{ width: 160 }}
                options={(catalog.data ?? []).map((item) => ({ value: item.code, label: item.name }))}
                onChange={() => form.setFieldValue('site_code', undefined)}
              />
            </Form.Item>
            <Form.Item name="site_code" label={copy.site} rules={[{ required: true }]}>
              <Select style={{ width: 120 }} options={sites.map((item) => ({ value: item, label: item }))} />
            </Form.Item>
          </Space>
          <Form.Item
            name="local_category_code"
            label={copy.localCode}
            rules={[{ required: true, pattern: /^[A-Za-z0-9_-]{1,64}$/, message: copy.codeRequired }]}
          >
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item
            name="platform_category_id"
            label={copy.platformCategory}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={128} />
          </Form.Item>
          <Form.List name="attrs">
            {(fields, { add, remove }) => (
              <div>
                {fields.map((field) => (
                  <Space key={field.key} align="start" style={{ display: 'flex' }}>
                    <Form.Item
                      name={[field.name, 'key']}
                      label={copy.attrKey}
                      rules={[{ required: true, pattern: /^[A-Za-z0-9_]{1,64}$/ }]}
                    >
                      <Input />
                    </Form.Item>
                    <Form.Item name={[field.name, 'label']} label={copy.attrLabel} rules={[{ required: true }]}>
                      <Input />
                    </Form.Item>
                    <Form.Item name={[field.name, 'required']} label={copy.required} valuePropName="checked">
                      <Switch />
                    </Form.Item>
                    <Button type="link" onClick={() => remove(field.name)}>
                      {copy.removeAttr}
                    </Button>
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add({ required: false })} block>
                  {copy.addAttr}
                </Button>
              </div>
            )}
          </Form.List>
        </Form>
      </Modal>
    </Card>
  );
}
