import { Alert, Menu } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';

import { ordersApi } from '@/api/orders';
import { Perm } from '@/api/types';
import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';

const LINKS = [
  { path: '/orders', label: zhCN.menu.ordersAll, permission: Perm.ORDER_READ },
  { path: '/orders/to-ship', label: zhCN.menu.ordersToShip, permission: Perm.ORDER_READ },
  { path: '/orders/exceptions', label: zhCN.menu.ordersExceptions, permission: Perm.ORDER_READ },
  { path: '/orders/returns', label: zhCN.menu.ordersReturns, permission: Perm.ORDER_READ },
  { path: '/orders/settings', label: zhCN.menu.ordersSettings, permission: Perm.ORDER_RULE },
] as const;

export function OrderNav() {
  const navigate = useNavigate();
  const location = useLocation();
  const { can } = usePermission();
  const visible = LINKS.filter((item) => can(item.permission));
  const selected = [...visible].reverse().find((item) => location.pathname === item.path)?.path ?? '/orders';
  return (
    <Menu
      mode="horizontal"
      selectedKeys={[selected]}
      style={{ marginBottom: 16, background: 'transparent' }}
      items={visible.map((item) => ({
        key: item.path,
        label: item.label,
        onClick: () => navigate(item.path),
      }))}
    />
  );
}

export function FreshnessBanner() {
  const freshness = useQuery({ queryKey: ['order-freshness'], queryFn: ordersApi.freshness });
  const lines = freshness.data ?? [];
  if (lines.length === 0) {
    return null;
  }
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 16 }}
      message={lines.map((line) => `${line.shop_name}：${line.message}`).join('；')}
    />
  );
}
