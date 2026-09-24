import { useMutation } from '@tanstack/react-query';
import { Alert, Button, Form, Input, Result, Typography } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { membersApi } from '@/api/members';
import type { AcceptInvitationRequest } from '@/api/types';
import { AuthCard } from '@/components/AuthCard';
import zhCN from '@/i18n/zh-CN';
import { checkPassword } from '@/utils/password';

interface AcceptForm {
  display_name?: string;
  password: string;
  confirm_password: string;
}

export function AcceptInvitationPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') ?? '';
  const accept = useMutation({
    mutationFn: (payload: AcceptInvitationRequest) => membersApi.acceptInvitation(payload),
  });

  if (accept.isSuccess) {
    return (
      <AuthCard>
        <Result
          status="success"
          title={zhCN.members.acceptSuccess}
          extra={<Button type="primary"><Link to="/login">{zhCN.auth.goLogin}</Link></Button>}
        />
      </AuthCard>
    );
  }

  const submit = ({ confirm_password: _, ...values }: AcceptForm) => {
    accept.mutate({ token, ...values });
  };

  return (
    <AuthCard>
      <Typography.Title level={4}>{zhCN.members.acceptInvitation}</Typography.Title>
      {!token ? <Alert type="error" showIcon message={zhCN.members.invalidInvitationLink} /> : null}
      {accept.isError ? (
        <Alert
          type="error"
          showIcon
          message={accept.error instanceof ApiError ? accept.error.message : zhCN.members.acceptFailed}
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <Form<AcceptForm> layout="vertical" size="large" requiredMark={false} onFinish={submit}>
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
          <Input.Password autoComplete="new-password" />
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
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={accept.isPending} disabled={!token} block>
          {zhCN.members.joinTenant}
        </Button>
      </Form>
    </AuthCard>
  );
}
