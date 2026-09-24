/** 主框架：左侧菜单 + 顶部栏 + 内容区。 */

import { LogoutOutlined, ShopOutlined, UserOutlined } from '@ant-design/icons';
import { Avatar, Dropdown, Layout, Menu, Tag, Typography } from 'antd';
import { useMemo } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { usePermission } from '@/hooks/usePermission';
import { MENU_ITEMS } from '@/router/menu';
import { useAuthStore } from '@/store/auth';

const { Header, Sider, Content } = Layout;

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { can, roleName } = usePermission();

  const user = useAuthStore((state) => state.user);
  const tenant = useAuthStore((state) => state.tenant);
  const logout = useAuthStore((state) => state.logout);

  // 未授权的菜单直接不渲染 —— 灰掉会让用户反复来问"为什么我点不了"
  const visibleItems = useMemo(() => MENU_ITEMS.filter((item) => can(item.permission)), [can]);

  const selectedKey = visibleItems.find((item) => location.pathname.startsWith(item.path))?.key ?? 'dashboard';

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
          style={{ borderInlineEnd: 'none' }}
          items={visibleItems.map((item) => ({
            key: item.key,
            icon: item.icon,
            label: item.label,
            onClick: () => navigate(item.path),
          }))}
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
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

export default AppLayout;
