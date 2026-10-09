/** 主框架：左侧菜单 + 顶部栏 + 内容区。 */

import { LogoutOutlined, ShopOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Avatar, Dropdown, Layout, Menu, Tag, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { usePermission } from '@/hooks/usePermission';
import zhCN from '@/i18n/zh-CN';
import { MENU_ITEMS, type MenuItemConfig } from '@/router/menu';
import { menuLeaves } from '@/router/menuLeaves';
import { useAuthStore } from '@/store/auth';

const { Header, Sider, Content } = Layout;

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { can, roleName } = usePermission();

  const user = useAuthStore((state) => state.user);
  const tenant = useAuthStore((state) => state.tenant);
  const logout = useAuthStore((state) => state.logout);

  const siderItems = useMemo(() => buildSiderItems(MENU_ITEMS, can, navigate), [can, navigate]);
  const selectedKey = useMemo(() => {
    const match = menuLeaves()
      .filter((item) => can(item.permission))
      .sort((left, right) => right.path.length - left.path.length)
      .find((item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`));
    return match?.key ?? 'dashboard';
  }, [can, location.pathname]);
  const [openKeys, setOpenKeys] = useState<string[]>(location.pathname.startsWith('/orders') ? ['orders'] : []);

  useEffect(() => {
    if (location.pathname.startsWith('/orders')) {
      setOpenKeys((keys) => (keys.includes('orders') ? keys : [...keys, 'orders']));
    }
  }, [location.pathname]);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={216} theme="light" style={{ borderRight: '1px solid #f0f0f0' }}>
        <div
          style={{
            height: 56,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '0 16px',
            fontWeight: 600,
            fontSize: 16,
            color: '#2f54eb',
          }}
        >
          <ShopOutlined />
          CrossPilot
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          openKeys={openKeys}
          onOpenChange={setOpenKeys}
          style={{ borderInlineEnd: 'none' }}
          items={siderItems}
        />
      </Sider>

      <Layout>
        <Header
          style={{
            background: '#fff',
            borderBottom: '1px solid #f0f0f0',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Typography.Text strong>{tenant?.name ?? '—'}</Typography.Text>
            {roleName ? <Tag color="blue">{roleName}</Tag> : null}
            {/* 让使用者随时能确认"我现在是以什么身份在看数据" */}
            {tenant?.can_view_cost ? (
              <Tag color="red">成本可见</Tag>
            ) : (
              <Tag color="default">成本不可见</Tag>
            )}
          </div>

          <Dropdown
            menu={{
              items: [
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: () => {
                    void logout().then(() => navigate('/login', { replace: true }));
                  },
                },
              ],
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <Avatar size={28} icon={<UserOutlined />} />
              <Typography.Text>{user?.display_name ?? user?.email ?? '未登录'}</Typography.Text>
            </div>
          </Dropdown>
        </Header>

        <Content style={{ padding: 20, background: '#f5f6f8' }}>
          {user && !user.email_verified_at ? (
            <Alert
              type="warning"
              showIcon
              message={zhCN.auth.emailNotVerified}
              style={{ marginBottom: 16 }}
            />
          ) : null}
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

function buildSiderItems(
  items: readonly MenuItemConfig[],
  can: (permission: MenuItemConfig['permission']) => boolean,
  navigate: (path: string) => void,
): MenuProps['items'] {
  const built: NonNullable<MenuProps['items']> = [];
  for (const item of items) {
    if (item.hideInMenu) {
      continue;
    }
    if (item.children && item.children.length > 0) {
      const children = buildSiderItems(item.children, can, navigate);
      if (!children || children.length === 0) {
        continue;
      }
      built.push({ key: item.key, icon: item.icon, label: item.label, children });
      continue;
    }
    if (!can(item.permission)) {
      continue;
    }
    built.push({
      key: item.key,
      icon: item.icon,
      label: item.label,
      onClick: () => navigate(item.path),
    });
  }
  return built;
}

export default AppLayout;
