"""CrossPilot 后端应用包。

分层约定（禁止跨层反向依赖）：
    api  →  services  →  repositories  →  models
                    ↘  engines / adapters

- ``api``          : 只做参数校验与编排，不写业务逻辑
- ``services``     : 纯业务逻辑，可单测，不感知 HTTP
- ``repositories`` : 唯一允许直接触达数据库的层，强制带 tenant_id
- ``adapters``     : 平台差异全部封在这里，业务层零改动（约束 C2）
- ``engines``      : Landed Cost / 利润 / 合规三大核心引擎（P0 不可砍）
"""

__version__ = "0.1.0"
