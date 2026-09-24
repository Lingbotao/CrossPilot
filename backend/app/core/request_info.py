"""请求信息提取（IP / UA）。

``audit_log.ip`` 与 ``login_log.ip`` 是 PostgreSQL 的 ``INET`` 类型 ——
**非法字符串会让整条 INSERT 报错**，进而把审计写入搞失败。
所以这里对所有来源做了规范化，拿不准就返回 None（宁可不记，不可报错）。
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

MAX_UA_LENGTH = 512


def normalize_ip(raw: str | None) -> str | None:
    """把各种形态的 IP 归一化；非法值返回 None。"""
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    # 去掉 IPv6 的 zone id（fe80::1%eth0）
    candidate = candidate.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    # ::ffff:127.0.0.1 → 127.0.0.1（更易读，也便于按 IP 聚合）
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ip)


def client_ip(request: Request) -> str | None:
    """取客户端 IP。

    优先 ``X-Forwarded-For`` 的第一跳。**前提是应用只能经反向代理访问**
    （docker-compose 里 api 不直接暴露到公网）。若将来直连公网，
    必须改成只信任受信代理写入的值，否则 IP 可被伪造。
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return normalize_ip(forwarded.split(",", 1)[0])
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return normalize_ip(real_ip)
    return normalize_ip(request.client.host if request.client else None)


def client_ua(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:MAX_UA_LENGTH]
