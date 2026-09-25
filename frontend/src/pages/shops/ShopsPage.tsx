import { LinkOutlined, SyncOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import { shopsApi, syncTasksApi } from '@/api/shops';
import { Perm, type Shop, type SyncModule, type SyncTask } from '@/api/types';
import { MoneyText } from '@/components/MoneyText';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { useAuthStore } from '@/store/auth';
import { formatDateTime } from '@/utils/format';

const copy = zhCN.shopPage;

const HEALTH_COLOR = {
  green: 'success',
  yellow: 'warning',
  red: 'error',
} as const;

const HEALTH_LABEL = {
  green: copy.healthGreen,
  yellow: copy.healthYellow,
  red: copy.healthRed,
} as const;

const STATUS_LABEL: Record<number, string> = {
  1: copy.active,
  2: copy.expired,
  3: copy.unbound,
};

export function ShopsPage() {
  const user = useAuthStore((state) => state.user);
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [authorizeOpen, setAuthorizeOpen] = useState(false);
  const [syncTarget, setSyncTarget] = useState<Shop | null>(null);
  const [logShop, setLogShop] = useState<Shop | null>(null);
  const [unbindTarget, setUnbindTarget] = useState<Shop | null>(null);
  const [password, setPassword] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const handledOAuth = useRef<string | null>(null);
  const [platform, setPlatform] = useState<string>();
  const [site, setSite] = useState<string>();
  const [module, setModule] = useState<SyncModule>('order');

  const catalog = useQuery({ queryKey: ['shop-catalog'], queryFn: shopsApi.catalog });
  const shops = useQuery({ queryKey: ['shops'], queryFn: () => shopsApi.list() });
  const logs = useQuery({
    queryKey: ['sync-tasks', logShop?.id],
    queryFn: () => syncTasksApi.list(logShop?.id),
    enabled: logShop !== null,
  });

  const callback = useMutation({
    mutationFn: ({ platformCode, code, state }: { platformCode: string; code: string; state: string }) =>
      shopsApi.callback(platformCode, code, state),
    onSuccess: async () => {
      setNotice(copy.callbackDone);
      setParams({}, { replace: true });
      await queryClient.invalidateQueries({ queryKey: ['shops'] });
    },
    onError: (error: unknown) => {
      setFormError(error instanceof ApiError ? error.message : copy.callbackFailed);
      setParams({}, { replace: true });
    },
  });

  useEffect(() => {
    const code = params.get('code');
    const state = params.get('state');
    const platformCode = params.get('platform');
    if (!code || !state || !platformCode) {
      return;
    }
    const key = `${platformCode}:${state}`;
    if (handledOAuth.current === key) {
      return;
    }
    handledOAuth.current = key;
    callback.mutate({ platformCode, code, state });
  }, [callback, params]);

  const selected = catalog.data?.find((item) => item.code === platform);
  const verified = Boolean(user?.email_verified_at);

  const startAuthorize = async () => {
    if (!platform || !site) {
      return;
    }
    setFormError(null);
    try {
      const result = await shopsApi.authUrl(platform, site);
      window.location.assign(result.url);
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : copy.callbackFailed);
    }
  };

  const runSync = async () => {
    if (!syncTarget) {
      return;
    }
    setFormError(null);
    try {
      const task = await shopsApi.sync(syncTarget.id, module);
      setSyncTarget(null);
      setLogShop(syncTarget);
      setNotice(task.stats.first_order ? `${copy.firstOrder} ${task.stats.first_order.platform_order_id}` : copy.sync);
      await queryClient.invalidateQueries({ queryKey: ['shops'] });
      await queryClient.invalidateQueries({ queryKey: ['sync-tasks'] });
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : copy.callbackFailed);
    }
  };

  const runUnbind = async () => {
    if (!unbindTarget) {
      return;
    }
    setFormError(null);
    try {
      const confirmed = await authApi.confirmPassword({ password, action: 'shop.unbind' });
      await shopsApi.unbind(unbindTarget.id, confirmed.confirmation_token);
      setUnbindTarget(null);
      setPassword('');
      await queryClient.invalidateQueries({ queryKey: ['shops'] });
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : copy.callbackFailed);
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ marginTop: 0 }}>{copy.title}</Typography.Title>
        <Typography.Paragraph type="secondary">{copy.description}</Typography.Paragraph>
        <Alert type="info" showIcon message={copy.guideTitle} description={copy.guideSteps} />
        {!verified ? <Alert style={{ marginTop: 12 }} type="warning" showIcon message={copy.unverified} /> : null}
        {notice ? <Alert style={{ marginTop: 12 }} type="success" showIcon message={notice} /> : null}
        {formError ? <Alert style={{ marginTop: 12 }} type="error" showIcon message={formError} /> : null}
      </Card>
      <Card
        extra={
          <PermissionGuard permission={Perm.SHOP_GRANT}>
            <Button type="primary" icon={<LinkOutlined />} disabled={!verified} onClick={() => setAuthorizeOpen(true)}>
              {copy.authorize}
            </Button>
          </PermissionGuard>
        }
      >
        <Table<Shop>
          rowKey="id"
          loading={shops.isLoading}
          dataSource={shops.data?.items ?? []}
          locale={{ emptyText: copy.empty }}
          pagination={false}
          columns={[
            { title: copy.platform, dataIndex: 'platform_code' },
            { title: copy.site, dataIndex: 'site_code' },
            { title: copy.shopName, dataIndex: 'shop_name' },
            { title: copy.platformShopId, dataIndex: 'platform_shop_id' },
            {
              title: copy.health,
              dataIndex: 'health',
              render: (health: Shop['health'], row) => (
                <Tag color={HEALTH_COLOR[health]}>{row.health_reason || HEALTH_LABEL[health]}</Tag>
              ),
            },
            {
              title: copy.status,
              dataIndex: 'status',
              render: (status: number) => STATUS_LABEL[status] ?? status,
            },
            {
              title: copy.lastSync,
              dataIndex: 'last_sync_at',
              render: (value: string | null) => formatDateTime(value),
            },
            { title: copy.lastError, dataIndex: 'last_error', render: (value: string | null) => value || '—' },
            {
              title: zhCN.common.actions,
              render: (_, row) => (
                <Space>
                  <PermissionGuard permission={Perm.SHOP_GRANT}>
                    <Button size="small" icon={<SyncOutlined />} onClick={() => setSyncTarget(row)}>{copy.sync}</Button>
                    <Button size="small" danger onClick={() => setUnbindTarget(row)}>{copy.unbind}</Button>
                  </PermissionGuard>
                  <Button size="small" onClick={() => setLogShop(row)}>{copy.syncLogs}</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={copy.authorize}
        open={authorizeOpen}
        onCancel={() => setAuthorizeOpen(false)}
        onOk={() => void startAuthorize()}
        okText={copy.goAuthorize}
        okButtonProps={{ disabled: !platform || !site }}
      >
        <Form layout="vertical">
          <Form.Item label={copy.platform} required>
            <Select
              value={platform}
              options={(catalog.data ?? []).map((item) => ({ value: item.code, label: item.name }))}
              onChange={(value) => {
                setPlatform(value);
                setSite(undefined);
              }}
            />
          </Form.Item>
          <Form.Item label={copy.site} required>
            <Select
              value={site}
              options={(selected?.sites ?? []).map((item) => ({ value: item, label: item }))}
              onChange={setSite}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={copy.sync} open={syncTarget !== null} onCancel={() => setSyncTarget(null)} onOk={() => void runSync()}>
        <Form layout="vertical">
          <Form.Item label={copy.module}>
            <Select
              value={module}
              onChange={setModule}
              options={[
                { value: 'order', label: copy.moduleOrder },
                { value: 'product', label: copy.moduleProduct },
                { value: 'inventory', label: copy.moduleInventory },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={copy.unbindTitle}
        open={unbindTarget !== null}
        onCancel={() => setUnbindTarget(null)}
        onOk={() => void runUnbind()}
        okButtonProps={{ danger: true, disabled: password.length === 0 }}
      >
        <Typography.Paragraph>{copy.unbindHint}</Typography.Paragraph>
        <Input.Password placeholder={copy.password} value={password} onChange={(event) => setPassword(event.target.value)} />
      </Modal>

      <Drawer title={copy.syncLogs} open={logShop !== null} onClose={() => setLogShop(null)} width={560}>
        <Table<SyncTask>
          rowKey="id"
          loading={logs.isLoading}
          dataSource={logs.data?.items ?? []}
          pagination={false}
          columns={[
            { title: copy.module, dataIndex: 'module' },
            {
              title: copy.status,
              dataIndex: 'status',
              render: (status: number) => {
                if (status === 2) return zhCN.sync.success;
                if (status === 3) return zhCN.sync.failed;
                return zhCN.sync.running;
              },
            },
            { title: copy.pulled, render: (_, row) => row.stats.pulled ?? '—' },
            {
              title: copy.firstOrder,
              render: (_, row) => {
                const order = row.stats.first_order;
                if (!order) {
                  return row.error || '—';
                }
                return (
                  <Space direction="vertical" size={0}>
                    <span>{order.platform_order_id}</span>
                    <MoneyText value={order.total_amount} currency={order.currency} />
                  </Space>
                );
              },
            },
            { title: zhCN.audit.time, dataIndex: 'created_at', render: (value: string) => formatDateTime(value) },
          ]}
        />
      </Drawer>
    </Space>
  );
}
