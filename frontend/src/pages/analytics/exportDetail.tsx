import { useQuery } from '@tanstack/react-query';
import { Button, Form, Input, Modal, Select, Space, Typography } from 'antd';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import { reportsApi } from '@/api/reports';
import type { ReportExportView } from '@/api/types';
import { feedback } from '@/app/feedback';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.analyticsPage;

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.requestFailed;
}

function statusLabel(status: ReportExportView['status']): string {
  return copy.exportStatuses[status];
}

export function ExportDetail({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) {
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
