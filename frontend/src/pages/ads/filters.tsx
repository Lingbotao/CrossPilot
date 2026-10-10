import { DatePicker, Form, Input, Select } from 'antd';

import zhCN from '@/i18n/zh-CN';

import { AD_PLATFORMS } from './query';

const copy = zhCN.adsPage;

export function AdsFilters() {
  return (
    <>
      <Form.Item name="range" label={copy.range} rules={[{ required: true, message: copy.range }]}>
        <DatePicker.RangePicker allowClear={false} />
      </Form.Item>
      <Form.Item name="platform_code" label={copy.platform}>
        <Select allowClear placeholder={copy.allPlatforms} options={[...AD_PLATFORMS]} style={{ width: 160 }} />
      </Form.Item>
      <Form.Item name="shop_id" label={copy.shopId}>
        <Input placeholder={copy.shopId} allowClear style={{ width: 180 }} />
      </Form.Item>
    </>
  );
}
