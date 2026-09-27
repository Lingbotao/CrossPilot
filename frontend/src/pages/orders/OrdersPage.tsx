import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';

import { ApiError } from '@/api/client';
import { downloadBase64, ordersApi } from '@/api/orders';
import {
  Perm,
  UnifiedStatus,
  type BatchShipResult,
  type LabelSize,
  type OrderListItem,
  type OrderListQuery,
  type UnifiedStatusValue,
} from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { formatDateTime } from '@/utils/format';
import { FreshnessBanner, OrderNav } from '@/pages/orders/OrderChrome';
import { exceptionLabel, reviewLabel } from '@/pages/orders/labels';

const copy = zhCN.orderPage;
const statusCopy = zhCN.order.unifiedStatus;

const EXPORT_FIELDS: { key: string; label: string }[] = [
  { key: 'platform_order_id', label: copy.orderNo },
  { key: 'platform_code', label: copy.platform },
  { key: 'shop_name', label: copy.shop },
  { key: 'site_code', label: copy.site },
  { key: 'unified_status', label: copy.status },
  { key: 'currency', label: copy.currency },
  { key: 'item_amount', label: copy.itemAmount },
  { key: 'shipping_amount', label: copy.shippingAmount },
  { key: 'tax_amount', label: copy.taxAmount },
  { key: 'discount_amount', label: copy.discountAmount },
  { key: 'total_amount', label: copy.amount },
  { key: 'paid_at', label: copy.paidAt },
  { key: 'buyer_name', label: copy.buyer },
  { key: 'fee_detail', label: copy.fees },
];

const DEFAULT_FIELDS = ['platform_order_id', 'unified_status', 'currency', 'total_amount', 'fee_detail'];

interface FilterForm {
  q?: string;
  platform_code?: string;
  unified_status?: string;
  shop_id?: string;
  site_code?: string;
  sku?: string;
  amount_min?: string;
  amount_max?: string;
}

function statusLabel(status: string) {
  return statusCopy[status as UnifiedStatusValue] ?? status;
}

function countdown(deadline: string | null) {
  if (!deadline) {
    return '—';
  }
  const remaining = new Date(deadline).getTime() - Date.now();
  if (remaining <= 0) {
    return copy.overdue;
  }
  const hours = Math.floor(remaining / 3_600_000);
  const minutes = Math.floor((remaining % 3_600_000) / 60_000);
  return `${hours}小时${minutes}分`;
}

export function OrdersPage({ mode = 'all' }: { mode?: 'all' | 'to_ship' | 'exception' }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FilterForm>();
  const queue = mode === 'to_ship' ? 'to_ship' : mode === 'exception' ? 'exception' : undefined;
  const [filters, setFilters] = useState<OrderListQuery>({ limit: 20, queue });
  const [activeIndex, setActiveIndex] = useState(0);
  const [addressOpen, setAddressOpen] = useState(false);
  const [addressCountry, setAddressCountry] = useState('SG');
  const [addressCity, setAddressCity] = useState('');
  const [addressLine, setAddressLine] = useState('');
  const [extra, setExtra] = useState<OrderListItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [shipOpen, setShipOpen] = useState(false);
  const [carrier, setCarrier] = useState('');
  const [shipResult, setShipResult] = useState<BatchShipResult | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [fields, setFields] = useState<string[]>(DEFAULT_FIELDS);
  const [notice, setNotice] = useState<string | null>(null);
  const [labelSize, setLabelSize] = useState<LabelSize>('A6');

  const list = useQuery({
    queryKey: ['orders', filters],
    queryFn: async () => {
      const page = await ordersApi.list(filters);
      setExtra([]);
      setCursor(page.page_info.cursor);
      setHasMore(page.page_info.has_more);
      return page;
    },
  });

  const detail = useQuery({
    queryKey: ['order', openId],
    queryFn: () => ordersApi.detail(openId ?? ''),
    enabled: openId !== null,
  });

  const rows = useMemo(() => [...(list.data?.items ?? []), ...extra], [extra, list.data]);

  const ship = useMutation({
    mutationFn: (orderIds: string[]) => ordersApi.batchShip(orderIds, carrier.trim()),
    onSuccess: async (result) => {
      setShipResult(result);
      setNotice(copy.shipDone);
      setShipOpen(false);
      await queryClient.invalidateQueries({ queryKey: ['orders'] });
      if (openId) {
        await queryClient.invalidateQueries({ queryKey: ['order', openId] });
      }
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError && error.code === 50003 && error.data && typeof error.data === 'object') {
        setShipResult(error.data as BatchShipResult);
        setNotice(copy.shipPartial);
        setShipOpen(false);
        void queryClient.invalidateQueries({ queryKey: ['orders'] });
        return;
      }
      setNotice(error instanceof ApiError ? error.message : copy.shipPartial);
    },
  });

  const applyFilters = (values: FilterForm) => {
    setSelected([]);
    setShipResult(null);
    setFilters({
      limit: 20,
      queue,
      exception_kind: filters.exception_kind,
      q: values.q || undefined,
      platform_code: values.platform_code,
      unified_status: values.unified_status,
      shop_id: values.shop_id || undefined,
      site_code: values.site_code || undefined,
      sku: values.sku || undefined,
      amount_min: values.amount_min || undefined,
      amount_max: values.amount_max || undefined,
    });
  };

  const loadMore = async () => {
    if (!cursor) {
      return;
    }
    const page = await ordersApi.list({ ...filters, cursor, limit: 20 });
    setExtra((current) => [...current, ...page.items]);
    setCursor(page.page_info.cursor);
    setHasMore(page.page_info.has_more);
  };

  const failedIds = shipResult?.results.filter((item) => !item.ok).map((item) => item.order_id) ?? [];

  useEffect(() => {
    if (mode !== 'to_ship') {
      return undefined;
    }
    const onKey = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
        return;
      }
      if (event.key === 'j' || event.key === 'J') {
        setActiveIndex((index) => Math.min(rows.length - 1, index + 1));
      }
      if (event.key === 'k' || event.key === 'K') {
        setActiveIndex((index) => Math.max(0, index - 1));
      }
      if (event.key === 'Enter' && rows[activeIndex]) {
        setSelected([rows[activeIndex].id]);
        setShipOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeIndex, mode, rows]);

  const title = mode === 'to_ship' ? zhCN.menu.ordersToShip : mode === 'exception' ? zhCN.menu.ordersExceptions : copy.title;

  return (
    <Card
      title={title}
      extra={<Typography.Text type="secondary">{mode === 'to_ship' ? copy.navHint : copy.description}</Typography.Text>}
    >
      <OrderNav />
      <FreshnessBanner />
      <Form form={form} layout="inline" onFinish={applyFilters} style={{ marginBottom: 16, rowGap: 8 }}>
        <Form.Item name="q">
          <Input allowClear placeholder={copy.searchPlaceholder} style={{ width: 280 }} />
        </Form.Item>
        <Form.Item name="platform_code">
          <Select
            allowClear
            placeholder={copy.platform}
            style={{ width: 140 }}
            options={['amazon', 'shopee', 'lazada', 'tiktok'].map((value) => ({ value, label: value }))}
          />
        </Form.Item>
        {mode === 'all' ? (
          <Form.Item name="unified_status">
            <Select
              allowClear
              placeholder={copy.status}
              style={{ width: 160 }}
              options={Object.values(UnifiedStatus).map((value) => ({ value, label: statusLabel(value) }))}
            />
          </Form.Item>
        ) : null}
        {mode === 'exception' ? (
          <Form.Item>
            <Select
              allowClear
              placeholder={copy.exceptionAll}
              style={{ width: 160 }}
              value={filters.exception_kind}
              onChange={(value) => {
                setSelected([]);
                setFilters((current) => ({ ...current, exception_kind: value }));
              }}
              options={[
                { value: 'SKU_UNMATCHED', label: copy.skuUnmatched },
                { value: 'ADDRESS_INVALID', label: copy.addressInvalid },
                { value: 'SHIP_DUE_SOON', label: copy.shipDueSoon },
                { value: 'SHIP_OVERDUE', label: copy.shipOverdue },
                { value: 'REVIEW_PENDING', label: copy.reviewPending },
              ]}
            />
          </Form.Item>
        ) : null}
        <Form.Item name="shop_id">
          <Input allowClear placeholder={copy.shopId} style={{ width: 160 }} />
        </Form.Item>
        <Form.Item name="site_code">
          <Input allowClear placeholder={copy.site} style={{ width: 100 }} />
        </Form.Item>
        <Form.Item name="sku">
          <Input allowClear placeholder={copy.sku} style={{ width: 140 }} />
        </Form.Item>
        <Form.Item name="amount_min">
          <Input allowClear placeholder={copy.amountMin} style={{ width: 120 }} />
        </Form.Item>
        <Form.Item name="amount_max">
          <Input allowClear placeholder={copy.amountMax} style={{ width: 120 }} />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button type="primary" htmlType="submit">
              {copy.apply}
            </Button>
            <Button
              onClick={() => {
                form.resetFields();
                setSelected([]);
                setShipResult(null);
                setFilters({ limit: 20, queue });
              }}
            >
              {copy.reset}
            </Button>
          </Space>
        </Form.Item>
      </Form>

      {notice ? (
        <Alert style={{ marginBottom: 12 }} type="info" showIcon message={notice} closable onClose={() => setNotice(null)} />
      ) : null}
      {shipResult && shipResult.failed > 0 ? (
        <Alert
          style={{ marginBottom: 12 }}
          type="warning"
          showIcon
          message={copy.shipPartial}
          description={shipResult.results
            .filter((item) => !item.ok)
            .map((item) => `${item.platform_order_id || item.order_id}：${item.message}`)
            .join('；')}
        />
      ) : null}

      <Space
        style={
          mode === 'to_ship'
            ? {
                position: 'fixed',
                bottom: 24,
                left: '50%',
                zIndex: 10,
                transform: 'translateX(-50%)',
                padding: 12,
                background: '#fff',
                border: '1px solid #f0f0f0',
                borderRadius: 8,
                boxShadow: '0 6px 16px rgb(0 0 0 / 8%)',
              }
            : { marginBottom: 12 }
        }
      >
        <Typography.Text>
          {copy.selected} {selected.length}
        </Typography.Text>
        <PermissionGuard permission={Perm.ORDER_SHIP}>
          <Button disabled={selected.length === 0} onClick={() => setShipOpen(true)}>
            {copy.ship}
          </Button>
          <Button
            disabled={failedIds.length === 0}
            onClick={() => {
              setSelected(failedIds);
              setShipOpen(true);
            }}
          >
            {copy.retryFailed}
          </Button>
          <Select
            value={labelSize}
            style={{ width: 140 }}
            onChange={setLabelSize}
            options={[
              { value: 'A6', label: copy.labelA6 },
              { value: '100x150', label: copy.labelThermal },
            ]}
          />
          <Button
            disabled={selected.length === 0}
            onClick={async () => {
              const file = await ordersApi.labels(selected, labelSize);
              downloadBase64(file.filename, file.content_type, file.content_base64);
              if (file.skipped.length > 0) {
                setNotice(
                  `${copy.labelSkipped}：${file.skipped
                    .map((item) => item.platform_order_id || item.order_id)
                    .join(', ')}`,
                );
              }
            }}
          >
            {copy.print}
          </Button>
        </PermissionGuard>
        {mode === 'to_ship' ? (
          <PermissionGuard permission={Perm.ORDER_WRITE}>
            <Button disabled={selected.length === 0} onClick={() => setAddressOpen(true)}>
              {copy.changeAddress}
            </Button>
          </PermissionGuard>
        ) : null}
        <Button onClick={() => setExportOpen(true)}>{copy.export}</Button>
        {hasMore ? <Button onClick={() => void loadMore()}>{copy.loadMore}</Button> : null}
      </Space>

      <Table<OrderListItem>
        rowKey="id"
        loading={list.isLoading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: copy.empty }}
        onRow={(row, index) => ({
          style: {
            background: row.exceptions.includes('SHIP_OVERDUE')
              ? '#fff1f0'
              : mode === 'to_ship' && index === activeIndex
                ? '#e6f4ff'
                : undefined,
          },
        })}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }}
        columns={[
          { title: copy.orderNo, dataIndex: 'platform_order_id' },
          { title: copy.platform, dataIndex: 'platform_code', width: 88 },
          {
            title: copy.shop,
            render: (_, row) => `${row.shop_name || row.shop_id} / ${row.site_code}`,
          },
          {
            title: copy.status,
            dataIndex: 'unified_status',
            width: 88,
            render: (value: string) => <span style={{ whiteSpace: 'nowrap' }}>{statusLabel(value)}</span>,
          },
          {
            title: copy.amount,
            width: 110,
            render: (_, row) => (
              <span style={{ whiteSpace: 'nowrap' }}>
                <MoneyText value={row.total_amount} currency={row.currency} />
              </span>
            ),
          },
          { title: copy.buyer, dataIndex: 'buyer_name', width: 100, render: (value: string | null) => value || '—' },
          ...(mode === 'to_ship'
            ? [
                {
                  title: copy.deadline,
                  width: 110,
                  render: (_: unknown, row: OrderListItem) => (
                    <span style={{ whiteSpace: 'nowrap', color: row.exceptions.includes('SHIP_OVERDUE') ? '#cf1322' : undefined }}>
                      {countdown(row.ship_deadline)}
                    </span>
                  ),
                },
              ]
            : []),
          ...(mode !== 'all'
            ? [
                {
                  title: copy.exception,
                  render: (_: unknown, row: OrderListItem) => row.exceptions.map(exceptionLabel).join('、') || '—',
                },
              ]
            : []),
          ...(mode === 'exception'
            ? [
                {
                  title: copy.reviewPending,
                  width: 160,
                  render: (_: unknown, row: OrderListItem) =>
                    row.review_status === 'PENDING' ? (
                      <PermissionGuard permission={Perm.ORDER_WRITE}>
                        <Button
                          type="link"
                          onClick={() =>
                            void ordersApi.decideReview(row.id, 'approve').then(() => queryClient.invalidateQueries({ queryKey: ['orders'] }))
                          }
                        >
                          {copy.approveReview}
                        </Button>
                      </PermissionGuard>
                    ) : (
                      reviewLabel(row.review_status)
                    ),
                },
              ]
            : []),
          {
            title: copy.paidAt,
            dataIndex: 'paid_at',
            width: 168,
            render: (value: string | null) => <span style={{ whiteSpace: 'nowrap' }}>{formatDateTime(value)}</span>,
          },
          {
            title: copy.shipment,
            width: 80,
            render: (_, row) =>
              row.shipment_status === 'SUCCEEDED' ? (
                <Tag color="success">{copy.shipmentOk}</Tag>
              ) : row.shipment_status === 'FAILED' ? (
                <Tag color="error">{copy.shipmentFailed}</Tag>
              ) : (
                '—'
              ),
          },
          {
            title: copy.detail,
            width: 72,
            render: (_, row) => (
              <Button type="link" onClick={() => setOpenId(row.id)}>
                {copy.detail}
              </Button>
            ),
          },
        ]}
      />

      <Drawer title={copy.detail} width={640} open={openId !== null} onClose={() => setOpenId(null)}>
        {detail.data ? (
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label={copy.orderNo}>{detail.data.platform_order_id}</Descriptions.Item>
              <Descriptions.Item label={copy.status}>{statusLabel(detail.data.unified_status)}</Descriptions.Item>
              <Descriptions.Item label={copy.amount}>
                <MoneyText value={detail.data.total_amount} currency={detail.data.currency} />
              </Descriptions.Item>
              <Descriptions.Item label={copy.buyer}>{detail.data.buyer.name || '—'}</Descriptions.Item>
              <Descriptions.Item label={copy.address}>
                {[
                  detail.data.ship_to.name,
                  detail.data.ship_to.phone,
                  detail.data.ship_to.line1,
                  detail.data.ship_to.city,
                  detail.data.ship_to.state,
                  detail.data.ship_to.postal_code,
                  detail.data.ship_to.country,
                ]
                  .filter(Boolean)
                  .join(' ') || '—'}
              </Descriptions.Item>
              <Descriptions.Item label={copy.shipment}>
                {detail.data.shipment.tracking_no
                  ? `${detail.data.shipment.carrier || ''} ${detail.data.shipment.tracking_no}`
                  : detail.data.shipment.failure_reason || copy.noShipment}
              </Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Title level={5}>{copy.items}</Typography.Title>
              {detail.data.items.map((item) => (
                <div key={item.id}>
                  {item.item_name} · {item.platform_sku_id} · {item.quantity} ×{' '}
                  <MoneyText value={item.unit_price} currency={item.currency} />
                </div>
              ))}
            </div>
            <div>
              <Typography.Title level={5}>{copy.fees}</Typography.Title>
              {detail.data.fees.length === 0
                ? copy.noFees
                : detail.data.fees.map((fee) => (
                    <div key={fee.id}>
                      {fee.fee_type} · <MoneyText value={fee.amount} currency={fee.currency} /> · {fee.source}
                    </div>
                  ))}
              <div>
                {copy.itemAmount} <MoneyText value={detail.data.item_amount} currency={detail.data.currency} />
                {' · '}
                {copy.shippingAmount} <MoneyText value={detail.data.shipping_amount} currency={detail.data.currency} />
                {' · '}
                {copy.taxAmount} <MoneyText value={detail.data.tax_amount} currency={detail.data.currency} />
                {' · '}
                {copy.discountAmount} <MoneyText value={detail.data.discount_amount} currency={detail.data.currency} />
              </div>
            </div>
            <div>
              <Typography.Title level={5}>{copy.timeline}</Typography.Title>
              <Timeline
                items={detail.data.timeline.map((entry) => ({
                  children: `${formatDateTime(entry.created_at)} ${entry.from_status ?? '—'} → ${statusLabel(entry.to_status)} · ${entry.source}${entry.remark ? ` · ${entry.remark}` : ''}`,
                }))}
              />
            </div>
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title={copy.ship}
        open={shipOpen}
        okText={copy.confirmShip}
        confirmLoading={ship.isPending}
        onCancel={() => setShipOpen(false)}
        onOk={() => {
          if (!carrier.trim() || selected.length === 0) {
            return;
          }
          ship.mutate(selected);
        }}
      >
        <Input
          value={carrier}
          placeholder={copy.carrierPlaceholder}
          onChange={(event) => setCarrier(event.target.value)}
        />
      </Modal>

      <Modal
        title={copy.changeAddress}
        open={addressOpen}
        onCancel={() => setAddressOpen(false)}
        onOk={async () => {
          const failures: string[] = [];
          for (const orderId of selected) {
            try {
              await ordersApi.changeAddress(orderId, {
                country: addressCountry.trim().toUpperCase(),
                city: addressCity.trim(),
                line1: addressLine.trim(),
              });
            } catch (error: unknown) {
              failures.push(error instanceof ApiError ? error.message : orderId);
            }
          }
          setAddressOpen(false);
          setNotice(failures.length > 0 ? failures.join('；') : copy.addressSaved);
          await queryClient.invalidateQueries({ queryKey: ['orders'] });
        }}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input value={addressCountry} addonBefore={copy.addressCountry} onChange={(event) => setAddressCountry(event.target.value)} />
          <Input value={addressCity} addonBefore={copy.addressCity} onChange={(event) => setAddressCity(event.target.value)} />
          <Input value={addressLine} addonBefore={copy.addressLine} onChange={(event) => setAddressLine(event.target.value)} />
        </Space>
      </Modal>

      <Modal
        title={copy.export}
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        onOk={async () => {
          const file = await ordersApi.exportFile({ ...filters, fields: fields.join(',') });
          downloadBase64(file.filename, file.content_type, file.content_base64);
          setNotice(file.truncated ? copy.exportTruncated : null);
          setExportOpen(false);
        }}
      >
        <Checkbox.Group
          value={fields}
          options={EXPORT_FIELDS.map((item) => ({ label: item.label, value: item.key }))}
          onChange={(values) => setFields(values.map(String))}
        />
      </Modal>
    </Card>
  );
}

export function ToShipPage() {
  return <OrdersPage mode="to_ship" />;
}

export function ExceptionsPage() {
  return <OrdersPage mode="exception" />;
}
