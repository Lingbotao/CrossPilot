import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Col, DatePicker, Form, Input, Row, Select, Space, Switch, Table, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import * as echarts from 'echarts';
import { useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/client';
import { landedCostApi } from '@/api/landedCost';
import {
  CONTENT_MARKETS,
  FEE_CHARGES,
  FEE_CHANNELS,
  FEE_CODES,
  FIRST_MILE_METHODS,
  LANDED_CHANNELS,
  LINE_CODES,
  Perm,
  type LandedCostCalcRequest,
  type LandedCostCalcView,
  type LandedCostCompareView,
  type LandedCostFeeView,
  type LandedCostLine,
  type LandedCostPricingView,
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
  target_margin_percent?: string;
  period_fixed_cost?: string;
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

function CostCharts({ view }: { view: LandedCostCalcView }) {
  const waterfallNode = useRef<HTMLDivElement>(null);
  const donutNode = useRef<HTMLDivElement>(null);
  const signature = view.lines.map((line) => `${line.code}:${line.amount ?? ''}`).join('|');
  useEffect(() => {
    if (!waterfallNode.current || !donutNode.current) {
      return undefined;
    }
    const drawn = view.lines.filter((line) => line.amount);
    const waterfall = echarts.init(waterfallNode.current);
    const donut = echarts.init(donutNode.current);
    // 坐标轴只接收接口返回的十进制字符串，合计仍由 MoneyText 展示。
    waterfall.setOption({
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: view.lines.map((line) => line.label), axisLabel: { interval: 0, rotate: 40 } },
      yAxis: { type: 'value' },
      series: [{ type: 'bar', data: view.lines.map((line) => line.amount) }],
    });
    donut.setOption({
      tooltip: { trigger: 'item' },
      series: [
        {
          type: 'pie',
          radius: ['42%', '68%'],
          data: drawn.map((line) => ({ name: line.label, value: line.amount })),
        },
      ],
    });
    const onResize = () => {
      waterfall.resize();
      donut.resize();
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      waterfall.dispose();
      donut.dispose();
    };
  }, [signature, view]);
  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <div ref={waterfallNode} style={{ height: 280, width: '100%' }} />
      <div ref={donutNode} style={{ height: 240, width: '100%' }} />
    </Space>
  );
}

export function LandedCostCalculator({ preset }: { preset?: LandedCostPreset }) {
  const [form] = Form.useForm<CalcForm>();
  const compare = Form.useWatch('compare', form);
  const method = Form.useWatch('first_mile_method', form);
  const [outcome, setOutcome] = useState<LandedCostCalcView | LandedCostCompareView | null>(null);
  const [pricing, setPricing] = useState<LandedCostPricingView | null>(null);
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
    },
  });

  const scenarios = useMutation({
    mutationFn: (values: CalcForm) =>
      Promise.all(
        (
          [
            { name: copy.scenarioPacket, channel: 'PACKET', method: 'CHARGEABLE', storage: false },
            { name: copy.scenarioSea, channel: 'SEA_LCL', method: 'VOLUME', storage: true },
            { name: copy.scenarioPlatform, channel: 'EXPRESS', method: 'CHARGEABLE', storage: false },
          ] as const
        ).map((item) =>
          landedCostApi.calculate({
            ...toRequest(values, 'A', preset?.skuId),
            name: item.name,
            channel: item.channel,
            first_mile_method: item.method,
            storage_days: item.storage ? countOrNull(values.storage_days) : null,
          }),
        ),
      ),
  });

  const watched = Form.useWatch([], form) as CalcForm | undefined;
  const fieldSignature = JSON.stringify(watched ?? {});
  const runRef = useRef(run.mutate);
  const scenarioRef = useRef(scenarios.mutate);
  runRef.current = run.mutate;
  scenarioRef.current = scenarios.mutate;
  useEffect(() => {
    const values = form.getFieldsValue(true) as CalcForm;
    if (!values.market || !values.selling_currency?.trim() || !values.channel || !values.first_mile_method) {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      runRef.current(values);
      scenarioRef.current(values);
    }, 200);
    return () => window.clearTimeout(timer);
  }, [fieldSignature, form]);

  const price = useMutation({
    mutationFn: (values: CalcForm) => {
      const target = values.target_margin_percent?.trim() ?? '';
      if (!target) {
        return Promise.reject(new Error(copy.targetMargin));
      }
      return landedCostApi.price({
        ...toRequest(values, 'A', preset?.skuId),
        target_margin_percent: target,
        period_fixed_cost: textOrNull(values.period_fixed_cost),
      });
    },
    onSuccess: (data) => {
      setPricing(data);
      feedback().message.success(copy.priced);
    },
  });

  const allocate = method === 'WEIGHT' || method === 'VOLUME' || method === 'VALUE';

  const primary = views[0] ?? null;

  return (
    <Card>
      <Row gutter={24}>
        <Col xs={24} xl={14}>
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
        onFinish={(values) => {
          run.mutate(values, {
            onSuccess: (data) => {
              feedback().message.success(copy.calculated);
              const incomplete = 'left' in data ? !data.left.complete || !data.right.complete : !data.complete;
              if (incomplete) {
                feedback().message.warning(copy.incomplete);
              }
            },
          });
        }}
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
          <Form.Item name="target_margin_percent" label={copy.targetMargin}>
            <Input style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="period_fixed_cost" label={copy.periodFixed}>
            <Input style={{ width: 160 }} />
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
          <Space>
            <Button type="primary" htmlType="submit" loading={run.isPending}>
              {copy.calculate}
            </Button>
            <Button loading={price.isPending} onClick={() => price.mutate(form.getFieldsValue(true) as CalcForm)}>
              {copy.suggest}
            </Button>
          </Space>
        </PermissionGuard>
      </Form>
      {price.isError ? <Alert style={{ marginTop: 16 }} type="error" message={messageOf(price.error)} /> : null}
      {pricing ? (
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 16 }}>
          <Typography.Paragraph>{pricing.formula}</Typography.Paragraph>
          <Typography.Paragraph>{pricing.quantity_formula}</Typography.Paragraph>
          <Space size={24} wrap>
            <span>
              {copy.suggested}{' '}
              <MoneyText value={pricing.suggested_price} currency={pricing.currency} />
            </span>
            <span>
              {copy.breakEvenPrice}{' '}
              <MoneyText value={pricing.break_even_price} currency={pricing.currency} />
            </span>
            <span>
              {copy.breakEvenQty} {pricing.break_even_quantity ?? '—'}
            </span>
          </Space>
          <Table
            rowKey="target_margin_percent"
            pagination={false}
            dataSource={pricing.curve}
            columns={[
              { title: copy.margin, dataIndex: 'target_margin_percent', render: (value: string) => `${value}%` },
              {
                title: copy.suggested,
                render: (_, row) => <MoneyText value={row.selling_price} currency={pricing.currency} />,
              },
              {
                title: copy.margin,
                dataIndex: 'net_margin_percent',
                render: (value: string | null) => (value ? `${value}%` : '—'),
              },
              {
                title: copy.state,
                dataIndex: 'reachable',
                render: (value: boolean) => (value ? copy.reachable : copy.unreachable),
              },
            ]}
          />
        </Space>
      ) : null}
        </Col>
        <Col xs={24} xl={10}>
          {primary ? <CostCharts view={primary} /> : null}
          {run.isError ? <Alert style={{ marginTop: 16 }} type="error" message={messageOf(run.error)} /> : null}
          {scenarios.isError ? <Alert style={{ marginTop: 16 }} type="error" message={messageOf(scenarios.error)} /> : null}
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
        </Col>
      </Row>
      <Typography.Title level={5} style={{ marginTop: 24 }}>
        {copy.scenarios}
      </Typography.Title>
      <Table<LandedCostCalcView>
        rowKey={(row) => `${row.name}-${row.id}`}
        pagination={false}
        loading={scenarios.isPending}
        dataSource={scenarios.data ?? []}
        locale={{ emptyText: copy.scenarioEmpty }}
        columns={[
          { title: copy.scenario, dataIndex: 'name' },
          {
            title: copy.landed,
            render: (_, row) => <MoneyText value={row.landed_cost} currency={row.currency} />,
          },
          {
            title: copy.net,
            render: (_, row) => <MoneyText value={row.net_profit} currency={row.currency} />,
          },
          {
            title: copy.margin,
            dataIndex: 'net_margin_percent',
            render: (value: string | null) => (value ? `${value}%` : '—'),
          },
          {
            title: copy.state,
            render: (_, row) => (row.complete && row.profit_complete ? copy.ready : copy.missing),
          },
        ]}
      />
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

function TogglePanel() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<{ market: string; channel: string; line_code: string; enabled: boolean }>();
  const toggles = useQuery({ queryKey: ['landed-cost-toggles'], queryFn: () => landedCostApi.toggles() });
  const save = useMutation({
    mutationFn: (values: { market: string; channel: string; line_code: string; enabled: boolean }) =>
      landedCostApi.setToggle({ ...values, enabled: values.enabled !== false }),
    onSuccess: async () => {
      feedback().message.success(copy.toggleSaved);
      await queryClient.invalidateQueries({ queryKey: ['landed-cost-toggles'] });
    },
  });
  return (
    <Card title={copy.toggles}>
      {toggles.isError ? <Alert type="error" message={messageOf(toggles.error)} /> : null}
      <Table
        rowKey="id"
        loading={toggles.isLoading}
        pagination={false}
        dataSource={toggles.data ?? []}
        columns={[
          { title: copy.market, dataIndex: 'market' },
          {
            title: copy.channel,
            dataIndex: 'channel',
            render: (value: string) => copy.channels[value as keyof typeof copy.channels] ?? value,
          },
          {
            title: copy.line,
            dataIndex: 'line_code',
            render: (value: string) => copy.lines[value as keyof typeof copy.lines] ?? value,
          },
          {
            title: copy.state,
            dataIndex: 'enabled',
            render: (value: boolean) => (value ? copy.enabled : copy.disabled),
          },
        ]}
      />
      <PermissionGuard permission={Perm.LANDED_COST_CALC}>
        <Form form={form} layout="vertical" style={{ marginTop: 16 }} initialValues={{ enabled: true }} onFinish={(values) => save.mutate(values)}>
          <Space wrap align="start">
            <Form.Item name="market" label={copy.market} rules={[{ required: true }]}>
              <Select style={{ width: 120 }} options={CONTENT_MARKETS.map((item) => ({ value: item, label: item }))} />
            </Form.Item>
            <Form.Item name="channel" label={copy.channel} rules={[{ required: true }]}>
              <Select style={{ width: 160 }} options={FEE_CHANNELS.map((item) => ({ value: item, label: copy.channels[item] }))} />
            </Form.Item>
            <Form.Item name="line_code" label={copy.line} rules={[{ required: true }]}>
              <Select style={{ width: 180 }} options={LINE_CODES.map((item) => ({ value: item, label: copy.lines[item] }))} />
            </Form.Item>
            <Form.Item name="enabled" label={copy.enabled} valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          {save.isError ? <Alert type="error" message={messageOf(save.error)} /> : null}
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            {copy.saveToggle}
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
        <TogglePanel />
        <FeePanel />
      </Space>
    </CostGuard>
  );
}
