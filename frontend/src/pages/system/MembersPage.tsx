import { DeleteOutlined, MailOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useState } from 'react';

import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import { membersApi } from '@/api/members';
import { rolesApi } from '@/api/roles';
import {
  DataScopeType,
  InvitationStatus,
  MemberStatus,
  Perm,
  ResourceType,
  type DataScopeTypeValue,
  type Member,
  type RoleCodeValue,
} from '@/api/types';
import { PermissionGuard } from '@/components/PermissionGuard';
import zhCN from '@/i18n/zh-CN';
import { useAuthStore } from '@/store/auth';
import { formatDateTime } from '@/utils/format';

interface InviteForm {
  email: string;
  role_code: Exclude<RoleCodeValue, 'OWNER'>;
}

interface ScopeForm {
  scope_type: DataScopeTypeValue;
  shop_ids_text?: string;
}

export function MembersPage() {
  const queryClient = useQueryClient();
  const currentUserId = useAuthStore((state) => state.user?.id);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<Member | null>(null);
  const [scopeTarget, setScopeTarget] = useState<Member | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const members = useQuery({ queryKey: ['members'], queryFn: membersApi.list });
  const roles = useQuery({ queryKey: ['roles'], queryFn: rolesApi.list });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['members'] });

  const invite = useMutation({
    mutationFn: membersApi.invite,
    onSuccess: async () => {
      setInviteOpen(false);
      await refresh();
    },
  });
  const updateRole = useMutation({
    mutationFn: ({ memberId, roleCode }: { memberId: string; roleCode: Exclude<RoleCodeValue, 'OWNER'> }) =>
      membersApi.updateRole(memberId, { role_code: roleCode }),
    onSuccess: refresh,
  });
  const revoke = useMutation({
    mutationFn: membersApi.revokeInvitation,
    onSuccess: refresh,
  });
  const updateScope = useMutation({
    mutationFn: ({ memberId, values }: { memberId: string; values: ScopeForm }) =>
      membersApi.updateDataScope(memberId, {
        resource_type: ResourceType.SHOP,
        scope_type: values.scope_type,
        shop_ids:
          values.scope_type === DataScopeType.SELECTED
            ? (values.shop_ids_text ?? '').split(/[\s,]+/).filter(Boolean)
            : [],
      }),
    onSuccess: async () => {
      setScopeTarget(null);
      await refresh();
    },
  });

  const removeMember = async ({ password }: { password: string }) => {
    if (!removeTarget) return;
    setRemoveError(null);
    try {
      const confirmed = await authApi.confirmPassword({ password, action: 'member.remove' });
      await membersApi.remove(removeTarget.id, confirmed.confirmation_token);
      setRemoveTarget(null);
      await refresh();
    } catch (error) {
      setRemoveError(error instanceof ApiError ? error.message : zhCN.members.removeFailed);
    }
  };

  const roleOptions = (roles.data ?? [])
    .filter((role) => role.code !== 'OWNER')
    .map((role) => ({ label: role.name, value: role.code }));

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>{zhCN.members.title}</Typography.Title>
            <Typography.Text type="secondary">{zhCN.members.description}</Typography.Text>
          </div>
          <PermissionGuard permission={Perm.MEMBER_WRITE}>
            <Button type="primary" icon={<MailOutlined />} onClick={() => setInviteOpen(true)}>
              {zhCN.members.invite}
            </Button>
          </PermissionGuard>
        </Space>
      </Card>

      <Card title={zhCN.members.activeMembers}>
        <Table<Member>
          rowKey="id"
          loading={members.isLoading}
          dataSource={members.data?.members ?? []}
          pagination={false}
          columns={[
            {
              title: zhCN.auth.email,
              render: (_, row) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text>{row.display_name || row.email}</Typography.Text>
                  <Typography.Text type="secondary">{row.email}</Typography.Text>
                </Space>
              ),
            },
            {
              title: zhCN.members.role,
              render: (_, row) =>
                row.role_code === 'OWNER' ? (
                  <Tag color="gold">{zhCN.roles.OWNER}</Tag>
                ) : (
                  <PermissionGuard
                    permission={Perm.MEMBER_WRITE}
                    fallback={<Tag>{zhCN.roles[row.role_code]}</Tag>}
                  >
                    <Select
                      value={row.role_code}
                      options={roleOptions}
                      style={{ minWidth: 120 }}
                      loading={updateRole.isPending}
                      onChange={(value: Exclude<RoleCodeValue, 'OWNER'>) =>
                        updateRole.mutate({ memberId: row.id, roleCode: value })
                      }
                    />
                  </PermissionGuard>
                ),
            },
            {
              title: zhCN.members.status,
              render: (_, row) => (
                <Tag color={row.status === MemberStatus.ACTIVE ? 'green' : 'default'}>
                  {row.status === MemberStatus.ACTIVE ? zhCN.members.active : zhCN.members.disabled}
                </Tag>
              ),
            },
            { title: zhCN.members.joinedAt, render: (_, row) => formatDateTime(row.joined_at) },
            {
              title: zhCN.common.actions,
              render: (_, row) => (
                <PermissionGuard permission={Perm.MEMBER_WRITE}>
                  <Space>
                    <Button icon={<SafetyCertificateOutlined />} onClick={() => setScopeTarget(row)}>
                      {zhCN.members.dataScope}
                    </Button>
                    <Button
                      danger
                      icon={<DeleteOutlined />}
                      disabled={row.role_code === 'OWNER' || row.user_id === currentUserId}
                      onClick={() => setRemoveTarget(row)}
                    >
                      {zhCN.members.remove}
                    </Button>
                  </Space>
                </PermissionGuard>
              ),
            },
          ]}
        />
      </Card>

      <Card title={zhCN.members.pendingInvitations}>
        <Table
          rowKey="id"
          dataSource={(members.data?.invitations ?? []).filter(
            (item) => item.status === InvitationStatus.PENDING,
          )}
          pagination={false}
          columns={[
            { title: zhCN.auth.email, dataIndex: 'email' },
            { title: zhCN.members.role, render: (_, row) => zhCN.roles[row.role_code] },
            { title: zhCN.members.expiresAt, render: (_, row) => formatDateTime(row.expires_at) },
            {
              title: zhCN.common.actions,
              render: (_, row) => (
                <PermissionGuard permission={Perm.MEMBER_WRITE}>
                  <Button danger loading={revoke.isPending} onClick={() => revoke.mutate(row.id)}>
                    {zhCN.members.revoke}
                  </Button>
                </PermissionGuard>
              ),
            },
          ]}
        />
      </Card>

      <Modal title={zhCN.members.invite} open={inviteOpen} footer={null} onCancel={() => setInviteOpen(false)}>
        <Form<InviteForm> layout="vertical" onFinish={(values) => invite.mutate(values)}>
          <Form.Item name="email" label={zhCN.auth.email} rules={[{ required: true }, { type: 'email' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role_code" label={zhCN.members.role} rules={[{ required: true }]}>
            <Select options={roleOptions} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={invite.isPending} block>{zhCN.members.sendInvite}</Button>
        </Form>
      </Modal>

      <Modal
        title={zhCN.members.removeConfirmTitle}
        open={Boolean(removeTarget)}
        footer={null}
        destroyOnClose
        onCancel={() => setRemoveTarget(null)}
      >
        <Alert type="warning" showIcon message={zhCN.members.removeConfirmHint} style={{ marginBottom: 16 }} />
        {removeError ? <Alert type="error" showIcon message={removeError} style={{ marginBottom: 16 }} /> : null}
        <Form layout="vertical" onFinish={removeMember}>
          <Form.Item name="password" label={zhCN.auth.password} rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button danger type="primary" htmlType="submit" block>{zhCN.members.confirmRemove}</Button>
        </Form>
      </Modal>

      <Modal
        title={zhCN.members.dataScope}
        open={Boolean(scopeTarget)}
        footer={null}
        destroyOnClose
        onCancel={() => setScopeTarget(null)}
      >
        <Form<ScopeForm>
          layout="vertical"
          initialValues={{ scope_type: DataScopeType.ALL }}
          onFinish={(values) => scopeTarget && updateScope.mutate({ memberId: scopeTarget.id, values })}
        >
          <Form.Item name="scope_type" label={zhCN.members.shopScope} rules={[{ required: true }]}>
            <Select
              options={[
                { value: DataScopeType.ALL, label: zhCN.members.scopeAll },
                { value: DataScopeType.SELECTED, label: zhCN.members.scopeSelected },
                { value: DataScopeType.NONE, label: zhCN.members.scopeNone },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) =>
              getFieldValue('scope_type') === DataScopeType.SELECTED ? (
                <Form.Item
                  name="shop_ids_text"
                  label={zhCN.members.shopIds}
                  extra={zhCN.members.shopIdsHelp}
                  rules={[{ required: true }]}
                >
                  <Input.TextArea rows={3} />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={updateScope.isPending} block>{zhCN.common.save}</Button>
        </Form>
      </Modal>
    </Space>
  );
}
