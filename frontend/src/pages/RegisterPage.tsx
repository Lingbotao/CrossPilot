import { LockOutlined, MailOutlined } from '@ant-design/icons';
import { Alert, Button, Form, Input, Typography } from 'antd';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import type { RegisterRequest } from '@/api/types';
import { AuthCard } from '@/components/AuthCard';
import zhCN from '@/i18n/zh-CN';
import { checkPassword } from '@/utils/password';

interface RegisterForm extends RegisterRequest {
  confirm_password: string;
}

export function RegisterPage() {
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const submit = async ({ confirm_password: _, ...payload }: RegisterForm) => {
    setSubmitting(true);
    setErrorText(null);
    try {
      await authApi.register(payload);
      navigate(`/verify-email?email=${encodeURIComponent(payload.email)}`, {
        replace: true,
        state: { registered: true },
      });
    } catch (error) {
      setErrorText(error instanceof ApiError ? error.message : zhCN.auth.registerFailed);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthCard>
      <Typography.Title level={4}>{zhCN.auth.register}</Typography.Title>
      {errorText ? <Alert type="error" showIcon message={errorText} style={{ marginBottom: 16 }} /> : null}
      <Form<RegisterForm> layout="vertical" size="large" requiredMark={false} onFinish={submit}>
        <Form.Item
          name="email"
          label={zhCN.auth.email}
          rules={[
            { required: true, message: zhCN.validation.emailRequired },
            { type: 'email', message: zhCN.validation.emailInvalid },
          ]}
        >
          <Input prefix={<MailOutlined />} autoComplete="email" placeholder="you@company.com" />
        </Form.Item>
        <Form.Item
          name="tenant_name"
          label={zhCN.auth.tenantName}
          rules={[{ required: true, message: zhCN.validation.tenantNameRequired }]}
        >
          <Input maxLength={128} />
        </Form.Item>
        <Form.Item
          name="tenant_code"
          label={zhCN.auth.tenantCode}
          extra={zhCN.auth.tenantCodeHelp}
          normalize={(value: string) => value.toLowerCase().trim()}
          rules={[
            { required: true, message: zhCN.validation.tenantCodeRequired },
            { pattern: /^[a-z0-9][a-z0-9-]{2,63}$/, message: zhCN.validation.tenantCodeInvalid },
          ]}
        >
          <Input maxLength={64} placeholder="my-company" />
        </Form.Item>
        <Form.Item name="display_name" label={zhCN.auth.displayName}>
          <Input maxLength={64} />
        </Form.Item>
        <Form.Item
          name="password"
          label={zhCN.auth.password}
          extra={zhCN.auth.passwordRule}
          rules={[
            { required: true, message: zhCN.validation.passwordRequired },
            {
              validator: (_, value: string | undefined) => {
                if (!value) return Promise.resolve();
                const result = checkPassword(value);
                return result.valid
                  ? Promise.resolve()
                  : Promise.reject(new Error(result.errors.join('、')));
              },
            },
          ]}
        >
          <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm_password"
          label={zhCN.auth.confirmPassword}
          dependencies={['password']}
          rules={[
            { required: true, message: zhCN.validation.confirmPasswordRequired },
            ({ getFieldValue }) => ({
              validator: (_, value: string | undefined) =>
                !value || value === getFieldValue('password')
                  ? Promise.resolve()
                  : Promise.reject(new Error(zhCN.validation.passwordMismatch)),
            }),
          ]}
        >
          <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={submitting} block>
          {zhCN.auth.createTenant}
        </Button>
      </Form>
      <Typography.Paragraph style={{ textAlign: 'center', margin: '16px 0 0' }}>
        {zhCN.auth.haveAccount} <Link to="/login">{zhCN.auth.login}</Link>
      </Typography.Paragraph>
    </AuthCard>
  );
}
