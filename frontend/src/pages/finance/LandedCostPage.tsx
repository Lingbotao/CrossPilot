import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, DatePicker, Form, Input, Select, Space, Table, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { landedCostApi } from '@/api/landedCost';
import {
  CONTENT_MARKETS,
  FEE_CHARGES,
  FEE_CHANNELS,
  FEE_CODES,
  FIRST_MILE_METHODS,
  LANDED_CHANNELS,
  Perm,
  type LandedCostCalcRequest,
  type LandedCostCalcView,
  type LandedCostCompareView,
  type LandedCostFeeView,
  type LandedCostLine,
} from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.landedCostPage;

export interface LandedCostPreset {
  skuId?: string;
  purchaseAmount?: string | null;
  purchaseCurrency?: string | null;
  weightG?: string;
  lengthCm?: string;
  widthCm?: string;
  heightCm?: string;
}

interface CalcForm {
  market: string;
  selling_currency: string;
  channel: string;
  first_mile_method: string;
  selling_price?: string;
  purchase_amount?: string;
  purchase_currency?: string;
  fx_rate?: string;
  fx_source?: string;
  weight_g?: string;
  volume_cm3?: string;
  length_cm?: string;
  width_cm?: string;
  height_cm?: string;
  hs_code?: string;
  declared_value?: string;
  declared_currency?: string;
  shipment_cost?: string;
  shipment_currency?: string;
  shipment_weight_g?: string;
  shipment_volume_cm3?: string;
  shipment_value?: string;
  storage_days?: string;
  compare?: boolean;
  channel_b?: string;
  method_b?: string;
  selling_price_b?: string;
  shipment_cost_b?: string;
  shipment_currency_b?: string;
  shipment_weight_g_b?: string;
  shipment_volume_cm3_b?: string;
  shipment_value_b?: string;
}

interface FeeForm {
  market: string;
  channel: string;
  fee_code: string;
  label?: string;
  charge: string;
  amount: string;
  currency?: string;
  volumetric_divisor?: string;
  effective_from: Dayjs;
  effective_to?: Dayjs;
  source: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

function textOrNull(value: string | undefined): string | null {
  const text = value?.trim() ?? '';
  return text === '' ? null : text;
}

function countOrNull(value: string | undefined): number | null {
  const text = value?.trim() ?? '';
  if (text === '' || !/^\d+$/.test(text)) {
    return null;
  }
  return Number(text);
}

function toRequest(values: CalcForm, side: 'A' | 'B', skuId?: string): LandedCostCalcRequest {
  const scenarioB = side === 'B';
  return {
    name: scenarioB ? copy.scenarioB : copy.scenarioA,
    market: values.market,
    selling_currency: values.selling_currency.trim().toUpperCase(),
    channel: scenarioB ? values.channel_b || values.channel : values.channel,
    first_mile_method: scenarioB ? values.method_b || values.first_mile_method : values.first_mile_method,
    sku_id: skuId ?? null,
    selling_price: textOrNull(scenarioB ? values.selling_price_b || values.selling_price : values.selling_price),
    purchase_amount: textOrNull(values.purchase_amount),
    purchase_currency: textOrNull(values.purchase_currency)?.toUpperCase() ?? null,
    fx_rate: textOrNull(values.fx_rate),
    fx_source: values.fx_source?.trim() ?? '',
    weight_g: textOrNull(values.weight_g),
    volume_cm3: textOrNull(values.volume_cm3),
    length_cm: textOrNull(values.length_cm),
    width_cm: textOrNull(values.width_cm),
    height_cm: textOrNull(values.height_cm),
    hs_code: values.hs_code?.trim() ?? '',
    declared_value: textOrNull(values.declared_value),
    declared_currency: textOrNull(values.declared_currency)?.toUpperCase() ?? null,
    shipment_cost: textOrNull(scenarioB ? values.shipment_cost_b : values.shipment_cost),
    shipment_currency: textOrNull(scenarioB ? values.shipment_currency_b : values.shipment_currency)?.toUpperCase() ?? null,
    shipment_weight_g: textOrNull(scenarioB ? values.shipment_weight_g_b : values.shipment_weight_g),
    shipment_volume_cm3: textOrNull(scenarioB ? values.shipment_volume_cm3_b : values.shipment_volume_cm3),
    shipment_value: textOrNull(scenarioB ? values.shipment_value_b : values.shipment_value),
    storage_days: countOrNull(values.storage_days),
  };
}

function csvCell(value: string): string {
  return `"${value.replaceAll('"', '""')}"`;
}

function downloadCsv(views: LandedCostCalcView[]): void {
  const header = [copy.item, copy.amount, copy.currency, copy.state, copy.formula, copy.source];
  const lines = views.flatMap((view) =>
    view.lines.map((line) =>
      [view.name, line.label, line.amount ?? '', line.currency, line.complete ? copy.ready : copy.missing, line.formula, line.source]
        .map(csvCell)
        .join(','),
    ),
  );
  const blob = new Blob([`\uFEFF${[header.map(csvCell).join(','), ...lines].join('\n')}`], {
    type: 'text/csv;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'landed-cost.csv';
  link.click();
  URL.revokeObjectURL(url);
}

function ResultCard({ view }: { view: LandedCostCalcView }) {
  return (
    <Card size="small" title={view.name || view.market}>
      <Space size={24} wrap>
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">{copy.landed}</Typography.Text>
          <MoneyText value={view.landed_cost} currency={view.currency} />
        </Space>
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">{copy.net}</Typography.Text>
          <MoneyText value={view.net_profit} currency={view.currency} />
        </Space>
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">{copy.margin}</Typography.Text>
          <Typography.Text>{view.net_margin_percent ? `${view.net_margin_percent}%` : '—'}</Typography.Text>
        </Space>
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">{copy.roi}</Typography.Text>
          <Typography.Text>{view.roi_percent ? `${view.roi_percent}%` : '—'}</Typography.Text>
        </Space>
      </Space>
      <Table<LandedCostLine>
        style={{ marginTop: 16 }}
        rowKey="code"
        pagination={false}
        dataSource={view.lines}
        columns={[
          { title: copy.item, dataIndex: 'label' },
          {
            title: copy.amount,
            render: (_, row) => <MoneyText value={row.amount} currency={row.currency} />,
          },
          { title: copy.state, render: (_, row) => (row.complete ? copy.ready : copy.missing) },
          { title: copy.formula, dataIndex: 'formula' },
          { title: copy.source, dataIndex: 'source' },
        ]}
      />
    </Card>
  );
}

export function LandedCostCalculator({ preset }: { preset?: LandedCostPreset }) {
  const [form] = Form.useForm<CalcForm>();
  const compare = Form.useWatch('compare', form);
  const method = Form.useWatch('first_mile_method', form);
  const [outcome, setOutcome] = useState<LandedCostCalcView | LandedCostCompareView | null>(null);
  const views = outcome && 'left' in outcome ? [outcome.left, outcome.right] : outcome ? [outcome] : [];

  const run = useMutation<LandedCostCalcView | LandedCostCompareView, Error, CalcForm>({
    mutationFn: (values) => {
      if (values.storage_days?.trim() && countOrNull(values.storage_days) === null) {
        return Promise.reject(new Error(copy.daysInvalid));
      }
      const left = toRequest(values, 'A', preset?.skuId);
      if (values.compare) {
        return landedCostApi.compare({ left, right: toRequest(values, 'B', preset?.skuId) });
      }
      return landedCostApi.calculate(left);
    },
    onSuccess: (data) => {
      setOutcome(data);
      feedback().message.success(copy.calculated);
      const incomplete = 'left' in data ? !data.left.complete || !data.right.complete : !data.complete;
      if (incomplete) {
        feedback().message.warning(copy.incomplete);
      }
    },
  });

  const allocate = method === 'WEIGHT' || method === 'VOLUME' || method === 'VALUE';

  return (
    <Card>
      <Form<CalcForm>
        form={form}
        layout="vertical"
        initialValues={{
          selling_currency: 'USD',
          channel: 'AIR',
          first_mile_method: 'CHARGEABLE',
          purchase_amount: preset?.purchaseAmount ?? undefined,
          purchase_currency: preset?.purchaseCurrency ?? undefined,
          weight_g: preset?.weightG,
          length_cm: preset?.lengthCm,
          width_cm: preset?.widthCm,
          height_cm: preset?.heightCm,
        }}
        onFinish={(values) => run.mutate(values)}
      >
        <Space wrap align="start">
          <Form.Item name="market" label={copy.market} rules={[{ required: true }]}>
            <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((item) => ({ value: item, label: item }))} />
          </Form.Item>
          <Form.Item name="channel" label={copy.channel} rules={[{ required: true }]}>
            <Select style={{ width: 160 }} options={LANDED_CHANNELS.map((item) => ({ value: item, label: copy.channels[item] }))} />
          </Form.Item>
          <Form.Item name="first_mile_method" label={copy.method} rules={[{ required: true }]}>
            <Select
              style={{ width: 180 }}
              options={FIRST_MILE_METHODS.map((item) => ({ value: item, label: copy.methods[item] }))}
            />
          </Form.Item>
          <Form.Item name="selling_currency" label={copy.sellingCurrency} rules={[{ required: true }]}>
            <Input style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="selling_price" label={copy.sellingPrice}>
            <Input style={{ width: 140 }} />
          </Form.Item>
          <Form.Item name="purchase_amount" label={copy.purchase}>
            <Input style={{ width: 140 }} />
          </Form.Item>
          <Form.Item name="purchase_currency" label={copy.purchaseCurrency}>
            <Input style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="fx_rate" label={copy.fxRate}>
            <Input style={{ width: 220 }} />
          </Form.Item>
          <Form.Item name="fx_source" label={copy.fxSource}>
            <Input style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="weight_g" label={copy.weight}>
            <Input style={{ width: 150 }} />
          </Form.Item>
          <Form.Item name="volume_cm3" label={copy.volume} extra={copy.dimensionHint}>
            <Input style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="length_cm" label={copy.length}>
            <Input style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="width_cm" label={copy.width}>
            <Input style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="height_cm" label={copy.height}>
            <Input style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="hs_code" label={copy.hs}>
            <Input style={{ width: 140 }} />
          </Form.Item>
          <Form.Item name="declared_value" label={copy.declared}>
            <Input style={{ width: 140 }} />
          </Form.Item>
          <Form.Item name="declared_currency" label={copy.declaredCurrency}>
            <Input style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="storage_days" label={copy.storageDays}>
            <Input style={{ width: 120 }} />
          </Form.Item>
        </Space>
        {allocate ? (
          <Space wrap align="start">
            <Form.Item name="shipment_cost" label={copy.shipmentCost}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_currency" label={copy.shipmentCurrency}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_weight_g" label={copy.shipmentWeight}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_volume_cm3" label={copy.shipmentVolume}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_value" label={copy.shipmentValue}>
              <Input />
            </Form.Item>
          </Space>
        ) : null}
        <Form.Item name="compare" valuePropName="checked">
          <Checkbox>{copy.compare}</Checkbox>
        </Form.Item>
        {compare ? (
          <Space wrap align="start">
            <Typography.Text strong>{copy.scenarioB}</Typography.Text>
            <Form.Item name="channel_b" label={copy.channel}>
              <Select style={{ width: 160 }} options={LANDED_CHANNELS.map((item) => ({ value: item, label: copy.channels[item] }))} />
            </Form.Item>
            <Form.Item name="method_b" label={copy.method}>
              <Select
                style={{ width: 180 }}
                options={FIRST_MILE_METHODS.map((item) => ({ value: item, label: copy.methods[item] }))}
              />
            </Form.Item>
            <Form.Item name="selling_price_b" label={copy.sellingPrice}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_cost_b" label={copy.shipmentCost}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_currency_b" label={copy.shipmentCurrency}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_weight_g_b" label={copy.shipmentWeight}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_volume_cm3_b" label={copy.shipmentVolume}>
              <Input />
            </Form.Item>
            <Form.Item name="shipment_value_b" label={copy.shipmentValue}>
              <Input />
            </Form.Item>
          </Space>
        ) : null}
        <PermissionGuard permission={Perm.LANDED_COST_CALC}>
          <Button type="primary" htmlType="submit" loading={run.isPending}>
            {copy.calculate}
          </Button>
        </PermissionGuard>
      </Form>
      {run.isError ? <Alert style={{ marginTop: 16 }} type="error" message={messageOf(run.error)} /> : null}
      {views.length > 0 ? (
        <Space direction="vertical" size={16} style={{ width: '100%', marginTop: 16 }}>
          <Button onClick={() => downloadCsv(views)}>{copy.export}</Button>
          {views.map((view) => (
            <ResultCard key={`${view.id}-${view.name}`} view={view} />
          ))}
        </Space>
      ) : (
        <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
          {copy.empty}
        </Typography.Paragraph>
      )}
    </Card>
  );
}

function FeePanel() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FeeForm>();
  const charge = Form.useWatch('charge', form);
  const feeCode = Form.useWatch('fee_code', form);
  const fees = useQuery({ queryKey: ['landed-cost-fees'], queryFn: () => landedCostApi.fees() });
  const save = useMutation({
    mutationFn: (values: FeeForm) => {
      const divisor = countOrNull(values.volumetric_divisor);
      if (values.volumetric_divisor?.trim() && divisor === null) {
        return Promise.reject(new Error(copy.divisor));
      }
      return landedCostApi.createFee({
        market: values.market,
        channel: values.channel,
        fee_code: values.fee_code,
        label: values.label?.trim() || '*',
        charge: values.charge,
        amount: values.amount.trim(),
        currency: charge === 'RATE' ? null : textOrNull(values.currency),
        volumetric_divisor: feeCode === 'FIRST_MILE' && charge === 'PER_KG' ? divisor : null,
        effective_from: values.effective_from.format('YYYY-MM-DD'),
        effective_to: values.effective_to ? values.effective_to.format('YYYY-MM-DD') : null,
        source: values.source.trim(),
      });
    },
    onSuccess: async () => {
      feedback().message.success(copy.saved);
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['landed-cost-fees'] });
    },
  });
  const retire = useMutation({
    mutationFn: (feeId: string) => landedCostApi.retireFee(feeId),
    onSuccess: async () => {
      feedback().message.success(copy.retired);
      await queryClient.invalidateQueries({ queryKey: ['landed-cost-fees'] });
    },
  });

  return (
    <Card title={copy.fees}>
      {fees.isError ? <Alert type="error" message={messageOf(fees.error)} /> : null}
      <Table<LandedCostFeeView>
        rowKey="id"
        loading={fees.isLoading}
        pagination={false}
        dataSource={fees.data ?? []}
        columns={[
          { title: copy.market, dataIndex: 'market' },
          { title: copy.channel, dataIndex: 'channel', render: (value: string) => copy.channels[value as keyof typeof copy.channels] ?? value },
          { title: copy.feeCode, dataIndex: 'fee_code', render: (value: string) => copy.codes[value as keyof typeof copy.codes] ?? value },
          { title: copy.label, dataIndex: 'label' },
          { title: copy.charge, dataIndex: 'charge', render: (value: string) => copy.charges[value as keyof typeof copy.charges] ?? value },
          {
            title: copy.amount,
            render: (_, row) =>
              row.amount_percent ? `${row.amount_percent}%` : <MoneyText value={row.amount} currency={row.currency ?? undefined} />,
          },
          { title: copy.source, dataIndex: 'source' },
          { title: copy.version, dataIndex: 'version' },
          {
            title: zhCN.common.actions,
            render: (_, row) =>
              row.status === 'ACTIVE' ? (
                <PermissionGuard permission={Perm.LANDED_COST_CALC}>
                  <Button type="link" onClick={() => retire.mutate(row.id)}>
                    {copy.retire}
                  </Button>
                </PermissionGuard>
              ) : (
                copy.retired
              ),
          },
        ]}
      />
      <PermissionGuard permission={Perm.LANDED_COST_CALC}>
        <Form<FeeForm> form={form} layout="vertical" style={{ marginTop: 16 }} onFinish={(values) => save.mutate(values)}>
          <Space wrap align="start">
            <Form.Item name="market" label={copy.market} rules={[{ required: true }]}>
              <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((item) => ({ value: item, label: item }))} />
            </Form.Item>
            <Form.Item name="channel" label={copy.channel} rules={[{ required: true }]}>
              <Select style={{ width: 160 }} options={FEE_CHANNELS.map((item) => ({ value: item, label: copy.channels[item] }))} />
            </Form.Item>
            <Form.Item name="fee_code" label={copy.feeCode} rules={[{ required: true }]}>
              <Select style={{ width: 160 }} options={FEE_CODES.map((item) => ({ value: item, label: copy.codes[item] }))} />
            </Form.Item>
            <Form.Item name="label" label={copy.label}>
              <Input style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="charge" label={copy.charge} rules={[{ required: true }]}>
              <Select style={{ width: 160 }} options={FEE_CHARGES.map((item) => ({ value: item, label: copy.charges[item] }))} />
            </Form.Item>
            <Form.Item name="amount" label={charge === 'RATE' ? copy.rate : copy.money} rules={[{ required: true }]}>
              <Input style={{ width: 140 }} />
            </Form.Item>
            {charge !== 'RATE' ? (
              <Form.Item name="currency" label={copy.currency} rules={[{ required: true }]}>
                <Input style={{ width: 100 }} />
              </Form.Item>
            ) : null}
            {feeCode === 'FIRST_MILE' && charge === 'PER_KG' ? (
              <Form.Item name="volumetric_divisor" label={copy.divisor}>
                <Input style={{ width: 220 }} />
              </Form.Item>
            ) : null}
            <Form.Item name="effective_from" label={copy.effectiveFrom} rules={[{ required: true }]}>
              <DatePicker />
            </Form.Item>
            <Form.Item name="effective_to" label={copy.effectiveTo}>
              <DatePicker />
            </Form.Item>
            <Form.Item name="source" label={copy.source} rules={[{ required: true }]}>
              <Input style={{ width: 240 }} />
            </Form.Item>
          </Space>
          {save.isError ? <Alert type="error" message={messageOf(save.error)} /> : null}
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            {copy.saveFee}
          </Button>
        </Form>
      </PermissionGuard>
    </Card>
  );
}

export function LandedCostPage() {
  return (
    <CostGuard>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Typography.Title level={3}>{copy.title}</Typography.Title>
        <Alert type="info" message={copy.disclaimer} />
        <LandedCostCalculator />
        <FeePanel />
      </Space>
    </CostGuard>
  );
}
