/**
 * 登录页（M0 占位流程，M1 会补齐注册/邮箱验证/邀请接受）。
 *
 * 这个页面在 M0 的作用是**让 DoD 第 ④ 条可被演示**：
 * "前端能跑通登录占位流程" —— 所以它是真实可用、真实调接口的，不是静态壳子。
 */

import { LockOutlined, MailOutlined, ShopOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { ErrorCode, type LoginRequest } from '@/api/types';
import zhCN from '@/i18n/zh-CN';
import { useAuthStore } from '@/store/auth';

interface LoginFormValues extends LoginRequest {
  tenant_code?: string;
}

const DEMO_ACCOUNTS = [
  { label: '所有者（可看成本）', email: 'owner@example.com', role: 'OWNER' },
  { label: '运营专员（成本不可见）', email: 'ops@example.com', role: 'OPS_STAFF' },
];

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const login = useAuthStore((state) => state.login);

  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [needTenantCode, setNeedTenantCode] = useState(false);
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);

  const redirectTo = (location.state as { from?: string } | null)?.from ?? '/';

  const handleSubmit = async (values: LoginFormValues) => {
    setSubmitting(true);
    setErrorText(null);
    setUnverifiedEmail(null);
    try {
      await login(values);
      navigate(redirectTo, { replace: true });
    } catch (error) {
      if (error instanceof ApiError) {
        setErrorText(error.message);
        // 后端在"用户属于多个租户但没指定"时会返回这条：
        // 与其猜，不如直接把租户输入框展开，让用户明确选择。
        if (error.message.includes('tenant_code')) {
          setNeedTenantCode(true);
        }
        if (error.code === ErrorCode.EMAIL_NOT_VERIFIED) {
          setUnverifiedEmail(values.email);
        }
      } else {
        setErrorText('登录失败，请稍后重试');
      }
    } finally {
      setSubmitting(false);
    }
  };

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
      <Card style={{ width: 420 }} styles={{ body: { padding: 32 } }}>
        <Space direction="vertical" size={4} style={{ marginBottom: 24 }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            <ShopOutlined style={{ color: '#2f54eb', marginRight: 8 }} />
            CrossPilot
          </Typography.Title>
          <Typography.Text type="secondary">跨境电商多平台运营中台</Typography.Text>
        </Space>

        {errorText ? (
          <Alert type="error" showIcon message={errorText} style={{ marginBottom: 16 }} closable onClose={() => setErrorText(null)} />
        ) : null}
        {unverifiedEmail ? (
          <Alert
            type="warning"
            showIcon
            message={
              <Link to={`/verify-email?email=${encodeURIComponent(unverifiedEmail)}`}>
                {zhCN.auth.resendVerification}
              </Link>
            }
            style={{ marginBottom: 16 }}
          />
        ) : null}

        <Form<LoginFormValues> layout="vertical" onFinish={handleSubmit} requiredMark={false} size="large">
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ required: true, message: '请输入邮箱' }, { type: 'email', message: '邮箱格式不正确' }]}
          >
            <Input prefix={<MailOutlined />} placeholder="you@company.com" autoComplete="username" />
          </Form.Item>

          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" autoComplete="current-password" />
          </Form.Item>

          {needTenantCode ? (
            <Form.Item
              name="tenant_code"
              label="租户标识"
              rules={[{ required: true, message: '该账号属于多个租户，请填写租户标识' }]}
              extra="例如：demo"
            >
              <Input placeholder="demo" />
            </Form.Item>
          ) : null}

          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              登录
            </Button>
          </Form.Item>
        </Form>

        <Typography.Paragraph style={{ textAlign: 'center', marginBottom: 16 }}>
          {zhCN.auth.noAccount} <Link to="/register">{zhCN.auth.register}</Link>
        </Typography.Paragraph>

        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          开发环境演示账号（密码 Demo@CrossPilot2026）：
        </Typography.Text>
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {DEMO_ACCOUNTS.map((account) => (
            <Typography.Text key={account.email} style={{ fontSize: 12 }} code>
              {account.label}：{account.email}
            </Typography.Text>
          ))}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            用两个账号分别登录，可在看板页看到「成本可见性」（F4 硬规则）的差异 ——
            这正是{' '}
            <Typography.Text code style={{ fontSize: 12 }}>
              {ErrorCode.PERMISSION_DENIED}
            </Typography.Text>{' '}
            权限校验的现场验证。
          </Typography.Text>
        </div>
      </Card>
    </div>
  );
}

export default LoginPage;
