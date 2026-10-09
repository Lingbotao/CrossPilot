import { useQuery } from '@tanstack/react-query';
import { Alert, Button, DatePicker, Form, Input, Space, Table, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import dayjs from 'dayjs';
import * as echarts from 'echarts';
import { useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/client';
import { profitApi } from '@/api/profit';
import type { WaterfallStepView } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.waterfallPage;

interface FilterForm {
  range: [Dayjs, Dayjs];
  currency?: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

function WaterfallChart({ steps }: { steps: WaterfallStepView[] }) {
  const node = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!node.current) {
      return undefined;
    }
    const chart = echarts.init(node.current);
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: [copy.title, copy.memo] },
      xAxis: { type: 'category', data: steps.map((step) => step.label), axisLabel: { interval: 0, rotate: 30 } },
      yAxis: { type: 'value' },
      series: [
        {
          name: copy.title,
          type: 'bar',
          data: steps.map((step) => (step.memo ? null : step.amount)),
        },
        {
          name: copy.memo,
          type: 'bar',
          data: steps.map((step) => (step.memo ? step.amount : null)),
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [steps]);
  return <div ref={node} style={{ height: 360, width: '100%' }} />;
}

export function WaterfallPage() {
  const [form] = Form.useForm<FilterForm>();
  const [query, setQuery] = useState({
    date_from: dayjs().subtract(6, 'day').format('YYYY-MM-DD'),
    date_to: dayjs().format('YYYY-MM-DD'),
    currency: '',
  });
  const view = useQuery({
    queryKey: ['profit-waterfall', query],
    queryFn: () =>
      profitApi.waterfall({
        date_from: query.date_from,
        date_to: query.date_to,
        currency: query.currency || undefined,
      }),
  });
  const steps = view.data?.steps ?? [];

  return (
    <CostGuard fallback={<Typography.Text type="secondary">无权限查看成本与利润</Typography.Text>}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Typography.Title level={3}>{copy.title}</Typography.Title>
        <Alert type="info" message={copy.hint} />
        <Form<FilterForm>
          form={form}
          layout="inline"
          initialValues={{ range: [dayjs().subtract(6, 'day'), dayjs()] }}
          onFinish={(values) =>
            setQuery({
              date_from: values.range[0].format('YYYY-MM-DD'),
              date_to: values.range[1].format('YYYY-MM-DD'),
              currency: values.currency?.trim().toUpperCase() ?? '',
            })
          }
        >
          <Form.Item name="range" rules={[{ required: true }]}>
            <DatePicker.RangePicker />
          </Form.Item>
          <Form.Item name="currency">
            <Input placeholder={zhCN.landedCostPage.currency} style={{ width: 100 }} />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            {zhCN.common.search ?? '查询'}
          </Button>
        </Form>
        {view.isError ? <Alert type="error" message={messageOf(view.error)} /> : null}
        {steps.length > 0 ? <WaterfallChart steps={steps} /> : <Typography.Text type="secondary">{copy.empty}</Typography.Text>}
        <Table<WaterfallStepView>
          rowKey="code"
          pagination={false}
          dataSource={steps}
          columns={[
            { title: zhCN.landedCostPage.item, dataIndex: 'label' },
            {
              title: zhCN.landedCostPage.amount,
              render: (_, row) => <MoneyText value={row.amount} currency={view.data?.currency} colorize />,
            },
            {
              title: zhCN.landedCostPage.net,
              render: (_, row) =>
                row.memo ? copy.memo : <MoneyText value={row.running} currency={view.data?.currency} />,
            },
            { title: zhCN.landedCostPage.formula, dataIndex: 'formula' },
          ]}
        />
      </Space>
    </CostGuard>
  );
}
