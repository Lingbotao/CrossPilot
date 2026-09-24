/**
 * 通用功能占位页。
 *
 * 存在意义：菜单结构按 PRD 完整铺开，但功能按里程碑交付。
 * 点进来能明确看到"这个功能什么时候来、在哪个里程碑"，
 * 而不是一个空白页 —— 空白页在验收时最容易引发"是不是漏做了"的争议。
 */

import { ToolOutlined } from '@ant-design/icons';
import { Card, Result, Tag, Typography } from 'antd';
import { useLocation } from 'react-router-dom';

import { MENU_ITEMS } from '@/router/menu';

export function PlaceholderPage() {
  const location = useLocation();
  const item = MENU_ITEMS.find((menu) => location.pathname.startsWith(menu.path));

  return (
    <Card>
      <Result
        icon={<ToolOutlined style={{ color: '#2f54eb' }} />}
        title={item ? `${item.label} 尚未开放` : '页面尚未开放'}
        subTitle={
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center' }}>
            <span>
              计划交付里程碑：
              <Tag color="blue">{item?.milestone ?? '待排期'}</Tag>
            </span>
            <Typography.Text type="secondary" style={{ fontSize: 12, maxWidth: 520 }}>
              M0 阶段只交付工程脚手架（仓库结构、多租户隔离、鉴权骨架、统一契约、CI 门禁）。
              功能模块按开发规划中的里程碑顺序推进，每个里程碑以 DoD 逐条验收。
            </Typography.Text>
          </div>
        }
      />
    </Card>
  );
}

export default PlaceholderPage;
