import { useQuery } from '@tanstack/react-query';
import { Card, Collapse, Descriptions, Space, Spin, Tag, Typography } from 'antd';

import { rolesApi } from '@/api/roles';
import zhCN from '@/i18n/zh-CN';

export function RolesPage() {
  const roles = useQuery({ queryKey: ['roles'], queryFn: rolesApi.list });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Typography.Title level={4} style={{ margin: 0 }}>{zhCN.rolePage.title}</Typography.Title>
        <Typography.Text type="secondary">{zhCN.rolePage.description}</Typography.Text>
      </Card>
      <Card>
        {roles.isLoading ? (
          <Spin />
        ) : (
          <Collapse
            items={(roles.data ?? []).map((role) => ({
              key: role.code,
              label: (
                <Space>
                  <Typography.Text strong>{role.name}</Typography.Text>
                  <Tag>{role.code}</Tag>
                  {role.is_system ? <Tag color="blue">{zhCN.rolePage.builtIn}</Tag> : null}
                </Space>
              ),
              children: (
                <Descriptions column={1} size="small">
                  <Descriptions.Item label={zhCN.rolePage.descriptionLabel}>
                    {role.description || '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label={zhCN.rolePage.permissions}>
                    <Space wrap>
                      {role.permission_codes.map((permission) => <Tag key={permission}>{permission}</Tag>)}
                    </Space>
                  </Descriptions.Item>
                </Descriptions>
              ),
            }))}
          />
        )}
      </Card>
    </Space>
  );
}
