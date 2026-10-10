import { Button, DatePicker, Form, Input, Select, Space } from 'antd';

import zhCN from '@/i18n/zh-CN';

import { ANALYTICS_PLATFORMS, type AnalyticsFilterValues } from './query';

const copy = zhCN.analyticsPage;

export function AnalyticsFilters({
  initial,
  onSearch,
  onRebuild,
  rebuilding,
}: {
  initial: AnalyticsFilterValues;
  onSearch: (values: AnalyticsFilterValues) => void;
  onRebuild: (values: AnalyticsFilterValues) => void;
  rebuilding: boolean;
}) {
  const [form] = Form.useForm<AnalyticsFilterValues>();
  return (
    <Form form={form} layout="inline" initialValues={initial} onFinish={onSearch}>
      <Form.Item name="range" label={copy.range} rules={[{ required: true, message: copy.range }]}>
        <DatePicker.RangePicker allowClear={false} />
      </Form.Item>
      <Form.Item name="platform_code" label={copy.platform}>
        <Select allowClear placeholder={copy.allPlatforms} options={[...ANALYTICS_PLATFORMS]} style={{ width: 160 }} />
      </Form.Item>
      <Form.Item name="shop_id" label={copy.shopId}>
        <Input placeholder={copy.shopId} allowClear style={{ width: 180 }} />
      </Form.Item>
      <Form.Item>
        <Space>
          <Button type="primary" htmlType="submit">
            {zhCN.common.search}
          </Button>
          <Button loading={rebuilding} onClick={() => onRebuild(form.getFieldsValue())}>
            {copy.rebuild}
          </Button>
        </Space>
      </Form.Item>
    </Form>
  );
}
