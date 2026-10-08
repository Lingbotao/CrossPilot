import { useMutation, useQuery } from '@tanstack/react-query';
import { Button, Card, Form, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';

import { listingsApi } from '@/api/listings';
import { productsApi } from '@/api/products';
import {
  Perm,
  type ListingBatchItemStatusValue,
  type ListingBatchStatusValue,
  type ListingBatchView,
  type SpuListItem,
} from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.publishPage;
const CURRENCIES = ['CNY', 'USD', 'SGD', 'MYR', 'THB', 'PHP', 'IDR', 'VND', 'EUR', 'GBP'];

const BATCH_STATUS: Record<ListingBatchStatusValue, string> = {
  PENDING: copy.statusPending,
  RUNNING: copy.statusRunning,
  SUCCEEDED: copy.statusSucceeded,
  PARTIAL: copy.statusPartial,
  FAILED: copy.statusFailed,
};

const ITEM_STATUS: Record<ListingBatchItemStatusValue, string> = {
  PENDING: copy.itemPending,
  SUCCEEDED: copy.itemSucceeded,
  FAILED: copy.itemFailed,
  SKIPPED: copy.itemSkipped,
};

interface PublishForm {
  sku_ids: string[];
  shop_ids: string[];
  price: string;
  currency: string;
}

function isMoney(value: string | undefined): boolean {
  if (!value?.trim()) {
    return false;
  }
  return /^\d+(\.\d+)?$/.test(value.trim());
}

function stillRunning(status: ListingBatchStatusValue | undefined): boolean {
  return status === 'PENDING' || status === 'RUNNING';
}

export function PublishPage() {
  const [form] = Form.useForm<PublishForm>();
  const [productQuery, setProductQuery] = useState('');
  const [spuId, setSpuId] = useState<string | undefined>();
  const [batchId, setBatchId] = useState<string | null>(null);
  const shops = useQuery({ queryKey: ['listing-shops'], queryFn: listingsApi.shops });
  const products = useQuery({
    queryKey: ['spus', 'publish-pick', productQuery],
    queryFn: () => productsApi.list({ q: productQuery, limit: 20 }),
    enabled: productQuery.trim().length > 0,
  });
  const spu = useQuery({
    queryKey: ['spu', spuId],
    queryFn: () => productsApi.detail(spuId ?? ''),
    enabled: Boolean(spuId),
  });
  const batch = useQuery({
    queryKey: ['listing-batch', batchId],
    queryFn: () => listingsApi.batch(batchId ?? ''),
    enabled: Boolean(batchId),
    refetchInterval: (query) => (stillRunning(query.state.data?.status) ? 2000 : false),
  });

  const submit = useMutation({
    mutationFn: (values: PublishForm) =>
      listingsApi.publishBatch({
        sku_ids: values.sku_ids,
        shop_ids: values.shop_ids,
        price: values.price.trim(),
        currency: values.currency,
      }),
    onSuccess: (data: ListingBatchView) => {
      setBatchId(data.id);
      feedback().message.success(copy.accepted);
    },
  });

  const skuCode = new Map((spu.data?.skus ?? []).map((item) => [item.id, item.sku_code]));
  const shopName = new Map((shops.data ?? []).map((item) => [item.id, `${item.shop_name} · ${item.site_code}`]));
  const progress = batch.data;

  return (
    <Card title={copy.title}>
      <Typography.Paragraph type="secondary">{copy.description}</Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={(values) => submit.mutate(values)} style={{ maxWidth: 720 }}>
        <Form.Item label={copy.searchProduct}>
          <Input.Search
            allowClear
            onSearch={(value) => {
              setProductQuery(value.trim());
              setSpuId(undefined);
              form.setFieldValue('sku_ids', []);
            }}
          />
        </Form.Item>
        <Form.Item label={copy.pickProduct}>
          <Select
            allowClear
            placeholder={copy.pickProduct}
            options={(products.data?.items ?? []).map((item: SpuListItem) => ({
              value: item.id,
              label: item.title,
            }))}
            onChange={(value: string) => {
              setSpuId(value);
              form.setFieldValue('sku_ids', []);
            }}
          />
        </Form.Item>
        <Form.Item name="sku_ids" label={copy.sku} rules={[{ required: true, message: copy.sku }]}>
          <Select
            mode="multiple"
            options={(spu.data?.skus ?? []).map((item) => ({ value: item.id, label: item.sku_code }))}
          />
        </Form.Item>
        <Form.Item name="shop_ids" label={copy.shop} rules={[{ required: true, message: copy.shop }]}>
          <Select
            mode="multiple"
            options={(shops.data ?? []).map((item) => ({
              value: item.id,
              label: `${item.shop_name} · ${item.platform_code}/${item.site_code}`,
            }))}
          />
        </Form.Item>
        <Space align="start">
          <Form.Item
            name="price"
            label={copy.price}
            rules={[
              { required: true, message: copy.priceRule },
              {
                validator: (_, value: string | undefined) =>
                  isMoney(value) ? Promise.resolve() : Promise.reject(new Error(copy.priceRule)),
              },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="currency" label={copy.currency} rules={[{ required: true, message: copy.priceRule }]}>
            <Select style={{ width: 120 }} options={CURRENCIES.map((code) => ({ value: code, label: code }))} />
          </Form.Item>
        </Space>
        <PermissionGuard permission={Perm.PRODUCT_WRITE}>
          <Button type="primary" htmlType="submit" loading={submit.isPending}>
            {copy.submit}
          </Button>
        </PermissionGuard>
      </Form>
      <Typography.Title level={5} style={{ marginTop: 24 }}>
        {copy.progress}
      </Typography.Title>
      {progress ? (
        <Typography.Paragraph>
          {BATCH_STATUS[progress.status]} · {copy.total} {progress.total} · {copy.succeeded} {progress.succeeded} ·{' '}
          {copy.failed} {progress.failed} · {copy.skipped} {progress.skipped}
        </Typography.Paragraph>
      ) : (
        <Typography.Paragraph type="secondary">{copy.empty}</Typography.Paragraph>
      )}
      <Table
        rowKey="id"
        pagination={false}
        loading={batch.isFetching && stillRunning(progress?.status)}
        dataSource={progress?.items ?? []}
        locale={{ emptyText: copy.empty }}
        columns={[
          {
            title: copy.sku,
            render: (_, row) => skuCode.get(row.sku_id) ?? row.sku_id,
          },
          {
            title: copy.shop,
            render: (_, row) => shopName.get(row.shop_id) ?? row.shop_id,
          },
          {
            title: zhCN.listingPage.status,
            dataIndex: 'status',
            render: (value: ListingBatchItemStatusValue) => <Tag>{ITEM_STATUS[value] ?? value}</Tag>,
          },
          {
            title: copy.error,
            dataIndex: 'error_message',
            render: (value: string | null) => value || '—',
          },
        ]}
      />
    </Card>
  );
}
