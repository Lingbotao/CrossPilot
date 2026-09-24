import { ShopOutlined } from '@ant-design/icons';
import { Card, Space, Typography } from 'antd';
import type { ReactNode } from 'react';

import zhCN from '@/i18n/zh-CN';

export function AuthCard({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #f0f5ff 0%, #f5f6f8 100%)',
        padding: 20,
      }}
    >
      <Card style={{ width: 460 }} styles={{ body: { padding: 32 } }}>
        <Space direction="vertical" size={4} style={{ marginBottom: 24 }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            <ShopOutlined style={{ color: '#2f54eb', marginRight: 8 }} />
            {zhCN.app.name}
          </Typography.Title>
          <Typography.Text type="secondary">{zhCN.app.slogan}</Typography.Text>
        </Space>
        {children}
      </Card>
    </div>
  );
}
