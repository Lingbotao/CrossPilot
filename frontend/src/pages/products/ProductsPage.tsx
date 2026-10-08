import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Descriptions, Drawer, Form, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';

import { ApiError } from '@/api/client';
import { downloadBase64 } from '@/api/orders';
import { productsApi } from '@/api/products';
import {
  ErrorCode,
  Perm,
  ProductStatus,
  type ProductImportRowError,
  type ProductStatusValue,
  type SkuView,
  type SkuWrite,
  type SpuListItem,
  type SpuListQuery,
} from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';
import { formatDateTime } from '@/utils/format';

const copy = zhCN.productPage;

const STATUS_LABEL: Record<ProductStatusValue, string> = {
  DRAFT: copy.statusDraft,
  ON_SALE: copy.statusOnSale,
  STOPPED: copy.statusStopped,
  OUT_OF_STOCK: copy.statusOutOfStock,
  VIOLATION_OFF: copy.statusViolation,
};

const CURRENCIES = ['CNY', 'USD', 'SGD', 'MYR', 'THB', 'PHP', 'IDR', 'VND', 'EUR', 'GBP'];

interface SkuForm {
  sku_code: string;
  barcode?: string;
  spec?: string;
  weight_g: string;
  length_cm: string;
  width_cm: string;
  height_cm: string;
  purchase_price?: string;
  currency?: string;
}

interface SpuForm {
  title: string;
  brand?: string;
  material?: string;
  purpose?: string;
  status: ProductStatusValue;
  skus: SkuForm[];
}

interface FilterForm {
  q?: string;
  status?: ProductStatusValue;
}

function isPositiveDecimal(value: string | undefined): boolean {
  if (!value) {
    return false;
  }
  const text = value.trim();
  if (!/^\d+(\.\d+)?$/.test(text)) {
    return false;
  }
  return !/^0+(\.0+)?$/.test(text);
}

function trimDecimal(value: string): string {
  if (!value.includes('.')) {
    return value;
  }
  return value.replace(/\.?0+$/, '');
}

function measureRule() {
  return {
    validator: (_: unknown, value: string | undefined) =>
      isPositiveDecimal(value) ? Promise.resolve() : Promise.reject(new Error(copy.measureRequired)),
  };
}

function toSku(row: SkuForm, canViewCost: boolean): SkuWrite {
  const sku: SkuWrite = {
    sku_code: row.sku_code.trim(),
    barcode: row.barcode?.trim() || null,
    spec_attrs: row.spec?.trim() ? { spec: row.spec.trim() } : {},
    weight_g: row.weight_g.trim(),
    length_cm: row.length_cm.trim(),
    width_cm: row.width_cm.trim(),
    height_cm: row.height_cm.trim(),
  };
  if (canViewCost) {
    const price = row.purchase_price?.trim();
    sku.purchase_price = price || null;
    sku.currency = price ? (row.currency ?? null) : null;
  }
  return sku;
}

function statusOptions() {
  return (Object.keys(STATUS_LABEL) as ProductStatusValue[]).map((value) => ({
    value,
    label: STATUS_LABEL[value],
  }));
}

function SkuFields({ index, canViewCost }: { index?: number; canViewCost: boolean }) {
  const name = (key: string) => (index === undefined ? key : [index, key]);
  return (
    <>
      <Form.Item name={name('sku_code')} label={copy.skuCode} rules={[{ required: true, message: copy.skuRequired }]}>
        <Input />
      </Form.Item>
      <Form.Item name={name('barcode')} label={copy.barcode}>
        <Input />
      </Form.Item>
      <Form.Item name={name('spec')} label={copy.spec}>
        <Input />
      </Form.Item>
      <Form.Item name={name('weight_g')} label={copy.weight} rules={[measureRule()]}>
        <Input />
      </Form.Item>
      <Form.Item name={name('length_cm')} label={copy.length} rules={[measureRule()]}>
        <Input />
      </Form.Item>
      <Form.Item name={name('width_cm')} label={copy.width} rules={[measureRule()]}>
        <Input />
      </Form.Item>
      <Form.Item name={name('height_cm')} label={copy.height} rules={[measureRule()]}>
        <Input />
      </Form.Item>
      {canViewCost ? (
        <CostGuard>
          <Form.Item name={name('purchase_price')} label={copy.purchasePrice}>
            <Input />
          </Form.Item>
          <Form.Item name={name('currency')} label={copy.currency}>
            <Select options={CURRENCIES.map((code) => ({ value: code, label: code }))} allowClear />
          </Form.Item>
        </CostGuard>
      ) : null}
    </>
  );
}

export function ProductsPage() {
  const queryClient = useQueryClient();
  const { canViewCost } = usePermission();
  const [filters, setFilters] = useState<SpuListQuery>({ limit: 20 });
  const [extra, setExtra] = useState<SpuListItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [editingSku, setEditingSku] = useState<SkuView | null>(null);
  const [createForm] = Form.useForm<SpuForm>();
  const [spuForm] = Form.useForm<SpuForm>();
  const [skuForm] = Form.useForm<SkuForm>();
  const [editSkuForm] = Form.useForm<SkuForm>();
  const [importOpen, setImportOpen] = useState(false);
  const [importErrors, setImportErrors] = useState<ProductImportRowError[]>([]);

  const list = useQuery({
    queryKey: ['spus', filters],
    queryFn: async () => {
      const page = await productsApi.list(filters);
      setExtra([]);
      setCursor(page.page_info.cursor);
      setHasMore(page.page_info.has_more);
      return page;
    },
  });

  const detail = useQuery({
    queryKey: ['spu', openId],
    queryFn: () => productsApi.detail(openId ?? ''),
    enabled: openId !== null,
  });

  useEffect(() => {
    if (!detail.data) {
      return;
    }
    spuForm.setFieldsValue({
      title: detail.data.title,
      brand: detail.data.brand ?? undefined,
      material: detail.data.material ?? undefined,
      purpose: detail.data.purpose ?? undefined,
      status: detail.data.status,
    });
  }, [detail.data, spuForm]);

  const rows = useMemo(() => [...(list.data?.items ?? []), ...extra], [extra, list.data]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['spus'] });
    if (openId) {
      await queryClient.invalidateQueries({ queryKey: ['spu', openId] });
    }
  };

  const create = useMutation({
    mutationFn: (values: SpuForm) =>
      productsApi.create({
        title: values.title.trim(),
        brand: values.brand?.trim() || null,
        material: values.material?.trim() || null,
        purpose: values.purpose?.trim() || null,
        status: values.status,
        skus: (values.skus ?? []).map((row) => toSku(row, canViewCost)),
      }),
    onSuccess: async () => {
      setCreateOpen(false);
      feedback().message.success(copy.created);
      await refresh();
    },
  });

  const saveSpu = useMutation({
    mutationFn: (values: SpuForm) =>
      productsApi.update(openId ?? '', {
        title: values.title.trim(),
        brand: values.brand?.trim() || null,
        material: values.material?.trim() || null,
        purpose: values.purpose?.trim() || null,
        status: values.status,
      }),
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      await refresh();
    },
  });

  const addSku = useMutation({
    mutationFn: (values: SkuForm) => productsApi.addSku(openId ?? '', toSku(values, canViewCost)),
    onSuccess: async () => {
      skuForm.resetFields();
      feedback().message.success(copy.saved);
      await refresh();
    },
  });

  const saveSku = useMutation({
    mutationFn: (values: SkuForm) => productsApi.updateSku(editingSku?.id ?? '', toSku(values, canViewCost)),
    onSuccess: async () => {
      setEditingSku(null);
      feedback().message.success(copy.saved);
      await refresh();
    },
  });

  const loadMore = async () => {
    if (!cursor) {
      return;
    }
    const page = await productsApi.list({ ...filters, cursor, limit: 20 });
    setExtra((current) => [...current, ...page.items]);
    setCursor(page.page_info.cursor);
    setHasMore(page.page_info.has_more);
  };

  const openCreate = () => {
    createForm.resetFields();
    createForm.setFieldsValue({ status: ProductStatus.DRAFT, skus: [{}] });
    setCreateOpen(true);
  };

  const openSkuEdit = (sku: SkuView) => {
    setEditingSku(sku);
    editSkuForm.setFieldsValue({
      sku_code: sku.sku_code,
      barcode: sku.barcode ?? undefined,
      spec: sku.spec_attrs.spec,
      weight_g: trimDecimal(sku.weight_g),
      length_cm: trimDecimal(sku.length_cm),
      width_cm: trimDecimal(sku.width_cm),
      height_cm: trimDecimal(sku.height_cm),
      purchase_price: sku.purchase_price ? trimDecimal(sku.purchase_price) : undefined,
      currency: sku.currency ?? undefined,
    });
  };

  const exportFile = useMutation({
    mutationFn: () => productsApi.exportFile({ status: filters.status, q: filters.q }),
    onSuccess: (file) => {
      downloadBase64(file.filename, file.content_type, file.content_base64);
      if (file.truncated) {
        feedback().message.warning(copy.exportTruncated);
      }
    },
  });
  const templateFile = useMutation({
    mutationFn: () => productsApi.importTemplate(),
    onSuccess: (file) => downloadBase64(file.filename, file.content_type, file.content_base64),
  });
  const importFile = useMutation({
    mutationFn: (file: File) => productsApi.importFile(file),
    onSuccess: async (result) => {
      feedback().message.success(`${copy.imported}：${result.created}`);
      setImportErrors([]);
      setImportOpen(false);
      await queryClient.invalidateQueries({ queryKey: ['spus'] });
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError && error.code === ErrorCode.PRODUCT_IMPORT_INVALID) {
        const data = error.data as { rows?: ProductImportRowError[] } | null;
        setImportErrors(data?.rows ?? []);
        return;
      }
      if (error instanceof ApiError) {
        feedback().message.error(error.message);
      }
    },
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {copy.title}
            </Typography.Title>
            <Typography.Text type="secondary">{copy.description}</Typography.Text>
          </div>
          <Space>
            <Button loading={templateFile.isPending} onClick={() => templateFile.mutate()}>
              {copy.template}
            </Button>
            <Button loading={exportFile.isPending} onClick={() => exportFile.mutate()}>
              {copy.exportCurrent}
            </Button>
            <PermissionGuard permission={Perm.PRODUCT_WRITE}>
              <Button
                onClick={() => {
                  setImportErrors([]);
                  setImportOpen(true);
                }}
              >
                {copy.importFile}
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                {copy.create}
              </Button>
            </PermissionGuard>
          </Space>
        </Space>
        <Form<FilterForm>
          layout="inline"
          style={{ marginTop: 16 }}
          onFinish={(values) => {
            setFilters({ limit: 20, q: values.q?.trim() || undefined, status: values.status });
          }}
        >
          <Form.Item name="q" label={copy.searchTitle}>
            <Input allowClear />
          </Form.Item>
          <Form.Item name="status" label={copy.status}>
            <Select allowClear style={{ minWidth: 140 }} options={statusOptions()} />
          </Form.Item>
          <Button htmlType="submit" type="primary">
            {zhCN.common.search}
          </Button>
        </Form>
      </Card>

      <Card>
        <Table<SpuListItem>
          rowKey="id"
          loading={list.isLoading}
          dataSource={rows}
          pagination={false}
          locale={{ emptyText: copy.empty }}
          columns={[
            { title: copy.searchTitle, dataIndex: 'title' },
            { title: copy.brand, dataIndex: 'brand', render: (value: string | null) => value || '—' },
            {
              title: copy.status,
              dataIndex: 'status',
              render: (value: ProductStatusValue) => <Tag>{STATUS_LABEL[value] ?? value}</Tag>,
            },
            { title: copy.skuCount, dataIndex: 'sku_count' },
            { title: copy.updatedAt, dataIndex: 'updated_at', render: (value: string) => formatDateTime(value) },
            {
              title: zhCN.common.actions,
              render: (_, row) => (
                <Button type="link" onClick={() => setOpenId(row.id)}>
                  {copy.detail}
                </Button>
              ),
            },
          ]}
        />
        {hasMore ? (
          <Button style={{ marginTop: 16 }} onClick={() => void loadMore()}>
            {copy.loadMore}
          </Button>
        ) : null}
      </Card>

      <Modal
        title={copy.create}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        confirmLoading={create.isPending}
        width={720}
        destroyOnClose
      >
        <Form<SpuForm> form={createForm} layout="vertical" onFinish={(values) => create.mutate(values)}>
          <Form.Item name="title" label={copy.searchTitle} rules={[{ required: true, message: copy.titleRequired }]}>
            <Input />
          </Form.Item>
          <Form.Item name="brand" label={copy.brand}>
            <Input />
          </Form.Item>
          <Form.Item name="material" label={copy.material}>
            <Input />
          </Form.Item>
          <Form.Item name="purpose" label={copy.purpose}>
            <Input />
          </Form.Item>
          <Form.Item name="status" label={copy.status}>
            <Select options={statusOptions()} />
          </Form.Item>
          <Form.List name="skus">
            {(fields, { add, remove }) => (
              <Space direction="vertical" style={{ width: '100%' }}>
                {fields.map((field) => (
                  <Card
                    key={field.key}
                    size="small"
                    extra={
                      fields.length > 1 ? (
                        <Button type="link" onClick={() => remove(field.name)}>
                          {copy.removeSku}
                        </Button>
                      ) : null
                    }
                  >
                    <SkuFields index={field.name} canViewCost={canViewCost} />
                  </Card>
                ))}
                <Button onClick={() => add()}>{copy.addSku}</Button>
              </Space>
            )}
          </Form.List>
        </Form>
      </Modal>

      <Drawer title={copy.detail} open={openId !== null} width={720} onClose={() => setOpenId(null)}>
        {detail.data ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Form<SpuForm> form={spuForm} layout="vertical" onFinish={(values) => saveSpu.mutate(values)}>
              <Form.Item name="title" label={copy.searchTitle} rules={[{ required: true, message: copy.titleRequired }]}>
                <Input />
              </Form.Item>
              <Form.Item name="brand" label={copy.brand}>
                <Input />
              </Form.Item>
              <Form.Item name="material" label={copy.material}>
                <Input />
              </Form.Item>
              <Form.Item name="purpose" label={copy.purpose}>
                <Input />
              </Form.Item>
              <Form.Item name="status" label={copy.status}>
                <Select options={statusOptions()} />
              </Form.Item>
              <PermissionGuard permission={Perm.PRODUCT_WRITE}>
                <Button type="primary" htmlType="submit" loading={saveSpu.isPending}>
                  {copy.save}
                </Button>
              </PermissionGuard>
            </Form>

            <Table<SkuView>
              rowKey="id"
              pagination={false}
              dataSource={detail.data.skus}
              columns={[
                { title: copy.skuCode, dataIndex: 'sku_code' },
                {
                  title: copy.spec,
                  render: (_, row) => Object.values(row.spec_attrs).filter(Boolean).join(' / ') || '—',
                },
                {
                  title: copy.measures,
                  render: (_, row) =>
                    `${trimDecimal(row.weight_g)} g · ${trimDecimal(row.length_cm)}×${trimDecimal(row.width_cm)}×${trimDecimal(row.height_cm)} cm`,
                },
                {
                  title: copy.purchasePrice,
                  render: (_, row) => (
                    <CostGuard fallback="—">
                      <MoneyText value={row.purchase_price} currency={row.currency ?? 'CNY'} decimals={4} />
                    </CostGuard>
                  ),
                },
                {
                  title: zhCN.common.actions,
                  render: (_, row) => (
                    <PermissionGuard permission={Perm.PRODUCT_WRITE}>
                      <Button type="link" onClick={() => openSkuEdit(row)}>
                        {copy.editSku}
                      </Button>
                    </PermissionGuard>
                  ),
                },
              ]}
            />

            <PermissionGuard permission={Perm.PRODUCT_WRITE}>
              <Descriptions title={copy.addSku} />
              <Form<SkuForm> form={skuForm} layout="vertical" onFinish={(values) => addSku.mutate(values)}>
                <SkuFields canViewCost={canViewCost} />
                <Button type="primary" htmlType="submit" loading={addSku.isPending}>
                  {copy.addSku}
                </Button>
              </Form>
            </PermissionGuard>
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title={copy.editSku}
        open={editingSku !== null}
        onCancel={() => setEditingSku(null)}
        onOk={() => editSkuForm.submit()}
        confirmLoading={saveSku.isPending}
        destroyOnClose
      >
        <Form<SkuForm> form={editSkuForm} layout="vertical" onFinish={(values) => saveSku.mutate(values)}>
          <SkuFields canViewCost={canViewCost} />
        </Form>
      </Modal>

      <Modal title={copy.importTitle} open={importOpen} onCancel={() => setImportOpen(false)} footer={null} destroyOnClose>
        <Typography.Paragraph type="secondary">{copy.importHint}</Typography.Paragraph>
        <input
          type="file"
          accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) {
              importFile.mutate(file);
            }
          }}
        />
        {importErrors.length > 0 ? (
          <Table<ProductImportRowError>
            style={{ marginTop: 16 }}
            rowKey={(row) => `${row.row}-${row.column}-${row.message}`}
            pagination={false}
            dataSource={importErrors}
            columns={[
              { title: copy.importRow, dataIndex: 'row', width: 80 },
              {
                title: copy.importColumn,
                dataIndex: 'column',
                render: (value: string) => {
                  const labels: Record<string, string> = {
                    sku_code: copy.skuCode,
                    title: copy.searchTitle,
                    brand: copy.brand,
                    barcode: copy.barcode,
                    weight_g: copy.weight,
                    length_cm: copy.length,
                    width_cm: copy.width,
                    height_cm: copy.height,
                  };
                  return labels[value] || value || '—';
                },
              },
              { title: copy.importMessage, dataIndex: 'message' },
            ]}
          />
        ) : null}
      </Modal>
    </Space>
  );
}
