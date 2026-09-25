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
  KeyOutlined,
  SafetyOutlined,
  ShopOutlined,
  ShoppingCartOutlined,
  TeamOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import type { ComponentType, ReactNode } from 'react';

import { Perm, type PermValue } from '@/api/types';
import zhCN from '@/i18n/zh-CN';
import { DashboardPage } from '@/pages/DashboardPage';
import { ShopsPage } from '@/pages/shops/ShopsPage';
import { AuditPage } from '@/pages/system/AuditPage';
import { MembersPage } from '@/pages/system/MembersPage';
import { RolesPage } from '@/pages/system/RolesPage';

export interface MenuItemConfig {
  key: string;
  path: string;
  label: string;
  icon: ReactNode;
  permission: PermValue;
  /** 该功能在哪个里程碑交付 —— 未交付的页面会渲染占位说明 */
  milestone: string;
  delivered: boolean;
  /** 已交付页面组件；未交付项由路由统一渲染 PlaceholderPage。 */
  component?: ComponentType;
}

export const MENU_ITEMS: readonly MenuItemConfig[] = [
  {
    key: 'dashboard',
    path: '/dashboard',
    label: zhCN.menu.dashboard,
    icon: <DashboardOutlined />,
    permission: Perm.DASHBOARD_READ,
    milestone: 'M5（Week 10–11）',
    delivered: true,
    component: DashboardPage,
  },
  {
    key: 'shops',
    path: '/shops',
    label: zhCN.menu.shops,
    icon: <ShopOutlined />,
    permission: Perm.SHOP_READ,
    milestone: 'M1（Week 2–3）',
    delivered: true,
    component: ShopsPage,
  },
  {
    key: 'orders',
    path: '/orders',
    label: zhCN.menu.orders,
    icon: <ShoppingCartOutlined />,
    permission: Perm.ORDER_READ,
    milestone: 'M2（Week 4–5）',
    delivered: false,
  },
  {
    key: 'products',
    path: '/products',
    label: zhCN.menu.products,
    icon: <AppstoreOutlined />,
    permission: Perm.PRODUCT_READ,
    milestone: 'M3（Week 6–7）',
    delivered: false,
  },
  {
    key: 'inventory',
    path: '/inventory',
    label: zhCN.menu.inventory,
    icon: <InboxOutlined />,
    permission: Perm.INVENTORY_READ,
    milestone: 'M3（Week 6–7）',
    delivered: false,
  },
  {
    key: 'compliance',
    path: '/compliance',
    label: zhCN.menu.compliance,
    icon: <SafetyOutlined />,
    permission: Perm.COMPLIANCE_READ,
    milestone: 'M4（Week 8–9）· ★ 核心差异化',
    delivered: false,
  },
  {
    key: 'purchase',
    path: '/purchase',
    label: zhCN.menu.purchase,
    icon: <GlobalOutlined />,
    permission: Perm.PURCHASE_READ,
    milestone: 'M5（Week 10–11）',
    delivered: false,
  },
  {
    key: 'finance',
    path: '/finance',
    label: zhCN.menu.finance,
    icon: <WalletOutlined />,
    permission: Perm.FINANCE_READ,
    milestone: 'M4/M5',
    delivered: false,
  },
  {
    key: 'members',
    path: '/system/members',
    label: zhCN.menu.members,
    icon: <TeamOutlined />,
    permission: Perm.MEMBER_READ,
    milestone: 'M1（Week 2–3）',
    delivered: true,
    component: MembersPage,
  },
  {
    key: 'roles',
    path: '/system/roles',
    label: zhCN.menu.roles,
    icon: <KeyOutlined />,
    permission: Perm.MEMBER_READ,
    milestone: 'M1（Week 2–3）',
    delivered: true,
    component: RolesPage,
  },
  {
    key: 'audit',
    path: '/system/audit-logs',
    label: zhCN.menu.audit,
    icon: <AuditOutlined />,
    permission: Perm.AUDIT_READ,
    milestone: 'M1（Week 2–3）',
    delivered: true,
    component: AuditPage,
  },
] as const;
