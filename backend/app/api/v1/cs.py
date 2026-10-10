"""客服接口（F11-01~06）。只读同步消息，回复跳到平台后台。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.errors import ParamInvalidError
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.cs import (
    CsAssigneeView,
    CsMessageView,
    CsShopView,
    CsSyncRequest,
    CsSyncView,
    CsTemplatePatch,
    CsTemplatePreview,
    CsTemplateView,
    CsTemplateWrite,
    CsTicketAssign,
    CsTicketClose,
    CsTicketCreate,
    CsTicketNoteWrite,
    CsTicketView,
)
from app.schemas.listing import parse_id
from app.services.cs import CsService

router = APIRouter(prefix="/cs", tags=["客服"])

Reader = Annotated[Identity, Depends(require_permission(Perm.CS_READ))]
Writer = Annotated[Identity, Depends(require_permission(Perm.CS_WRITE))]


def _path_id(value: str) -> int:
    try:
        return parse_id(value)
    except ValueError as exc:
        raise ParamInvalidError("ID 不合法") from exc


@router.get("/shops", response_model=ApiResponse[list[CsShopView]], summary="可同步的店铺")
async def cs_shops(identity: Reader, session: DbSession) -> ApiResponse[list[CsShopView]]:
    del identity
    return ok(await CsService(session).shops())


@router.get("/assignees", response_model=ApiResponse[list[CsAssigneeView]], summary="可分派的成员")
async def cs_assignees(identity: Reader, session: DbSession) -> ApiResponse[list[CsAssigneeView]]:
    del identity
    return ok(await CsService(session).assignees())


@router.post("/messages/sync", response_model=ApiResponse[CsSyncView], summary="拉取店铺消息")
async def sync_messages(
    payload: CsSyncRequest,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsSyncView]:
    data = await CsService(session).sync(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
    )
    return ok(data)


@router.get("/messages", response_model=ApiResponse[PageData[CsMessageView]], summary="消息列表")
async def list_messages(
    identity: Reader,
    session: DbSession,
    shop_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    sla: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApiResponse[PageData[CsMessageView]]:
    del identity
    data = await CsService(session).messages(
        shop_id=shop_id,
        status=status,
        sla=sla,
        cursor=cursor,
        limit=limit,
    )
    return ok(data)


@router.get("/messages/{message_id}", response_model=ApiResponse[CsMessageView], summary="消息与订单")
async def get_message(
    message_id: str,
    identity: Reader,
    session: DbSession,
) -> ApiResponse[CsMessageView]:
    del identity
    return ok(await CsService(session).message(_path_id(message_id)))


@router.post("/messages/{message_id}/read", response_model=ApiResponse[CsMessageView], summary="标为已读")
async def read_message(
    message_id: str,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsMessageView]:
    del idempotency_key
    return ok(await CsService(session).mark_read(_path_id(message_id), identity.user.id))


@router.get("/templates", response_model=ApiResponse[list[CsTemplateView]], summary="回复模板")
async def list_templates(identity: Reader, session: DbSession) -> ApiResponse[list[CsTemplateView]]:
    del identity
    return ok(await CsService(session).templates())


@router.post("/templates", response_model=ApiResponse[CsTemplateView], summary="新建回复模板")
async def create_template(
    payload: CsTemplateWrite,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsTemplateView]:
    del idempotency_key
    return ok(await CsService(session).create_template(payload, identity.user.id))


@router.patch("/templates/{template_id}", response_model=ApiResponse[CsTemplateView], summary="修改回复模板")
async def update_template(
    template_id: str,
    payload: CsTemplatePatch,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsTemplateView]:
    del idempotency_key
    return ok(await CsService(session).update_template(_path_id(template_id), payload, identity.user.id))


@router.delete("/templates/{template_id}", response_model=ApiResponse[CsTemplateView], summary="停用回复模板")
async def delete_template(
    template_id: str,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsTemplateView]:
    del idempotency_key
    return ok(await CsService(session).delete_template(_path_id(template_id), identity.user.id))


@router.get("/templates/{template_id}/preview", response_model=ApiResponse[CsTemplatePreview], summary="预览模板")
async def preview_template(
    template_id: str,
    identity: Reader,
    session: DbSession,
    message_id: Annotated[str | None, Query()] = None,
) -> ApiResponse[CsTemplatePreview]:
    del identity
    linked = None if message_id is None or not message_id.strip() else _path_id(message_id)
    return ok(await CsService(session).preview(_path_id(template_id), linked))


@router.get("/tickets", response_model=ApiResponse[PageData[CsTicketView]], summary="售后工单")
async def list_tickets(
    identity: Reader,
    session: DbSession,
    shop_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApiResponse[PageData[CsTicketView]]:
    del identity
    data = await CsService(session).tickets(shop_id=shop_id, status=status, cursor=cursor, limit=limit)
    return ok(data)


@router.post("/tickets", response_model=ApiResponse[CsTicketView], summary="新建售后工单")
async def create_ticket(
    payload: CsTicketCreate,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsTicketView]:
    del idempotency_key
    return ok(await CsService(session).create_ticket(payload, identity.user.id))


@router.get("/tickets/{ticket_id}", response_model=ApiResponse[CsTicketView], summary="工单详情")
async def get_ticket(
    ticket_id: str,
    identity: Reader,
    session: DbSession,
) -> ApiResponse[CsTicketView]:
    del identity
    return ok(await CsService(session).ticket(_path_id(ticket_id)))


@router.post("/tickets/{ticket_id}/assign", response_model=ApiResponse[CsTicketView], summary="分派工单")
async def assign_ticket(
    ticket_id: str,
    payload: CsTicketAssign,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsTicketView]:
    del idempotency_key
    return ok(await CsService(session).assign_ticket(_path_id(ticket_id), payload, identity.user.id))


@router.post("/tickets/{ticket_id}/notes", response_model=ApiResponse[CsTicketView], summary="跟进工单")
async def note_ticket(
    ticket_id: str,
    payload: CsTicketNoteWrite,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsTicketView]:
    del idempotency_key
    return ok(await CsService(session).add_ticket_note(_path_id(ticket_id), payload, identity.user.id))


@router.post("/tickets/{ticket_id}/close", response_model=ApiResponse[CsTicketView], summary="关闭工单")
async def close_ticket(
    ticket_id: str,
    payload: CsTicketClose,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CsTicketView]:
    del idempotency_key
    return ok(await CsService(session).close_ticket(_path_id(ticket_id), payload, identity.user.id))
