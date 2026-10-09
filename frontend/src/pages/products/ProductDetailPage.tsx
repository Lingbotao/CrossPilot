import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Select, Space, Table, Tabs, Tag, Typography } from 'antd';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { complianceApi } from '@/api/compliance';
import { listingsApi } from '@/api/listings';
import { localeApi } from '@/api/locale';
import { productsApi } from '@/api/products';
import {
  CONTENT_MARKETS,
  Perm,
  type CertificateView,
  type ContentQuality,
  type ListingContentView,
  type ListingView,
  type ProductImageView,
  type SkuView,
  type SpuHsBindingView,
} from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.productDetail;
const quality = zhCN.localePage;

const QUALITY_LABEL: Record<ContentQuality, string> = {
  UNTRANSLATED: quality.statusUntranslated,
  MT_DRAFT: quality.statusDraft,
  REVIEWED: quality.statusReviewed,
  PUBLISHED: quality.statusPublished,
};

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

function ProductHsBindings({ spuId }: { spuId: string }) {
  const bindings = useQuery({
    queryKey: ['spu-hs-bindings', spuId],
    queryFn: () => complianceApi.bindings(spuId),
  });
  if (bindings.isError) {
    return <Alert type="error" message={messageOf(bindings.error)} />;
  }
  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Typography.Text strong>{copy.hsTitle}</Typography.Text>
      <Table<SpuHsBindingView>
        rowKey="id"
        loading={bindings.isLoading}
        pagination={false}
        dataSource={bindings.data ?? []}
        locale={{ emptyText: copy.hsEmpty }}
        columns={[
          { title: copy.hsMarket, dataIndex: 'market', width: 80 },
          { title: copy.hsCode, dataIndex: 'code', width: 140 },
          { title: copy.hsDescription, dataIndex: 'description' },
          { title: copy.hsBasis, dataIndex: 'basis' },
          {
            title: copy.hsUpdated,
            dataIndex: 'updated_at',
            width: 200,
            render: (value: string) => new Date(value).toLocaleString('zh-CN'),
          },
        ]}
      />
    </Space>
  );
}

function ProductCertificates({ spuId, skus }: { spuId: string; skus: SkuView[] }) {
  const [form] = Form.useForm<{ sku_id: string; market: string; category_code: string }>();
  const [gapQuery, setGapQuery] = useState<{ sku_id: string; market: string; category_code: string } | null>(null);
  const certificates = useQuery({
    queryKey: ['spu-certificates', spuId],
    queryFn: () => complianceApi.certificates({ spu_id: spuId }),
  });
  const gaps = useQuery({
    queryKey: ['product-cert-gaps', gapQuery],
    enabled: gapQuery !== null,
    queryFn: () => complianceApi.gaps(gapQuery ?? { sku_id: '', market: '', category_code: '' }),
  });
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Typography.Text strong>{copy.certTitle}</Typography.Text>
      {certificates.isError ? <Alert type="error" message={messageOf(certificates.error)} /> : null}
      <Table<CertificateView>
        rowKey="id"
        loading={certificates.isLoading}
        pagination={false}
        dataSource={certificates.data ?? []}
        locale={{ emptyText: copy.certEmpty }}
        columns={[
          { title: zhCN.productPage.skuCode, dataIndex: 'sku_code' },
          { title: copy.hsMarket, dataIndex: 'market', width: 80 },
          { title: copy.certType, dataIndex: 'cert_type' },
          { title: copy.certNo, dataIndex: 'cert_no' },
          { title: copy.certExpires, dataIndex: 'expires_at', width: 120 },
          {
            title: copy.certFile,
            width: 100,
            render: (_, row) => (row.object_key ? copy.certAttached : copy.certMissingFile),
          },
        ]}
      />
      <Typography.Text strong>{copy.gapTitle}</Typography.Text>
      <Form
        form={form}
        layout="inline"
        onFinish={(values) => setGapQuery({ ...values, category_code: values.category_code.trim() })}
      >
        <Form.Item name="sku_id" rules={[{ required: true }]}>
          <Select
            placeholder={zhCN.productPage.skuCode}
            style={{ width: 160 }}
            options={skus.map((sku) => ({ value: sku.id, label: sku.sku_code }))}
          />
        </Form.Item>
        <Form.Item name="market" rules={[{ required: true }]}>
          <Select
            placeholder={copy.hsMarket}
            style={{ width: 100 }}
            options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))}
          />
        </Form.Item>
        <Form.Item name="category_code" rules={[{ required: true }]}>
          <Input placeholder={copy.gapCategory} />
        </Form.Item>
        <Button htmlType="submit">{copy.gapCheck}</Button>
      </Form>
      {gaps.isError ? <Alert type="error" message={messageOf(gaps.error)} /> : null}
      {gapQuery && gaps.isSuccess && (gaps.data ?? []).length === 0 ? (
        <Typography.Text type="secondary">{copy.gapEmpty}</Typography.Text>
      ) : null}
      {(gaps.data ?? []).length > 0 ? (
        <Table
          rowKey="cert_type"
          pagination={false}
          dataSource={gaps.data ?? []}
          columns={[
            { title: copy.certType, dataIndex: 'cert_type' },
            {
              title: copy.gapReason,
              dataIndex: 'reason',
              render: (value: string) => (value === 'EXPIRED' ? copy.gapExpired : copy.gapMissing),
            },
          ]}
        />
      ) : null}
    </Space>
  );
}

export function ProductDetailPage() {
  const { spuId } = useParams();
  const navigate = useNavigate();
  const detail = useQuery({
    queryKey: ['product-detail', spuId],
    enabled: Boolean(spuId),
    queryFn: async () => {
      const id = spuId ?? '';
      const spu = await productsApi.detail(id);
      const images = await productsApi.images(id);
      const listingPages = await Promise.all(
        spu.skus.map((sku) => listingsApi.list({ sku_id: sku.id, limit: 50 })),
      );
      const listings = listingPages.flatMap((page) => page.items);
      const contentPages = await Promise.all(
        listings.map((row) => localeApi.listContents({ listing_id: row.id, limit: 20 })),
      );
      return { spu, images, listings, contents: contentPages.flatMap((page) => page.items) };
    },
  });

  if (!spuId) {
    return <Alert type="warning" message={copy.missing} />;
  }
  if (detail.isError) {
    return <Alert type="error" message={messageOf(detail.error)} />;
  }

  const spu = detail.data?.spu;
  const listings = detail.data?.listings ?? [];
  const images = detail.data?.images ?? [];
  const contents = detail.data?.contents ?? [];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card loading={detail.isLoading}>
        <Space>
          <Button onClick={() => navigate('/products')}>{copy.back}</Button>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {spu?.title ?? copy.title}
          </Typography.Title>
        </Space>
        <Tabs
          style={{ marginTop: 16 }}
          items={[
            {
              key: 'listing',
              label: copy.tabListing,
              children: (
                <Table<ListingView>
                  rowKey="id"
                  pagination={false}
                  dataSource={listings}
                  locale={{ emptyText: copy.listingEmpty }}
                  columns={[
                    { title: zhCN.productPage.skuCode, dataIndex: 'sku_code' },
                    { title: copy.shop, dataIndex: 'shop_name' },
                    { title: zhCN.inventoryPage.platform, dataIndex: 'platform_code' },
                    { title: copy.site, dataIndex: 'site_code' },
                    { title: zhCN.productPage.status, dataIndex: 'status' },
                    {
                      title: copy.price,
                      render: (_, row) => <MoneyText value={row.price} currency={row.currency ?? undefined} />,
                    },
                  ]}
                />
              ),
            },
            {
              key: 'cost',
              label: copy.tabCost,
              children: (
                <CostGuard>
                  <Typography.Paragraph type="secondary">{copy.costHint}</Typography.Paragraph>
                  <Table<SkuView>
                    rowKey="id"
                    pagination={false}
                    dataSource={spu?.skus ?? []}
                    locale={{ emptyText: copy.costEmpty }}
                    columns={[
                      { title: zhCN.productPage.skuCode, dataIndex: 'sku_code' },
                      {
                        title: zhCN.productPage.purchasePrice,
                        render: (_, row) =>
                          row.purchase_price ? (
                            <MoneyText value={row.purchase_price} currency={row.currency ?? 'CNY'} decimals={4} />
                          ) : (
                            '—'
                          ),
                      },
                    ]}
                  />
                </CostGuard>
              ),
            },
            {
              key: 'compliance',
              label: copy.tabCompliance,
              children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <PermissionGuard permission={Perm.COMPLIANCE_READ}>
                    <ProductHsBindings spuId={spuId} />
                    <ProductCertificates spuId={spuId} skus={spu?.skus ?? []} />
                  </PermissionGuard>
                  <Typography.Paragraph type="secondary">{copy.complianceHint}</Typography.Paragraph>
                  <Typography.Text strong>{copy.imageCheck}</Typography.Text>
                  <Table<ProductImageView>
                    rowKey="id"
                    pagination={false}
                    dataSource={images}
                    locale={{ emptyText: copy.noImages }}
                    columns={[
                      { title: zhCN.productPage.status, dataIndex: 'image_type' },
                      {
                        title: copy.imageCheck,
                        render: (_, row) =>
                          row.compliance.length === 0
                            ? '—'
                            : row.compliance.map((item) => (
                                <Tag key={item.platform_code} color={item.ok ? 'green' : 'orange'}>
                                  {item.platform_code} {item.ok ? copy.imageOk : item.issues.join('、') || copy.imageIssue}
                                </Tag>
                              )),
                      },
                    ]}
                  />
                  <Typography.Text strong>{copy.languageQuality}</Typography.Text>
                  <Table<ListingContentView>
                    rowKey="id"
                    pagination={false}
                    dataSource={contents}
                    locale={{ emptyText: copy.noCopy }}
                    columns={[
                      { title: quality.lang, dataIndex: 'lang' },
                      {
                        title: quality.status,
                        dataIndex: 'quality_status',
                        render: (value: ContentQuality) => <Tag>{QUALITY_LABEL[value] ?? value}</Tag>,
                      },
                      { title: quality.listing, dataIndex: 'title' },
                    ]}
                  />
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
