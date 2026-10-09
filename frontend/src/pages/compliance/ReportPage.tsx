import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Space, Statistic, Table, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { complianceApi } from '@/api/compliance';
import type { ComplianceReportItem } from '@/api/types';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.reportPage;

export function ReportPage() {
  const report = useQuery({ queryKey: ['compliance-report'], queryFn: complianceApi.report });
  const data = report.data;

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {copy.title}
      </Typography.Title>
      <Alert type="warning" showIcon message={copy.disclaimer} />
      <Card>
        <Space size={48}>
          <Statistic title={copy.red} value={data?.red ?? 0} valueStyle={{ color: '#cf1322' }} />
          <Statistic title={copy.yellow} value={data?.yellow ?? 0} valueStyle={{ color: '#d48806' }} />
          <Statistic title={copy.green} value={data?.green ?? 0} valueStyle={{ color: '#389e0d' }} />
        </Space>
      </Card>
      <Card
        extra={
          <Button onClick={() => window.print()} disabled={!data}>
            {copy.print}
          </Button>
        }
      >
        <Table<ComplianceReportItem>
          rowKey={(row) => `${row.level}-${row.code}-${row.spu_id}-${row.sku_id ?? ''}-${row.market ?? ''}-${row.cert_type ?? ''}`}
          loading={report.isLoading}
          dataSource={data?.items ?? []}
          pagination={false}
          locale={{ emptyText: copy.empty }}
          columns={[
            {
              title: copy.level,
              dataIndex: 'level',
              width: 90,
              render: (value: string) =>
                value === 'L1' ? <Tag color="red">{copy.l1}</Tag> : <Tag color="gold">{copy.l2}</Tag>,
            },
            { title: copy.product, dataIndex: 'title', render: (value: string) => value || '—' },
            {
              title: copy.sku,
              dataIndex: 'sku_code',
              render: (value: string | null) => value || '—',
            },
            {
              title: copy.market,
              dataIndex: 'market',
              render: (value: string | null) => value || '—',
            },
            { title: copy.problem, dataIndex: 'summary' },
            {
              title: copy.fix,
              dataIndex: 'fix_path',
              width: 100,
              render: (value: string) => <Link to={value}>{copy.fix}</Link>,
            },
          ]}
        />
      </Card>
    </Space>
  );
}
