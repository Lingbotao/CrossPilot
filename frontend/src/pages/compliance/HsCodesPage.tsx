import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Select, Space, Table, Typography } from 'antd';
import { useState } from 'react';

import { ApiError } from '@/api/client';
import { complianceApi } from '@/api/compliance';
import { productsApi } from '@/api/products';
import { CONTENT_MARKETS, Perm, type HsCodeHit, type SpuHsBindingView } from '@/api/types';
import { feedback } from '@/app/feedback';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';

const copy = zhCN.hsPage;

interface BindForm {
  spu_id: string;
  market: string;
  basis: string;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : zhCN.inventoryPage.requestFailed;
}

function formatWhen(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString('zh-CN');
}

export function HsCodesPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<BindForm>();
  const [keyword, setKeyword] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [recommendSpu, setRecommendSpu] = useState<string | undefined>();
  const [selected, setSelected] = useState<HsCodeHit | null>(null);
  const [spuKeyword, setSpuKeyword] = useState('');
  const watchedSpu = Form.useWatch('spu_id', form);

  const results = useQuery({
    queryKey: ['hs-search', submitted, recommendSpu ?? ''],
    enabled: Boolean(submitted) || Boolean(recommendSpu),
    queryFn: () =>
      complianceApi.searchHsCodes({
        q: submitted || undefined,
        spu_id: submitted ? undefined : recommendSpu,
        limit: 20,
      }),
  });

  const spus = useQuery({
    queryKey: ['hs-spu-options', spuKeyword],
    queryFn: () => productsApi.list({ q: spuKeyword || undefined, limit: 20 }),
  });

  const bindings = useQuery({
    queryKey: ['spu-hs-bindings', watchedSpu],
    enabled: Boolean(watchedSpu),
    queryFn: () => complianceApi.bindings(watchedSpu ?? ''),
  });

  const bind = useMutation({
    mutationFn: (values: BindForm) => {
      if (!selected) {
        return Promise.reject(new Error(copy.pickFirst));
      }
      return complianceApi.bindHsCode(values.spu_id, {
        market: values.market,
        hs_code_id: selected.id,
        basis: values.basis.trim(),
      });
    },
    onSuccess: async () => {
      feedback().message.success(copy.bound);
      await queryClient.invalidateQueries({ queryKey: ['spu-hs-bindings', watchedSpu] });
    },
  });

  const runSearch = () => {
    const next = keyword.trim();
    if (!next) {
      return;
    }
    setRecommendSpu(undefined);
    setSubmitted(next);
  };

  const runRecommend = () => {
    if (!watchedSpu) {
      return;
    }
    setSubmitted('');
    setRecommendSpu(watchedSpu);
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {copy.title}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{copy.hint}</Typography.Paragraph>
        <Space wrap>
          <Input
            style={{ width: 320 }}
            placeholder={copy.queryPlaceholder}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onPressEnter={runSearch}
          />
          <Button type="primary" onClick={runSearch}>
            {copy.search}
          </Button>
          <Button onClick={runRecommend} disabled={!watchedSpu}>
            {copy.recommend}
          </Button>
        </Space>
      </Card>

      <Card>
        {results.isError ? (
          <Alert type="error" message={messageOf(results.error)} style={{ marginBottom: 12 }} />
        ) : null}
        <Table<HsCodeHit>
          rowKey="id"
          loading={results.isFetching}
          dataSource={results.data ?? []}
          pagination={false}
          locale={{ emptyText: copy.empty }}
          rowSelection={{
            type: 'radio',
            selectedRowKeys: selected ? [selected.id] : [],
            onChange: (_keys, rows) => setSelected(rows[0] ?? null),
          }}
          columns={[
            { title: copy.code, dataIndex: 'code', width: 140 },
            { title: copy.description, dataIndex: 'description' },
            { title: copy.chapter, dataIndex: 'chapter', width: 80 },
            { title: copy.source, dataIndex: 'source' },
          ]}
        />
      </Card>

      <PermissionGuard permission={Perm.COMPLIANCE_WRITE}>
        <Card>
          <Form<BindForm> form={form} layout="vertical" onFinish={(values) => bind.mutate(values)}>
            <Typography.Text>
              {copy.picked}：{selected ? `${selected.code} ${selected.description}` : copy.pickFirst}
            </Typography.Text>
            <Form.Item name="spu_id" label={copy.spu} rules={[{ required: true, message: zhCN.common.required }]}>
              <Select
                showSearch
                filterOption={false}
                placeholder={copy.spuPlaceholder}
                onSearch={setSpuKeyword}
                options={(spus.data?.items ?? []).map((item) => ({ value: item.id, label: item.title }))}
              />
            </Form.Item>
            <Form.Item name="market" label={copy.market} rules={[{ required: true, message: zhCN.common.required }]}>
              <Select options={CONTENT_MARKETS.map((code) => ({ value: code, label: code }))} />
            </Form.Item>
            <Form.Item name="basis" label={copy.basis} rules={[{ required: true, message: zhCN.common.required }]}>
              <Input.TextArea rows={3} maxLength={500} placeholder={copy.basisPlaceholder} />
            </Form.Item>
            <Button type="primary" htmlType="submit" disabled={!selected} loading={bind.isPending}>
              {copy.bind}
            </Button>
          </Form>
        </Card>
      </PermissionGuard>

      {watchedSpu ? (
        <Card title={copy.bindings}>
          {bindings.isError ? (
            <Alert type="error" message={messageOf(bindings.error)} style={{ marginBottom: 12 }} />
          ) : null}
          <Table<SpuHsBindingView>
            rowKey="id"
            loading={bindings.isFetching}
            dataSource={bindings.data ?? []}
            pagination={false}
            locale={{ emptyText: copy.noBindings }}
            columns={[
              { title: copy.market, dataIndex: 'market', width: 80 },
              { title: copy.code, dataIndex: 'code', width: 140 },
              { title: copy.description, dataIndex: 'description' },
              { title: copy.basis, dataIndex: 'basis' },
              { title: copy.operator, dataIndex: 'updated_by', width: 180 },
              {
                title: copy.updatedAt,
                dataIndex: 'updated_at',
                width: 200,
                render: (value: string) => formatWhen(value),
              },
            ]}
          />
        </Card>
      ) : null}
    </Space>
  );
}
