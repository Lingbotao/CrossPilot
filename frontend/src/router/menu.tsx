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
import { ExceptionsPage, OrdersPage, ToShipPage } from '@/pages/orders/OrdersPage';
import { ReturnsPage } from '@/pages/orders/ReturnsPage';
import { OrderSettingsPage } from '@/pages/orders/SettingsPage';
import { FlowsPage } from '@/pages/inventory/FlowsPage';
import { OverviewPage } from '@/pages/inventory/OverviewPage';
import { ReplenishmentPage } from '@/pages/inventory/ReplenishmentPage';
import { WarehousesPage } from '@/pages/inventory/WarehousesPage';
import { CategoryTemplatesPage } from '@/pages/products/CategoryTemplatesPage';
import { ImagesPage } from '@/pages/products/ImagesPage';
import { ListingsPage } from '@/pages/products/ListingsPage';
import { LocalePage } from '@/pages/products/LocalePage';
import { ProductsPage } from '@/pages/products/ProductsPage';
import { PublishPage } from '@/pages/products/PublishPage';
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
  children?: readonly MenuItemConfig[];
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
    delivered: true,
    children: [
      {
        key: 'orders-all',
        path: '/orders',
        label: zhCN.menu.ordersAll,
        icon: null,
        permission: Perm.ORDER_READ,
        milestone: 'M2（Week 4–5）',
        delivered: true,
        component: OrdersPage,
      },
      {
        key: 'orders-to-ship',
        path: '/orders/to-ship',
        label: zhCN.menu.ordersToShip,
        icon: null,
        permission: Perm.ORDER_READ,
        milestone: 'M2（Week 4–5）',
        delivered: true,
        component: ToShipPage,
      },
      {
        key: 'orders-exceptions',
        path: '/orders/exceptions',
        label: zhCN.menu.ordersExceptions,
        icon: null,
        permission: Perm.ORDER_READ,
        milestone: 'M2（Week 4–5）',
        delivered: true,
        component: ExceptionsPage,
      },
      {
        key: 'orders-returns',
        path: '/orders/returns',
        label: zhCN.menu.ordersReturns,
        icon: null,
        permission: Perm.ORDER_READ,
        milestone: 'M2（Week 4–5）',
        delivered: true,
        component: ReturnsPage,
      },
      {
        key: 'orders-settings',
        path: '/orders/settings',
        label: zhCN.menu.ordersSettings,
        icon: null,
        permission: Perm.ORDER_RULE,
        milestone: 'M2（Week 4–5）',
        delivered: true,
        component: OrderSettingsPage,
      },
    ],
  },
  {
    key: 'products',
    path: '/products',
    label: zhCN.menu.products,
    icon: <AppstoreOutlined />,
    permission: Perm.PRODUCT_READ,
    milestone: 'M3（Week 6–7）',
    delivered: true,
    children: [
      {
        key: 'products-library',
        path: '/products',
        label: zhCN.menu.productsLibrary,
        icon: null,
        permission: Perm.PRODUCT_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: ProductsPage,
      },
      {
        key: 'products-images',
        path: '/products/images',
        label: zhCN.menu.productImages,
        icon: null,
        permission: Perm.PRODUCT_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: ImagesPage,
      },
      {
        key: 'products-locale',
        path: '/products/locale',
        label: zhCN.menu.productLocale,
        icon: null,
        permission: Perm.PRODUCT_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: LocalePage,
      },
      {
        key: 'products-listings',
        path: '/products/listings',
        label: zhCN.menu.productListings,
        icon: null,
        permission: Perm.PRODUCT_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: ListingsPage,
      },
      {
        key: 'products-publish',
        path: '/products/publish',
        label: zhCN.menu.productPublish,
        icon: null,
        permission: Perm.PRODUCT_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: PublishPage,
      },
      {
        key: 'products-categories',
        path: '/products/categories',
        label: zhCN.menu.productCategories,
        icon: null,
        permission: Perm.PRODUCT_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: CategoryTemplatesPage,
      },
    ],
  },
  {
    key: 'inventory',
    path: '/inventory',
    label: zhCN.menu.inventory,
    icon: <InboxOutlined />,
    permission: Perm.INVENTORY_READ,
    milestone: 'M3（Week 6–7）',
    delivered: true,
    children: [
      {
        key: 'inventory-overview',
        path: '/inventory',
        label: zhCN.menu.inventoryOverview,
        icon: null,
        permission: Perm.INVENTORY_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: OverviewPage,
      },
      {
        key: 'inventory-warehouses',
        path: '/inventory/warehouses',
        label: zhCN.menu.inventoryWarehouses,
        icon: null,
        permission: Perm.INVENTORY_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: WarehousesPage,
      },
      {
        key: 'inventory-flows',
        path: '/inventory/flows',
        label: zhCN.menu.inventoryFlows,
        icon: null,
        permission: Perm.INVENTORY_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: FlowsPage,
      },
      {
        key: 'inventory-replenishment',
        path: '/inventory/replenishment',
        label: zhCN.menu.inventoryReplenishment,
        icon: null,
        permission: Perm.INVENTORY_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: ReplenishmentPage,
      },
      {
        key: 'inventory-transfers',
        path: '/inventory/transfers',
        label: zhCN.menu.inventoryTransfers,
        icon: null,
        permission: Perm.INVENTORY_READ,
        milestone: 'M3（Week 6–7）',
        delivered: false,
      },
      {
        key: 'inventory-stocktaking',
        path: '/inventory/stocktaking',
        label: zhCN.menu.inventoryStocktaking,
        icon: null,
        permission: Perm.INVENTORY_READ,
        milestone: 'M3（Week 6–7）',
        delivered: false,
      },
    ],
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
