/**
 * 路由与菜单的单一事实来源。
 *
 * 菜单项带 `permission`，未授权的项**不渲染**（而不是渲染成灰色）——
 * 灰色菜单会暴露"系统里还有这个功能"，对 B 端多角色产品来说是不必要的干扰。
 */

import {
  AppstoreOutlined,
  AuditOutlined,
  DashboardOutlined,
  GlobalOutlined,
  InboxOutlined,
  SafetyOutlined,
  ShopOutlined,
  ShoppingCartOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';

import { Perm, type PermValue } from '@/api/types';

export interface MenuItemConfig {
  key: string;
  path: string;
  label: string;
  icon: ReactNode;
  permission: PermValue;
  /** 该功能在哪个里程碑交付 —— 未交付的页面会渲染占位说明 */
  milestone: string;
  delivered: boolean;
}

export const MENU_ITEMS: readonly MenuItemConfig[] = [
  {
    key: 'dashboard',
    path: '/dashboard',
    label: '数据看板',
    icon: <DashboardOutlined />,
    permission: Perm.DASHBOARD_READ,
    milestone: 'M5（Week 10–11）',
    delivered: false,
  },
  {
    key: 'shops',
    path: '/shops',
    label: '店铺授权',
    icon: <ShopOutlined />,
    permission: Perm.SHOP_READ,
    milestone: 'M1（Week 2–3）',
    delivered: false,
  },
  {
    key: 'orders',
    path: '/orders',
    label: '订单管理',
    icon: <ShoppingCartOutlined />,
    permission: Perm.ORDER_READ,
    milestone: 'M2（Week 4–5）',
    delivered: false,
  },
  {
    key: 'products',
    path: '/products',
    label: '商品库',
    icon: <AppstoreOutlined />,
    permission: Perm.PRODUCT_READ,
    milestone: 'M3（Week 6–7）',
    delivered: false,
  },
  {
    key: 'inventory',
    path: '/inventory',
    label: '库存管理',
    icon: <InboxOutlined />,
    permission: Perm.INVENTORY_READ,
    milestone: 'M3（Week 6–7）',
    delivered: false,
  },
  {
    key: 'compliance',
    path: '/compliance',
    label: '合规与算账',
    icon: <SafetyOutlined />,
    permission: Perm.COMPLIANCE_READ,
    milestone: 'M4（Week 8–9）· ★ 核心差异化',
    delivered: false,
  },
  {
    key: 'purchase',
    path: '/purchase',
    label: '采购管理',
    icon: <GlobalOutlined />,
    permission: Perm.PURCHASE_READ,
    milestone: 'M5（Week 10–11）',
    delivered: false,
  },
  {
    key: 'finance',
    path: '/finance',
    label: '财务与利润',
    icon: <WalletOutlined />,
    permission: Perm.FINANCE_READ,
    milestone: 'M4/M5',
    delivered: false,
  },
  {
    key: 'system',
    path: '/system',
    label: '系统管理',
    icon: <AuditOutlined />,
    permission: Perm.SYSTEM_READ,
    milestone: 'M6（Week 12）',
    delivered: false,
  },
] as const;
