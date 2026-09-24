"""★ 权限矩阵测试（PRD 4.3）。

这里用测试把**产品硬规则**钉死 —— 以后有人"顺手"给 OPS_STAFF 加个成本权限，
CI 会直接红掉。
"""

from __future__ import annotations

import pytest

from app.core.permissions import (
    COST_VISIBLE_ROLES,
    ROLE_NAMES_ZH,
    ROLE_PERMISSIONS,
    SYSTEM_ROLE_CODES,
    Perm,
    RoleCode,
    permission_codes_for_role,
    permissions_for_role,
    role_can_view_cost,
    role_has_permission,
)


class TestMatrixIntegrity:
    def test_every_role_has_permissions(self) -> None:
        for role in RoleCode:
            assert role in ROLE_PERMISSIONS, f"{role} 未在权限矩阵中定义"
            assert ROLE_PERMISSIONS[role], f"{role} 权限为空 —— 空权限会导致登录后什么都看不到"

    def test_every_role_has_chinese_name(self) -> None:
        for role in RoleCode:
            assert ROLE_NAMES_ZH.get(role), f"{role} 缺少中文名"

    def test_all_permissions_are_declared_enum_members(self) -> None:
        declared = {p.value for p in Perm}
        for role, perms in ROLE_PERMISSIONS.items():
            unknown = {p for p in perms if p.value not in declared}
            assert not unknown, f"{role} 引用了未登记的权限点：{unknown}"

    def test_owner_and_admin_have_full_permissions(self) -> None:
        every = set(Perm)
        assert ROLE_PERMISSIONS[RoleCode.OWNER] == every
        assert ROLE_PERMISSIONS[RoleCode.ADMIN] == every

    def test_system_role_codes_match_enum(self) -> None:
        assert {r.value for r in RoleCode} == SYSTEM_ROLE_CODES


class TestCostVisibilityRule:
    """★ F4 硬规则：采购成本价与利润默认对 OPS_STAFF 不可见。"""

    def test_ops_staff_cannot_read_cost(self) -> None:
        assert not role_has_permission(RoleCode.OPS_STAFF, Perm.COST_READ.value)
        assert not role_can_view_cost(RoleCode.OPS_STAFF)

    def test_ops_staff_cannot_read_finance(self) -> None:
        assert not role_has_permission(RoleCode.OPS_STAFF, Perm.FINANCE_READ.value)

    def test_cs_cannot_read_cost(self) -> None:
        assert not role_can_view_cost(RoleCode.CS)

    def test_viewer_cannot_read_cost(self) -> None:
        assert not role_can_view_cost(RoleCode.VIEWER)

    @pytest.mark.parametrize(
        "role",
        [RoleCode.OWNER, RoleCode.ADMIN, RoleCode.OPS_MANAGER, RoleCode.PURCHASER, RoleCode.FINANCE],
    )
    def test_cost_visible_roles_can_read_cost(self, role: RoleCode) -> None:
        assert role_can_view_cost(role)
        assert role_has_permission(role, Perm.COST_READ.value)

    def test_cost_visible_roles_constant_matches_matrix(self) -> None:
        """常量与矩阵不能各说各话 —— 这是两处逻辑最容易漂移的地方。"""
        derived = {r for r, perms in ROLE_PERMISSIONS.items() if Perm.COST_READ in perms}
        assert derived == set(COST_VISIBLE_ROLES)


class TestRoleBehaviors:
    def test_viewer_is_read_only(self) -> None:
        write_perms = {
            Perm.TENANT_WRITE,
            Perm.MEMBER_WRITE,
            Perm.PRODUCT_WRITE,
            Perm.ORDER_SHIP,
            Perm.INVENTORY_WRITE,
            Perm.PURCHASE_WRITE,
            Perm.COMPLIANCE_WRITE,
        }
        assert not (ROLE_PERMISSIONS[RoleCode.VIEWER] & write_perms)

    def test_purchaser_has_no_order_access(self) -> None:
        assert not role_has_permission(RoleCode.PURCHASER, Perm.ORDER_READ.value)

    def test_cs_can_ship_orders(self) -> None:
        assert role_has_permission(RoleCode.CS, Perm.ORDER_SHIP.value)

    def test_unknown_role_gets_no_permissions(self) -> None:
        """fail-closed：角色码不认识时给空权限，而不是给全权限。"""
        assert permissions_for_role("NOT_A_ROLE") == frozenset()
        assert role_has_permission("NOT_A_ROLE", Perm.ORDER_READ.value) is False
        assert role_can_view_cost("NOT_A_ROLE") is False

    def test_permission_codes_helper_is_sorted_and_stringified(self) -> None:
        codes = permission_codes_for_role(RoleCode.VIEWER)
        assert codes == sorted(codes)
        assert all(isinstance(c, str) for c in codes)
        assert Perm.SHOP_READ.value in codes
