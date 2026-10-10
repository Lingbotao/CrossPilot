/**
 * 路由与菜单的单一事实来源。
 *
 * 菜单项带 `permission`，未授权的项**不渲染**（而不是渲染成灰色）——
 * 灰色菜单会暴露"系统里还有这个功能"，对 B 端多角色产品来说是不必要的干扰。
 */

import {
  AppstoreOutlined,
  AuditOutlined,
  BarChartOutlined,
  DashboardOutlined,
  CustomerServiceOutlined,
  FundOutlined,
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
import { StocktakingPage } from '@/pages/inventory/StocktakingPage';
import { TransfersPage } from '@/pages/inventory/TransfersPage';
import { OverviewPage } from '@/pages/inventory/OverviewPage';
import { ReplenishmentPage } from '@/pages/inventory/ReplenishmentPage';
import { WarehousesPage } from '@/pages/inventory/WarehousesPage';
import { ExchangeRatesPage } from '@/pages/finance/ExchangeRatesPage';
import { LandedCostPage } from '@/pages/finance/LandedCostPage';
import { ProfitPage } from '@/pages/finance/ProfitPage';
import { SettlementsPage } from '@/pages/finance/SettlementsPage';
import { WaterfallPage } from '@/pages/finance/WaterfallPage';
import { CertificatesPage } from '@/pages/compliance/CertificatesPage';
import { TaxRegistrationsPage } from '@/pages/compliance/TaxRegistrationsPage';
import { ReportPage } from '@/pages/compliance/ReportPage';
import { HsCodesPage } from '@/pages/compliance/HsCodesPage';
import { TaxRulesPage } from '@/pages/compliance/TaxRulesPage';
import { CategoryTemplatesPage } from '@/pages/products/CategoryTemplatesPage';
import { ImagesPage } from '@/pages/products/ImagesPage';
import { ListingsPage } from '@/pages/products/ListingsPage';
import { LocalePage } from '@/pages/products/LocalePage';
import { ProductDetailPage } from '@/pages/products/ProductDetailPage';
import { ProductsPage } from '@/pages/products/ProductsPage';
import { PublishPage } from '@/pages/products/PublishPage';
import { CsMessagesPage } from '@/pages/cs/CsMessagesPage';
import { CsTemplatesPage } from '@/pages/cs/CsTemplatesPage';
import { CsTicketsPage } from '@/pages/cs/CsTicketsPage';
import { AnalyticsAdsPage } from '@/pages/analytics/AnalyticsAdsPage';
import { AnalyticsCostsPage } from '@/pages/analytics/AnalyticsCostsPage';
import { AnalyticsFulfillmentPage } from '@/pages/analytics/AnalyticsFulfillmentPage';
import { AnalyticsInventoryPage } from '@/pages/analytics/AnalyticsInventoryPage';
import { AnalyticsOverviewPage } from '@/pages/analytics/AnalyticsOverviewPage';
import { AnalyticsPlatformsPage } from '@/pages/analytics/AnalyticsPlatformsPage';
import { AnalyticsRankingPage } from '@/pages/analytics/AnalyticsRankingPage';
import { AnalyticsTrendsPage } from '@/pages/analytics/AnalyticsTrendsPage';
import { AdsCampaignsPage } from '@/pages/ads/AdsCampaignsPage';
import { AdsKeywordsPage } from '@/pages/ads/AdsKeywordsPage';
import { AdsLossPage } from '@/pages/ads/AdsLossPage';
import { AdsOverviewPage } from '@/pages/ads/AdsOverviewPage';
import { FirstMilePage } from '@/pages/purchase/FirstMilePage';
import { PurchaseOrdersPage } from '@/pages/purchase/PurchaseOrdersPage';
import { SuppliersPage } from '@/pages/purchase/SuppliersPage';
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
  /** 路由仍注册，侧栏不展示。用于带参数的详情页。 */
  hideInMenu?: boolean;
  children?: readonly MenuItemConfig[];
}

export const MENU_ITEMS: readonly MenuItemConfig[] = [
  {
    key: 'dashboard',
    path: '/dashboard',
    label: zhCN.menu.workbench,
    icon: <DashboardOutlined />,
    permission: Perm.DASHBOARD_READ,
    milestone: 'M0',
    delivered: true,
    component: DashboardPage,
  },
  {
    key: 'analytics',
    path: '/analytics',
    label: zhCN.menu.dashboard,
    icon: <BarChartOutlined />,
    permission: Perm.DASHBOARD_READ,
    milestone: 'M5（Week 10–11）',
    delivered: false,
    children: [
      {
        key: 'analytics-overview',
        path: '/analytics/overview',
        label: zhCN.menu.analyticsOverview,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsOverviewPage,
      },
      {
        key: 'analytics-platforms',
        path: '/analytics/platforms',
        label: zhCN.menu.analyticsPlatforms,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsPlatformsPage,
      },
      {
        key: 'analytics-ranking',
        path: '/analytics/ranking',
        label: zhCN.menu.analyticsRanking,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsRankingPage,
      },
      {
        key: 'analytics-trends',
        path: '/analytics/trends',
        label: zhCN.menu.analyticsTrends,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsTrendsPage,
      },
      {
        key: 'analytics-costs',
        path: '/analytics/costs',
        label: zhCN.menu.analyticsCosts,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsCostsPage,
      },
      {
        key: 'analytics-inventory',
        path: '/analytics/inventory',
        label: zhCN.menu.analyticsInventory,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsInventoryPage,
      },
      {
        key: 'analytics-ads',
        path: '/analytics/ads',
        label: zhCN.menu.analyticsAds,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsAdsPage,
      },
      {
        key: 'analytics-fulfillment',
        path: '/analytics/fulfillment',
        label: zhCN.menu.analyticsFulfillment,
        icon: null,
        permission: Perm.DASHBOARD_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AnalyticsFulfillmentPage,
      },
    ],
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
        key: 'products-detail',
        path: '/products/:spuId',
        label: zhCN.productPage.detail,
        icon: null,
        permission: Perm.PRODUCT_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        hideInMenu: true,
        component: ProductDetailPage,
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
        delivered: true,
        component: TransfersPage,
      },
      {
        key: 'inventory-stocktaking',
        path: '/inventory/stocktaking',
        label: zhCN.menu.inventoryStocktaking,
        icon: null,
        permission: Perm.INVENTORY_READ,
        milestone: 'M3（Week 6–7）',
        delivered: true,
        component: StocktakingPage,
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
    children: [
      {
        key: 'compliance-report',
        path: '/compliance/report',
        label: zhCN.menu.complianceReport,
        icon: null,
        permission: Perm.COMPLIANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: ReportPage,
      },
      {
        key: 'compliance-hs',
        path: '/compliance/hs-codes',
        label: zhCN.menu.complianceHs,
        icon: null,
        permission: Perm.COMPLIANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: HsCodesPage,
      },
      {
        key: 'compliance-tax',
        path: '/compliance/tax-rules',
        label: zhCN.menu.complianceTax,
        icon: null,
        permission: Perm.COMPLIANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: TaxRulesPage,
      },
      {
        key: 'compliance-certificates',
        path: '/compliance/certificates',
        label: zhCN.menu.complianceCertificates,
        icon: null,
        permission: Perm.COMPLIANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: CertificatesPage,
      },
      {
        key: 'compliance-tax-registration',
        path: '/compliance/tax-registrations',
        label: zhCN.menu.complianceTaxRegistration,
        icon: null,
        permission: Perm.COMPLIANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: TaxRegistrationsPage,
      },
    ],
  },
  {
    key: 'purchase',
    path: '/purchase',
    label: zhCN.menu.purchase,
    icon: <GlobalOutlined />,
    permission: Perm.PURCHASE_READ,
    milestone: 'M5（Week 10–11）',
    delivered: false,
    children: [
      {
        key: 'purchase-orders',
        path: '/purchase/orders',
        label: zhCN.menu.purchaseOrders,
        icon: null,
        permission: Perm.PURCHASE_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: PurchaseOrdersPage,
      },
      {
        key: 'purchase-suppliers',
        path: '/purchase/suppliers',
        label: zhCN.menu.purchaseSuppliers,
        icon: null,
        permission: Perm.PURCHASE_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: SuppliersPage,
      },
      {
        key: 'purchase-first-mile',
        path: '/purchase/first-mile',
        label: zhCN.menu.purchaseFirstMile,
        icon: null,
        permission: Perm.PURCHASE_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: FirstMilePage,
      },
      {
        key: 'purchase-payables',
        path: '/purchase/payables',
        label: zhCN.menu.purchasePayables,
        icon: null,
        permission: Perm.PURCHASE_READ,
        milestone: 'M5（Week 10–11）',
        delivered: false,
      },
    ],
  },
  {
    key: 'ads',
    path: '/ads',
    label: zhCN.menu.ads,
    icon: <FundOutlined />,
    permission: Perm.ADS_READ,
    milestone: 'M5（Week 10–11）',
    delivered: false,
    children: [
      {
        key: 'ads-overview',
        path: '/ads/overview',
        label: zhCN.menu.adsOverview,
        icon: null,
        permission: Perm.ADS_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AdsOverviewPage,
      },
      {
        key: 'ads-campaigns',
        path: '/ads/campaigns',
        label: zhCN.menu.adsCampaigns,
        icon: null,
        permission: Perm.ADS_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AdsCampaignsPage,
      },
      {
        key: 'ads-keywords',
        path: '/ads/keywords',
        label: zhCN.menu.adsKeywords,
        icon: null,
        permission: Perm.ADS_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AdsKeywordsPage,
      },
      {
        key: 'ads-loss',
        path: '/ads/loss',
        label: zhCN.menu.adsLoss,
        icon: null,
        permission: Perm.ADS_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: AdsLossPage,
      },
    ],
  },
  {
    key: 'cs',
    path: '/cs',
    label: zhCN.menu.cs,
    icon: <CustomerServiceOutlined />,
    permission: Perm.CS_READ,
    milestone: 'M5（Week 10–11）',
    delivered: false,
    children: [
      {
        key: 'cs-messages',
        path: '/cs/messages',
        label: zhCN.menu.csMessages,
        icon: null,
        permission: Perm.CS_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: CsMessagesPage,
      },
      {
        key: 'cs-templates',
        path: '/cs/templates',
        label: zhCN.menu.csTemplates,
        icon: null,
        permission: Perm.CS_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: CsTemplatesPage,
      },
      {
        key: 'cs-tickets',
        path: '/cs/tickets',
        label: zhCN.menu.csTickets,
        icon: null,
        permission: Perm.CS_READ,
        milestone: 'M5（Week 10–11）',
        delivered: true,
        component: CsTicketsPage,
      },
    ],
  },
  {
    key: 'finance',
    path: '/finance',
    label: zhCN.menu.finance,
    icon: <WalletOutlined />,
    permission: Perm.FINANCE_READ,
    milestone: 'M4/M5',
    delivered: false,
    children: [
      {
        key: 'landed-cost',
        path: '/finance/landed-cost',
        label: zhCN.menu.landedCost,
        icon: null,
        permission: Perm.LANDED_COST_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: LandedCostPage,
      },
      {
        key: 'exchange-rates',
        path: '/finance/rates',
        label: zhCN.menu.exchangeRates,
        icon: null,
        permission: Perm.FINANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: ExchangeRatesPage,
      },
      {
        key: 'sku-profit',
        path: '/finance/profit',
        label: zhCN.menu.skuProfit,
        icon: null,
        permission: Perm.FINANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: ProfitPage,
      },
      {
        key: 'profit-waterfall',
        path: '/finance/waterfall',
        label: zhCN.menu.profitWaterfall,
        icon: null,
        permission: Perm.FINANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: WaterfallPage,
      },
      {
        key: 'settlements',
        path: '/finance/settlements',
        label: zhCN.menu.settlements,
        icon: null,
        permission: Perm.FINANCE_READ,
        milestone: 'M4（Week 8–9）',
        delivered: true,
        component: SettlementsPage,
      },
    ],
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
