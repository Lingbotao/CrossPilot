/**
 * M0 脚手架自检页。
 *
 * 这一页不是"占位页"，而是 **M0 交付验收的可演示界面**：
 * 它把 M0 的几条 DoD 直接摆出来，登录后一眼就能确认脚手架是否真的通了。
 *
 * | DoD | 本页对应位置 |
 * |---|---|
 * | ④ 前端跑通登录占位流程 | 顶部身份卡片（真实来自 `/auth/me`） |
 * | 多租户上下文与权限点下发 | 权限点列表 |
 * | ★ F4 成本不可见硬规则 | 「成本可见性」卡片（用两个演示账号对比） |
 * | 统一响应信封 + trace_id | 环境信息卡片 |
 * | C4 全链 Decimal | 金额精度演示卡片 |
 */

import { ApiOutlined, CheckCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { Alert, Card, Col, Descriptions, Row, Space, Tag, Typography, Divider } from 'antd';

import { BASE_URL } from '@/api/client';
import { CostGuard } from '@/components/PermissionGuard';
import { MoneyText } from '@/components/MoneyText';
import { usePermission } from '@/hooks/usePermission';
import { useAuthStore } from '@/store/auth';

/** 故意选一个超过 JS 安全整数范围的大额，用来证明前端没有经过 Number */
const BIG_AMOUNT = '12345678901234.567890';
const SMALL_COST = '0.100000';

export function DashboardPage() {
  const user = useAuthStore((state) => state.user);
  const tenant = useAuthStore((state) => state.tenant);
  const permissions = useAuthStore((state) => state.permissionSet);
  const { canViewCost, roleCode, roleName } = usePermission();

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="success"
        showIcon
        icon={<CheckCircleOutlined />}
        message="M0 脚手架已就绪"
        description="本页用于验收开发脚手架（M0）：登录链路、租户上下文、权限下发、金额精度、统一响应信封均已打通。业务功能自 M1 起逐个交付。"
      />

      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="当前身份" size="small">
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="用户">{user?.display_name ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="邮箱">{user?.email ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="租户">
                {tenant?.name} <Tag>{tenant?.code}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="角色">
                {roleName} <Tag color="blue">{roleCode}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="权限点数量">{permissions.size}</Descriptions.Item>
              <Descriptions.Item label="上次登录">{user?.last_login_at ?? '—'}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card
            title="★ F4 成本与利润可见性（产品硬规则）"
            size="small"
            extra={
              canViewCost ? <Tag color="red">当前角色可见</Tag> : <Tag color="default">当前角色不可见</Tag>
            }
          >
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              PRD 4.3 关键设计决策：<b>采购成本价与利润默认对 OPS_STAFF 不可见</b>。
              一线运营看到成本价，容易在议价或离职后带来风险。
            </Typography.Paragraph>

            <Descriptions column={1} size="small">
              <Descriptions.Item label="采购成本价">
                {/* 未授权时这里渲染出的是"—"，而不是把数字画出来再灰掉 */}
                <CostGuard fallback={<Typography.Text type="secondary">无权限查看</Typography.Text>}>
                  <MoneyText value="86.500000" decimals={4} strong />
                </CostGuard>
              </Descriptions.Item>
              <Descriptions.Item label="SKU 毛利">
                <CostGuard fallback={<Typography.Text type="secondary">无权限查看</Typography.Text>}>
                  <MoneyText value="12.340000" colorize strong />
                </CostGuard>
              </Descriptions.Item>
            </Descriptions>

            <Divider style={{ margin: '12px 0' }} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              验证方式：退出后用 <Typography.Text code>ops@example.com</Typography.Text> 登录，
              本卡片两项会变为「无权限查看」，顶部标签也会变成「成本不可见」。
            </Typography.Text>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="C4 金额精度（全链 Decimal）" size="small">
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              后端金额是 <Typography.Text code>NUMERIC(20,6)</Typography.Text> 并以字符串传输。
              下面第一个金额已超出 JS 安全整数范围（2^53-1 ≈ 9.007e15），
              若前端走过 <Typography.Text code>Number</Typography.Text>，尾数会被抹掉。
            </Typography.Paragraph>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="大额订单（原始值）">
                <Typography.Text code>{BIG_AMOUNT}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="格式化展示">
                <MoneyText value={BIG_AMOUNT} strong />
              </Descriptions.Item>
              <Descriptions.Item label="小额成本（4 位小数）">
                <MoneyText value={SMALL_COST} decimals={4} currency="USD" showCode />
              </Descriptions.Item>
              <Descriptions.Item label="带汇率提示">
                <MoneyText
                  value="100.000000"
                  currency="USD"
                  rate={{ from: 'USD', to: 'CNY', rate: '7.123400', source: '平台结算单', effective_date: '2026-09-01' }}
                />
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="环境与接口" size="small">
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="API 前缀">
                <Typography.Text code>{BASE_URL}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="租户默认币种">{tenant?.default_currency ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="租户时区">{tenant?.timezone ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="数据范围">
                {Object.keys(tenant?.data_scope ?? {}).length > 0
                  ? JSON.stringify(tenant?.data_scope)
                  : '未配置（默认全部）'}
              </Descriptions.Item>
            </Descriptions>
            <Divider style={{ margin: '12px 0' }} />
            <Space size={6} align="start">
              <InfoCircleOutlined style={{ color: '#8c8c8c', marginTop: 3 }} />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                每个响应都带 <Typography.Text code>trace_id</Typography.Text>（同时出现在响应头
                <Typography.Text code>X-Trace-Id</Typography.Text>）。接口报错时把追踪号给后端，
                可以直接检索到该请求的全链路日志。
              </Typography.Text>
            </Space>
          </Card>
        </Col>

        <Col span={24}>
          <Card
            title={
              <Space>
                <ApiOutlined />
                当前角色权限点（由 <Typography.Text code>/tenants/current</Typography.Text> 下发）
              </Space>
            }
            size="small"
          >
            <Space size={[6, 6]} wrap>
              {[...permissions].sort().map((permission) => (
                <Tag key={permission} color={permission.startsWith('cost') ? 'red' : undefined}>
                  {permission}
                </Tag>
              ))}
              {permissions.size === 0 ? <Typography.Text type="secondary">无权限点</Typography.Text> : null}
            </Space>
          </Card>
        </Col>
      </Row>
    </Space>
  );
}

export default DashboardPage;
