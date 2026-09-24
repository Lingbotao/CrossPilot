import { useMutation, useQuery } from '@tanstack/react-query';
import { Alert, Button, Form, Input, Result, Typography } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';

import { authApi } from '@/api/auth';
import { ApiError } from '@/api/client';
import { AuthCard } from '@/components/AuthCard';
import zhCN from '@/i18n/zh-CN';

export function VerifyEmailPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') ?? '';
  const initialEmail = searchParams.get('email') ?? '';

  const verification = useQuery({
    queryKey: ['verify-email', token],
    queryFn: () => authApi.verifyEmail({ token }),
    enabled: token.length > 0,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });

  const resend = useMutation({
    mutationFn: authApi.resendVerification,
  });

  if (token && verification.isSuccess) {
    return (
      <AuthCard>
        <Result
          status="success"
          title={zhCN.auth.verifySuccess}
          subTitle={zhCN.auth.verifySuccessHint}
          extra={<Button type="primary"><Link to="/login">{zhCN.auth.goLogin}</Link></Button>}
        />
      </AuthCard>
    );
  }

  return (
    <AuthCard>
      <Typography.Title level={4}>{zhCN.auth.verifyEmail}</Typography.Title>
      <Typography.Paragraph type="secondary">
        {token ? zhCN.auth.verifying : zhCN.auth.verifyHint}
      </Typography.Paragraph>
      {verification.isLoading ? <Alert type="info" showIcon message={zhCN.auth.verifying} /> : null}
      {verification.isError ? (
        <Alert
          type="error"
          showIcon
          message={verification.error instanceof ApiError ? verification.error.message : zhCN.auth.verifyFailed}
          style={{ marginBottom: 16 }}
        />
      ) : null}
      {resend.isSuccess ? (
        <Alert type="success" showIcon message={zhCN.auth.resendSuccess} style={{ marginBottom: 16 }} />
      ) : null}
      <Form
        layout="vertical"
        size="large"
        initialValues={{ email: initialEmail }}
        onFinish={(values: { email: string }) => resend.mutate(values)}
      >
        <Form.Item
          name="email"
          label={zhCN.auth.email}
          rules={[
            { required: true, message: zhCN.validation.emailRequired },
            { type: 'email', message: zhCN.validation.emailInvalid },
          ]}
        >
          <Input autoComplete="email" />
        </Form.Item>
        <Button htmlType="submit" loading={resend.isPending} block>
          {zhCN.auth.resendVerification}
        </Button>
      </Form>
      <Typography.Paragraph style={{ textAlign: 'center', margin: '16px 0 0' }}>
        <Link to="/login">{zhCN.auth.backLogin}</Link>
      </Typography.Paragraph>
    </AuthCard>
  );
}
