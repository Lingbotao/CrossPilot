import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Form, Input, Modal, Row, Select, Space, Statistic, Typography } from 'antd';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { dashboardApi } from '@/api/dashboard';
import { ApiError } from '@/api/client';
import { authApi } from '@/api/auth';
import { reportsApi } from '@/api/reports';
import { Perm, type ReportExportView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { MoneyText } from '@/components/MoneyText';
import { CostGuard, PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

import { AnalyticsFilters } from './filters';
import { percentLabel } from './query';
import { useAnalyticsFilters } from './useAnalytics';

const copy = zhCN.analyticsPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

function statusLabel(status: ReportExportView['status']): string {
  return copy.exportStatuses[status];
}

function ExportDetail({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [exportId, setExportId] = useState<string | null>(null);
  const [form] = Form.useForm<{ file_format: 'csv' | 'xlsx'; password: string }>();
  const saved = useRef<string | null>(null);
  const job = useQuery({
    queryKey: ['reports', 'export', exportId],
    queryFn: () => reportsApi.get(exportId ?? ''),
    enabled: exportId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'PENDING' || status === 'RUNNING' ? 1500 : false;
    },
  });

  useEffect(() => {
    const current = job.data;
    if (!current || current.status !== 'SUCCEEDED' || saved.current === current.id) return;
    saved.current = current.id;
    void reportsApi.download(current.id).then(
      () => feedback().message.success(copy.exportReady),
      (error: unknown) => feedback().message.error(messageOf(error)),
    );
  }, [job.data]);

  const submit = async (values: { file_format: 'csv' | 'xlsx'; password: string }) => {
    setSubmitting(true);
    try {
      const confirmed = await authApi.confirmPassword({ password: values.password, action: 'report.export' });
      const created = await reportsApi.create(
        { dataset: 'sku_profit', file_format: values.file_format, date_from: dateFrom, date_to: dateTo },
        confirmed.confirmation_token,
      );
      saved.current = null;
      setExportId(created.id);
      setOpen(false);
      form.resetFields();
    } catch (error) {
      feedback().message.error(messageOf(error));
    } finally {
      setSubmitting(false);
    }
  };

  const current = job.data;
  let status: ReactNode = null;
  if (current) {
    status = (
      <Typography.Text type={current.status === 'FAILED' ? 'danger' : 'secondary'}>
        {statusLabel(current.status)}
        {current.error ? ` ${current.error}` : ''}
      </Typography.Text>
    );
  } else if (exportId) {
    status = <Typography.Text type="secondary">{copy.exportQueued}</Typography.Text>;
  }
  return (
    <Space align="start">
      <Button onClick={() => setOpen(true)}>{copy.exportDetail}</Button>
      {status}
      <Modal
        title={copy.exportDetail}
        open={open}
        confirmLoading={submitting}
        onCancel={() => setOpen(false)}
        onOk={() => void form.submit()}
        okText={zhCN.common.confirm}
        cancelText={zhCN.common.cancel}
      >
        <Typography.Paragraph type="secondary">{copy.exportHint}</Typography.Paragraph>
        <Typography.Paragraph>
          {dateFrom} 至 {dateTo}
        </Typography.Paragraph>
        <Form form={form} layout="vertical" initialValues={{ file_format: 'csv' }} onFinish={(values) => void submit(values)}>
          <Form.Item name="file_format" label={copy.exportFormat} rules={[{ required: true, message: zhCN.common.required }]}>
            <Select
              options={[
                { value: 'csv', label: copy.exportCsv },
                { value: 'xlsx', label: copy.exportExcel },
              ]}
            />
          </Form.Item>
          <Form.Item name="password" label={zhCN.auth.password} rules={[{ required: true, message: zhCN.common.required }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

export function AnalyticsOverviewPage() {
  const { values, setValues, query, rebuild } = useAnalyticsFilters();
  const overview = useQuery({
    queryKey: ['dashboard', 'overview', query],
    queryFn: () => dashboardApi.overview(query),
  });
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <div>
        <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
          <div>
            <Typography.Title level={3}>{copy.overviewTitle}</Typography.Title>
            <Typography.Paragraph type="secondary">{copy.overviewDescription}</Typography.Paragraph>
          </div>
          <PermissionGuard permission={Perm.REPORT_EXPORT}>
            <ExportDetail dateFrom={query.date_from} dateTo={query.date_to} />
          </PermissionGuard>
        </Space>
      </div>
      <Card>
        <AnalyticsFilters
          initial={values}
          onSearch={setValues}
          onRebuild={(next) => {
            setValues(next);
            rebuild.mutate(next);
          }}
          rebuilding={rebuild.isPending}
        />
      </Card>
      {overview.isError ? <Alert type="error" message={messageOf(overview.error)} /> : null}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title={<Link to="/analytics/fulfillment">{copy.orders}</Link>} value={overview.data?.order_count ?? 0} />
            <Typography.Text type="secondary">{copy.change} {percentLabel(overview.data?.order_change)}</Typography.Text>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card title={<Link to="/analytics/trends">{copy.gmv}</Link>}>
            {(overview.data?.currencies ?? []).length === 0 ? '—' : null}
            {(overview.data?.currencies ?? []).map((row) => (
              <div key={row.currency}>
                <MoneyText value={row.gmv} currency={row.currency} />
                <Typography.Text type="secondary"> {copy.change} {percentLabel(row.gmv_change)}</Typography.Text>
              </div>
            ))}
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title={<Link to="/analytics/fulfillment">{copy.onTimeRate}</Link>} value={percentLabel(overview.data?.on_time_rate)} />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <CostGuard fallback={<Typography.Text type="secondary">{copy.costHidden}</Typography.Text>}>
              <Typography.Text strong>
                <Link to="/analytics/ranking">{copy.netProfit}</Link>
              </Typography.Text>
              {(overview.data?.currencies ?? []).map((row) => (
                <div key={row.currency}>
                  <MoneyText value={row.net_profit} currency={row.currency} colorize />
                  <Typography.Text type="secondary"> {copy.change} {percentLabel(row.profit_change)}</Typography.Text>
                </div>
              ))}
            </CostGuard>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <CostGuard fallback={<Typography.Text type="secondary">{copy.costHidden}</Typography.Text>}>
              <Typography.Text strong>
                <Link to="/analytics/costs">{copy.netMargin}</Link>
              </Typography.Text>
              {(overview.data?.currencies ?? []).map((row) => (
                <div key={row.currency}>{row.currency} {percentLabel(row.net_margin)}</div>
              ))}
            </CostGuard>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <CostGuard fallback={<Typography.Text type="secondary">{copy.costHidden}</Typography.Text>}>
              <Typography.Text strong>
                <Link to="/analytics/ads">{copy.roas}</Link>
              </Typography.Text>
              {(overview.data?.currencies ?? []).map((row) => (
                <div key={row.currency}>{row.currency} {row.roas ?? '—'}</div>
              ))}
            </CostGuard>
          </Card>
        </Col>
      </Row>
      <Typography.Paragraph type="secondary">
        {copy.yoy} {percentLabel(overview.data?.order_yoy)} · {copy.returnRate} {percentLabel(overview.data?.return_rate)}
      </Typography.Paragraph>
    </Space>
  );
}
