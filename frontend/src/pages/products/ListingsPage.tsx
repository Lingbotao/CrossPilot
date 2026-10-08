import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';

import { listingsApi } from '@/api/listings';
import { productsApi } from '@/api/products';
import {
  ListingStatus,
  Perm,
  type AttrTemplateItem,
  type ListingBatchStatusValue,
  type ListingQuery,
  type ListingStatusValue,
  type ListingView,
  type PricePreview,
  type SpuListItem,
} from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.listingPage;
const CURRENCIES = ['CNY', 'USD', 'SGD', 'MYR', 'THB', 'PHP', 'IDR', 'VND', 'EUR', 'GBP'];

const STATUS_LABEL: Record<ListingStatusValue, string> = {
  DRAFT: copy.statusDraft,
  LINKED: copy.statusLinked,
  UNLISTED: copy.statusUnlisted,
};

interface ListingForm {
  product_query?: string;
  spu_id?: string;
  sku_id: string;
  shop_id: string;
  category_mapping_id?: string;
  platform_product_id?: string;
  platform_sku_id?: string;
  price?: string;
  currency?: string;
  status?: ListingStatusValue;
  attr_values?: Record<string, string>;
}

interface FilterForm {
  shop_id?: string;
  status?: ListingStatusValue;
  platform_product_id?: string;
  sku_id?: string;
}

function isMoney(value: string | undefined): boolean {
  if (!value?.trim()) {
    return true;
  }
  return /^\d+(\.\d+)?$/.test(value.trim());
}

const BATCH_STATUS: Record<ListingBatchStatusValue, string> = {
  PENDING: zhCN.publishPage.statusPending,
  RUNNING: zhCN.publishPage.statusRunning,
  SUCCEEDED: zhCN.publishPage.statusSucceeded,
  PARTIAL: zhCN.publishPage.statusPartial,
  FAILED: zhCN.publishPage.statusFailed,
};

export function ListingsPage() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<ListingQuery>({ limit: 20 });
  const [extra, setExtra] = useState<ListingView[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ListingView | null>(null);
  const [productQuery, setProductQuery] = useState('');
  const [spuId, setSpuId] = useState<string | undefined>();
  const [form] = Form.useForm<ListingForm>();
  const [filterForm] = Form.useForm<FilterForm>();
  const [priceForm] = Form.useForm<{ price: string; currency: string }>();
  const [selected, setSelected] = useState<ListingView[]>([]);
  const [priceOpen, setPriceOpen] = useState(false);
  const [preview, setPreview] = useState<PricePreview | null>(null);
  const [priceBatchId, setPriceBatchId] = useState<string | null>(null);
  const shopId = Form.useWatch('shop_id', form);
  const templateId = Form.useWatch('category_mapping_id', form);

  const shops = useQuery({ queryKey: ['listing-shops'], queryFn: listingsApi.shops });
  const list = useQuery({
    queryKey: ['listings', filters],
    queryFn: async () => {
      const page = await listingsApi.list(filters);
      setExtra([]);
      setCursor(page.page_info.cursor);
      setHasMore(page.page_info.has_more);
      return page;
    },
  });
  const products = useQuery({
    queryKey: ['spus', 'listing-pick', productQuery],
    queryFn: () => productsApi.list({ q: productQuery, limit: 20 }),
    enabled: open && productQuery.trim().length > 0,
  });
  const spu = useQuery({
    queryKey: ['spu', spuId],
    queryFn: () => productsApi.detail(spuId ?? ''),
    enabled: open && Boolean(spuId),
  });

  const selectedShop = shops.data?.find((item) => item.id === shopId);
  const templates = useQuery({
    queryKey: ['category-mappings', selectedShop?.platform_code, selectedShop?.site_code],
    queryFn: () =>
      listingsApi.listTemplates({
        limit: 50,
        platform_code: selectedShop?.platform_code,
        site_code: selectedShop?.site_code,
      }),
    enabled: Boolean(selectedShop),
  });
  const template = templates.data?.items.find((item) => item.id === templateId);

  const rows = useMemo(() => [...(list.data?.items ?? []), ...extra], [extra, list.data]);
  const priceBatch = useQuery({
    queryKey: ['listing-batch', priceBatchId],
    queryFn: () => listingsApi.batch(priceBatchId ?? ''),
    enabled: Boolean(priceBatchId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'PENDING' || status === 'RUNNING' ? 2000 : false;
    },
  });

  useEffect(() => {
    const status = priceBatch.data?.status;
    if (status && status !== 'PENDING' && status !== 'RUNNING') {
      void queryClient.invalidateQueries({ queryKey: ['listings'] });
    }
  }, [priceBatch.data?.status, queryClient]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['listings'] });
  };

  const save = useMutation({
    mutationFn: (values: ListingForm) => {
      const price = values.price?.trim() || null;
      const currency = values.currency ?? null;
      const payload = {
        sku_id: values.sku_id,
        shop_id: values.shop_id,
        category_mapping_id: values.category_mapping_id || null,
        platform_product_id: values.platform_product_id?.trim() || null,
        platform_sku_id: values.platform_sku_id?.trim() || null,
        price,
        currency,
        attr_values: values.attr_values ?? {},
        status: values.status ?? null,
      };
      if (editing) {
        return listingsApi.update(editing.id, payload);
      }
      return listingsApi.create(payload);
    },
    onSuccess: async () => {
      const wasEdit = editing !== null;
      setOpen(false);
      setEditing(null);
      feedback().message.success(wasEdit ? copy.saved : copy.created);
      await refresh();
    },
  });

  const openCreate = () => {
    setEditing(null);
    setProductQuery('');
    setSpuId(undefined);
    form.resetFields();
    setOpen(true);
  };

  const openEdit = (row: ListingView) => {
    setEditing(row);
    setProductQuery('');
    setSpuId(undefined);
    form.setFieldsValue({
      sku_id: row.sku_id,
      shop_id: row.shop_id,
      category_mapping_id: row.category_mapping_id ?? undefined,
      platform_product_id: row.platform_product_id ?? undefined,
      platform_sku_id: row.platform_sku_id ?? undefined,
      price: row.price ?? undefined,
      currency: row.currency ?? undefined,
      attr_values: row.attr_values,
    });
    setOpen(true);
  };

  const loadMore = async () => {
    if (!cursor) {
      return;
    }
    const page = await listingsApi.list({ ...filters, cursor });
    setExtra((current) => [...current, ...page.items]);
    setCursor(page.page_info.cursor);
    setHasMore(page.page_info.has_more);
  };

  const applyFilters = (values: FilterForm) => {
    setFilters({
      limit: 20,
      shop_id: values.shop_id,
      status: values.status,
      platform_product_id: values.platform_product_id?.trim() || undefined,
      sku_id: values.sku_id?.trim() || undefined,
    });
  };

  const pricePayload = (values: { price: string; currency: string }, confirmed: boolean) => ({
    listing_ids: selected.map((row) => row.id),
    price: values.price.trim(),
    currency: values.currency,
    confirmed,
  });

  const acceptPrice = async (values: { price: string; currency: string }, confirmed: boolean) => {
    const batch = await listingsApi.repriceBatch(pricePayload(values, confirmed));
    setPriceBatchId(batch.id);
    setPriceOpen(false);
    setSelected([]);
    feedback().message.success(copy.batchAccepted);
  };

  const previewPrice = useMutation({
    mutationFn: (values: { price: string; currency: string }) => listingsApi.previewPrices(pricePayload(values, false)),
    onSuccess: async (data, values) => {
      setPreview(data);
      if (!data.needs_confirm) {
        await acceptPrice(values, false);
      }
    },
  });

  const confirmPrice = useMutation({
    mutationFn: (values: { price: string; currency: string }) => acceptPrice(values, true),
  });

  return (
    <Card title={copy.title}>
      <Typography.Paragraph type="secondary">{copy.description}</Typography.Paragraph>
      <Form form={filterForm} layout="inline" onFinish={applyFilters} style={{ marginBottom: 16 }}>
        <Form.Item name="shop_id" label={copy.shop}>
          <Select
            allowClear
            style={{ width: 200 }}
            options={(shops.data ?? []).map((item) => ({
              value: item.id,
              label: `${item.shop_name} · ${item.site_code}`,
            }))}
          />
        </Form.Item>
        <Form.Item name="status" label={copy.status}>
          <Select
            allowClear
            style={{ width: 120 }}
            options={Object.values(ListingStatus).map((value) => ({ value, label: STATUS_LABEL[value] }))}
          />
        </Form.Item>
        <Form.Item name="platform_product_id" label={copy.platformProduct}>
          <Input allowClear />
        </Form.Item>
        <Form.Item name="sku_id" label={copy.sku}>
          <Input allowClear placeholder="SKU ID" />
        </Form.Item>
        <Button type="primary" htmlType="submit">
          {zhCN.common.search}
        </Button>
      </Form>
      <PermissionGuard permission={Perm.PRODUCT_WRITE}>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            {copy.create}
          </Button>
          <Button
            disabled={selected.length === 0}
            onClick={() => {
              setPreview(null);
              priceForm.resetFields();
              setPriceOpen(true);
            }}
          >
            {copy.reprice}
          </Button>
        </Space>
      </PermissionGuard>
      {priceBatch.data ? (
        <Typography.Paragraph>
          {zhCN.publishPage.progress}：{BATCH_STATUS[priceBatch.data.status]} · {priceBatch.data.succeeded}/
          {priceBatch.data.total}
        </Typography.Paragraph>
      ) : null}
      {(shops.data?.length ?? 0) === 0 && !shops.isLoading ? (
        <Typography.Paragraph type="secondary">{copy.noShop}</Typography.Paragraph>
      ) : null}
      <Table
        rowKey="id"
        loading={list.isLoading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: copy.empty }}
        rowSelection={{
          selectedRowKeys: selected.map((row) => row.id),
          onChange: (_, picked) => setSelected(picked),
        }}
        columns={[
          { title: copy.sku, dataIndex: 'sku_code' },
          {
            title: copy.shop,
            render: (_, row) => `${row.shop_name} · ${row.platform_code}/${row.site_code}`,
          },
          { title: copy.platformProduct, dataIndex: 'platform_product_id', render: (value: string | null) => value || '—' },
          { title: copy.platformSku, dataIndex: 'platform_sku_id', render: (value: string | null) => value || '—' },
          {
            title: copy.price,
            render: (_, row) => <MoneyText value={row.price} currency={row.currency ?? undefined} />,
          },
          { title: copy.template, dataIndex: 'template_name', render: (value: string | null) => value || '—' },
          {
            title: copy.status,
            dataIndex: 'status',
            render: (value: ListingStatusValue) => <Tag>{STATUS_LABEL[value] ?? value}</Tag>,
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
        width={640}
      >
        <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
          {editing ? (
            <Typography.Paragraph>
              {copy.sku}：{editing.sku_code} · {copy.status}：{STATUS_LABEL[editing.status]}
            </Typography.Paragraph>
          ) : null}
          <Form.Item label={copy.searchProduct}>
            <Input.Search
              allowClear
              onSearch={(value) => {
                setProductQuery(value.trim());
                setSpuId(undefined);
              }}
            />
          </Form.Item>
          <Form.Item label={copy.pickSku}>
            <Select
              allowClear
              placeholder={copy.pickSku}
              options={(products.data?.items ?? []).map((item: SpuListItem) => ({
                value: item.id,
                label: item.title,
              }))}
              onChange={(value: string) => {
                setSpuId(value);
                form.setFieldValue('sku_id', undefined);
              }}
            />
          </Form.Item>
          <Form.Item name="sku_id" label={copy.sku} rules={[{ required: true, message: copy.pickSku }]}>
            <Select
              options={(spu.data?.skus ?? []).map((item) => ({
                value: item.id,
                label: item.sku_code,
              }))}
            />
          </Form.Item>
          <Form.Item name="shop_id" label={copy.shop} rules={[{ required: true, message: copy.pickShop }]}>
            <Select
              options={(shops.data ?? []).map((item) => ({
                value: item.id,
                label: `${item.shop_name} · ${item.platform_code}/${item.site_code}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="category_mapping_id" label={copy.template}>
            <Select
              allowClear
              options={(templates.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.name} · ${item.local_category_code}`,
              }))}
            />
          </Form.Item>
          {(template?.attrs_template ?? []).map((item: AttrTemplateItem) => (
            <Form.Item
              key={item.key}
              name={['attr_values', item.key]}
              label={`${item.label}${item.required ? ' *' : ''}`}
              rules={item.required ? [{ required: true, whitespace: true, message: copy.requiredAttr }] : []}
            >
              <Input />
            </Form.Item>
          ))}
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="platform_product_id" label={copy.platformProduct}>
              <Input maxLength={128} />
            </Form.Item>
            <Form.Item name="platform_sku_id" label={copy.platformSku}>
              <Input maxLength={128} />
            </Form.Item>
          </Space>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item
              name="price"
              label={copy.price}
              dependencies={['currency']}
              rules={[
                ({ getFieldValue }) => ({
                  validator: (_, value: string | undefined) => {
                    const currency = getFieldValue('currency') as string | undefined;
                    const text = value?.trim() ?? '';
                    if (!isMoney(text)) {
                      return Promise.reject(new Error(copy.priceRule));
                    }
                    if ((text.length > 0) !== Boolean(currency)) {
                      return Promise.reject(new Error(copy.priceRule));
                    }
                    return Promise.resolve();
                  },
                }),
              ]}
            >
              <Input />
            </Form.Item>
            <Form.Item name="currency" label={copy.currency}>
              <Select allowClear style={{ width: 120 }} options={CURRENCIES.map((code) => ({ value: code, label: code }))} />
            </Form.Item>
          </Space>
          <Form.Item name="status" label={copy.status} extra={copy.statusHint}>
            <Select
              allowClear
              options={Object.values(ListingStatus).map((value) => ({ value, label: STATUS_LABEL[value] }))}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={copy.reprice}
        open={priceOpen}
        onCancel={() => setPriceOpen(false)}
        footer={[
          <Button key="preview" loading={previewPrice.isPending} onClick={() => priceForm.submit()}>
            {copy.preview}
          </Button>,
          preview?.needs_confirm ? (
            <Button
              key="confirm"
              type="primary"
              loading={confirmPrice.isPending}
              onClick={() => {
                void priceForm.validateFields().then((values) => confirmPrice.mutate(values));
              }}
            >
              {copy.confirmReprice}
            </Button>
          ) : null,
        ]}
      >
        <Typography.Paragraph type="secondary">{copy.repriceHint}</Typography.Paragraph>
        <Form form={priceForm} layout="vertical" onFinish={(values) => previewPrice.mutate(values)}>
          <Space align="start">
            <Form.Item
              name="price"
              label={copy.newPrice}
              rules={[
                { required: true, message: copy.priceRule },
                {
                  validator: (_, value: string | undefined) =>
                    value && isMoney(value) ? Promise.resolve() : Promise.reject(new Error(copy.priceRule)),
                },
              ]}
            >
              <Input />
            </Form.Item>
            <Form.Item name="currency" label={copy.currency} rules={[{ required: true, message: copy.priceRule }]}>
              <Select style={{ width: 120 }} options={CURRENCIES.map((code) => ({ value: code, label: code }))} />
            </Form.Item>
          </Space>
        </Form>
        {preview?.needs_confirm ? (
          <>
            <Typography.Text type="warning">{copy.needsConfirm}</Typography.Text>
            {preview.lines
              .filter((line) => line.needs_confirm)
              .map((line) => (
                <div key={line.listing_id}>
                  <MoneyText value={line.price_before} currency={line.currency_before ?? undefined} />
                  {' → '}
                  <MoneyText value={line.price_after} currency={line.currency_after} />
                </div>
              ))}
          </>
        ) : null}
      </Modal>
    </Card>
  );
}
