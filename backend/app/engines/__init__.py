"""★ 核心引擎层 —— 三个 P0 不可砍的差异化能力（PRD 15 章标 ★）。

| 引擎 | 文件（M3/M4 落地） | 为什么不可砍 |
|---|---|---|
| Landed Cost（落地成本） | ``landed_cost.py`` | 产品核心卖点：算清"卖一单到底赚多少" |
| Profit（SKU 级利润） | ``profit.py`` | 与账单核对偏差必须 ≤2%（验收项 9） |
| Compliance（合规前置校验） | ``compliance.py`` | 目的国禁售/认证/HS 编码前置拦截 |

**本层是全项目唯一不允许调用外部 API 的地方** —— 引擎必须是纯函数式的
可单测逻辑（输入参数 + 配置数据 → 输出结果），否则黄金案例集无法做回归。

典型签名（M4 落地时按此实现）::

    def compute_landed_cost(inputs: LandedCostInput, rules: TaxRuleSet) -> LandedCostResult: ...

铁律：
- **金额一律 ``Decimal``**，禁止 ``float``（约束 C4）—— 精度损失不可逆；
- **税率/费率一律来自配置表并带版本**（约束 C5）—— 硬编码后每次变更都要发版；
- 每次计算必须落 ``landed_cost_calc`` 记录（params + result），支持历史回溯重算。
"""

__all__: list[str] = []
