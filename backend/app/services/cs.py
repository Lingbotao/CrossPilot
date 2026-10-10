"""客服消息同步、模板渲染和售后工单（M5-05 / F11-01~06）。

消息只读入库，回复在平台后台完成。工单跟已有退货单，不另做一套退款。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView, UnifiedMessage
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import adapter_registry
from app.core.config import settings
from app.core.errors import (
    AppError,
    CsTemplateDuplicateError,
    CsTicketStateError,
    ErrorCode,
    NotFoundError,
    ParamInvalidError,
)
from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.engines.cs import (
    MESSAGE_STATUSES,
    MESSAGE_UNREAD,
    SLA_LEVELS,
    TEMPLATE_SCENES,
    TICKET_OPEN,
    TICKET_STATUSES,
    TICKET_TYPES,
    TicketAction,
    clean_lang,
    mark_message_read,
    next_ticket_status,
    render_template,
    resolve_deadline,
    sla_level,
)
from app.models.cs import CsMessage, CsTemplate, CsTicket, CsTicketNote
from app.models.enums import ShopStatus, SyncStatus, SyncTrigger
from app.models.order import ReturnOrder, SalesOrder
from app.models.platform import Shop, SyncTask
from app.repositories.cs import CsRepository
from app.repositories.platform import ShopCredentialRepository, ShopRepository, SyncTaskRepository
from app.schemas.common import money_to_str
from app.schemas.cs import (
    CsAssigneeView,
    CsMessageView,
    CsOrderContext,
    CsReturnLink,
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
    CsTicketNoteView,
    CsTicketNoteWrite,
    CsTicketView,
)
from app.schemas.listing import parse_id
from app.services.credential_service import view_from_row

_TEMPLATE_LIMIT = 200


class CsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CsRepository(session)
        self.shop_repo = ShopRepository(session)
        self.credentials = ShopCredentialRepository(session)
        self.tasks = SyncTaskRepository(session)
        register_builtin_adapters()

    async def shops(self) -> list[CsShopView]:
        rows = await self.repo.active_shops()
        return [
            CsShopView(
                id=str(row.id),
                shop_name=row.shop_name,
                platform_code=row.platform_code,
                site_code=row.site_code,
            )
            for row in rows
        ]

    async def assignees(self) -> list[CsAssigneeView]:
        rows = await self.repo.assignees()
        return [CsAssigneeView(user_id=str(user_id), display_name=name) for user_id, name in rows]

    async def sync(
        self,
        payload: CsSyncRequest,
        *,
        tenant_id: int,
        actor_id: int | None,
        idempotency_key: str | None,
        trigger: SyncTrigger = SyncTrigger.MANUAL,
    ) -> CsSyncView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.tasks.find_by_idempotency("cs", key)
            if existing is not None and int(existing.status) == int(SyncStatus.SUCCESS):
                summary = existing.stats.get("summary")
                if isinstance(summary, dict):
                    return CsSyncView.model_validate(summary)
            if existing is not None:
                raise AppError("相同幂等键的客服同步不能重复执行", code=ErrorCode.IDEMPOTENCY_CONFLICT)
        shop = await self.shop_repo.get_or_404(_required_id(payload.shop_id))
        if int(shop.status) != int(ShopStatus.ACTIVE):
            raise AppError("店铺当前不能同步消息", code=ErrorCode.SHOP_GRANT_EXPIRED)
        credential = await self.credentials.get_by_shop_id(shop.id)
        if credential is None:
            raise AppError("店铺还没有授权，不能同步消息", code=ErrorCode.SHOP_GRANT_EXPIRED)
        pulled = await self._pull(shop, view_from_row(shop, credential))
        stored = await self._store(shop, pulled, actor_id)
        task = await self.tasks.add(
            SyncTask(
                tenant_id=tenant_id,
                shop_id=shop.id,
                module="cs",
                trigger_type=int(trigger),
                status=int(SyncStatus.SUCCESS),
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                stats={},
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        view = CsSyncView(task_id=str(task.id), shop_id=str(shop.id), messages=stored)
        stats: dict[str, object] = {"summary": view.model_dump()}
        if key:
            stats["idempotency_key"] = key
        task.stats = stats
        await self.repo.flush()
        return view

    async def messages(
        self,
        *,
        shop_id: str | None,
        status: str | None,
        sla: str | None,
        cursor: str | None,
        limit: int,
    ) -> PageData[CsMessageView]:
        status_code = _choice(status, MESSAGE_STATUSES, "消息状态不合法")
        sla_code = _choice(sla, SLA_LEVELS, "SLA 状态不合法")
        now = datetime.now(UTC)
        warn = timedelta(hours=settings.cs_sla_warn_hours)
        rows = await self.repo.list_messages(
            shop_id=_optional_id(shop_id),
            status=status_code,
            sla=sla_code,
            now=now,
            warn=warn,
            before_id=_before_id(cursor),
            limit=limit + 1,
        )
        views = [await self._message_view(row, now, warn, with_order=False) for row in rows]
        return build_cursor_page(views, limit)

    async def message(self, message_id: int) -> CsMessageView:
        row = await self.repo.get_message(message_id)
        if row is None:
            raise NotFoundError()
        now = datetime.now(UTC)
        warn = timedelta(hours=settings.cs_sla_warn_hours)
        return await self._message_view(row, now, warn, with_order=True)

    async def mark_read(self, message_id: int, actor_id: int | None) -> CsMessageView:
        row = await self.repo.get_message(message_id)
        if row is None:
            raise NotFoundError()
        try:
            row.status = mark_message_read(row.status)
        except ValueError as exc:
            raise ParamInvalidError(str(exc)) from exc
        row.updated_by = actor_id
        await self.repo.flush()
        now = datetime.now(UTC)
        warn = timedelta(hours=settings.cs_sla_warn_hours)
        return await self._message_view(row, now, warn, with_order=True)

    async def templates(self) -> list[CsTemplateView]:
        rows = await self.repo.list_templates(_TEMPLATE_LIMIT)
        return [_template_view(row) for row in rows]

    async def create_template(self, payload: CsTemplateWrite, actor_id: int | None) -> CsTemplateView:
        scene, lang, name, body = _template_fields(payload.scene, payload.lang, payload.name, payload.body)
        if await self.repo.find_template(scene, lang, name) is not None:
            raise CsTemplateDuplicateError("同一场景、语言和名称的模板已存在")
        row = await self.repo.add_template(
            CsTemplate(scene=scene, lang=lang, name=name, body=body, created_by=actor_id, updated_by=actor_id)
        )
        return _template_view(row)

    async def update_template(
        self,
        template_id: int,
        payload: CsTemplatePatch,
        actor_id: int | None,
    ) -> CsTemplateView:
        row = await self.repo.get_template(template_id)
        if row is None:
            raise NotFoundError()
        scene, lang, name, body = _template_fields(
            payload.scene or row.scene,
            payload.lang or row.lang,
            payload.name or row.name,
            payload.body or row.body,
        )
        other = await self.repo.find_template(scene, lang, name)
        if other is not None and int(other.id) != int(row.id):
            raise CsTemplateDuplicateError("同一场景、语言和名称的模板已存在")
        row.scene = scene
        row.lang = lang
        row.name = name
        row.body = body
        row.updated_by = actor_id
        await self.repo.flush()
        return _template_view(row)

    async def delete_template(self, template_id: int, actor_id: int | None) -> CsTemplateView:
        row = await self.repo.get_template(template_id)
        if row is None:
            raise NotFoundError()
        row.deleted_at = datetime.now(UTC)
        row.updated_by = actor_id
        await self.repo.flush()
        return _template_view(row)

    async def preview(self, template_id: int, message_id: int | None) -> CsTemplatePreview:
        row = await self.repo.get_template(template_id)
        if row is None:
            raise NotFoundError()
        variables = {"order_no": "", "tracking_no": "", "buyer_name": ""}
        if message_id is not None:
            message = await self.repo.get_message(message_id)
            if message is None:
                raise NotFoundError()
            variables = await self._variables(message)
        text, missing = render_template(row.body, variables)
        return CsTemplatePreview(text=text, missing=list(missing))

    async def tickets(
        self,
        *,
        shop_id: str | None,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> PageData[CsTicketView]:
        status_code = _choice(status, TICKET_STATUSES, "工单状态不合法")
        rows = await self.repo.list_tickets(
            shop_id=_optional_id(shop_id),
            status=status_code,
            before_id=_before_id(cursor),
            limit=limit + 1,
        )
        views = [await self._ticket_view(row, with_detail=False) for row in rows]
        return build_cursor_page(views, limit)

    async def ticket(self, ticket_id: int) -> CsTicketView:
        row = await self.repo.get_ticket(ticket_id)
        if row is None:
            raise NotFoundError()
        return await self._ticket_view(row, with_detail=True)

    async def create_ticket(self, payload: CsTicketCreate, actor_id: int | None) -> CsTicketView:
        title = _text(payload.title, "标题不能为空", 128)
        ticket_type = _required_choice(payload.ticket_type, TICKET_TYPES, "工单类型不合法")
        message = None
        if payload.message_id:
            message = await self.repo.get_message(_required_id(payload.message_id))
            if message is None:
                raise NotFoundError()
        shop_id = message.shop_id if message is not None else _required_id(payload.shop_id or "")
        shop = await self.shop_repo.get_or_404(shop_id)
        order = await self._ticket_order(shop.id, message, payload.order_id)
        linked = await self._linked_return(order, payload.return_order_id)
        row = await self.repo.add_ticket(
            CsTicket(
                shop_id=shop.id,
                message_id=None if message is None else message.id,
                order_id=None if order is None else order.id,
                return_order_id=None if linked is None else linked.id,
                buyer_id=None if message is None else message.buyer_id,
                buyer_name=None if message is None else message.buyer_name,
                ticket_type=ticket_type,
                status=TICKET_OPEN,
                title=title,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        return await self._ticket_view(row, with_detail=True)

    async def assign_ticket(self, ticket_id: int, payload: CsTicketAssign, actor_id: int | None) -> CsTicketView:
        row = await self._open_ticket(ticket_id, TicketAction.ASSIGN, actor_id)
        assignee = _required_id(payload.assignee_user_id)
        members = {user_id for user_id, _name in await self.repo.assignees()}
        if assignee not in members:
            raise NotFoundError()
        row.assignee_user_id = assignee
        await self.repo.flush()
        return await self._ticket_view(row, with_detail=True)

    async def add_ticket_note(
        self,
        ticket_id: int,
        payload: CsTicketNoteWrite,
        actor_id: int | None,
    ) -> CsTicketView:
        row = await self._open_ticket(ticket_id, TicketAction.NOTE, actor_id)
        body = _text(payload.body, "跟进内容不能为空", 2000)
        await self.repo.add_note(
            CsTicketNote(ticket_id=row.id, body=body, created_by=actor_id, updated_by=actor_id)
        )
        return await self._ticket_view(row, with_detail=True)

    async def close_ticket(self, ticket_id: int, payload: CsTicketClose, actor_id: int | None) -> CsTicketView:
        row = await self._open_ticket(ticket_id, TicketAction.CLOSE, actor_id)
        row.resolution = _text(payload.resolution, "关闭说明不能为空", 2000)
        await self.repo.flush()
        return await self._ticket_view(row, with_detail=True)

    async def _pull(self, shop: Shop, cred: CredentialView) -> list[UnifiedMessage]:
        adapter = adapter_registry.get(shop.platform_code)
        items: list[UnifiedMessage] = []
        cursor: str | None = None
        seen: set[str] = set()
        try:
            for _page in range(settings.cs_sync_max_pages):
                page = await adapter.fetch_messages(cred, cursor=cursor)
                items.extend(page.items)
                if not page.next_cursor or page.next_cursor in seen:
                    break
                seen.add(page.next_cursor)
                cursor = page.next_cursor
        except AdapterError as exc:
            raise AppError(str(exc), code=ErrorCode.PLATFORM_API_ERROR) from exc
        return items

    async def _store(self, shop: Shop, pulled: list[UnifiedMessage], actor_id: int | None) -> int:
        hours = _sla_hours(shop.platform_code)
        stored = 0
        for item in pulled:
            try:
                deadline = resolve_deadline(item.received_at, item.sla_deadline, hours)
            except ValueError as exc:
                raise ParamInvalidError(str(exc)) from exc
            order = None
            if item.platform_order_id:
                order = await self.repo.find_order(shop.id, item.platform_order_id)
            existing = await self.repo.find_message(shop.id, item.platform_message_id)
            if existing is None:
                await self.repo.add_message(
                    CsMessage(
                        shop_id=shop.id,
                        platform_code=shop.platform_code,
                        platform_message_id=item.platform_message_id,
                        platform_order_id=item.platform_order_id,
                        order_id=None if order is None else order.id,
                        buyer_id=item.buyer_id,
                        buyer_name=item.buyer_name,
                        content=item.content,
                        lang=item.lang,
                        status=MESSAGE_UNREAD,
                        sla_deadline=deadline,
                        received_at=item.received_at,
                        console_url=item.console_url,
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )
            else:
                changed = existing.content != item.content
                existing.platform_order_id = item.platform_order_id
                existing.order_id = None if order is None else order.id
                existing.buyer_id = item.buyer_id
                existing.buyer_name = item.buyer_name
                existing.content = item.content
                existing.lang = item.lang
                existing.sla_deadline = deadline
                existing.console_url = item.console_url
                existing.updated_by = actor_id
                if changed:
                    existing.status = MESSAGE_UNREAD
            stored += 1
        await self.repo.flush()
        return stored

    async def _message_view(
        self,
        row: CsMessage,
        now: datetime,
        warn: timedelta,
        *,
        with_order: bool,
    ) -> CsMessageView:
        order = None
        if with_order and row.order_id is not None:
            found = await self.repo.get_order(int(row.order_id))
            if found is not None:
                order = await self._order_context(found)
        return CsMessageView(
            id=str(row.id),
            shop_id=str(row.shop_id),
            platform_code=row.platform_code,
            platform_message_id=row.platform_message_id,
            platform_order_id=row.platform_order_id,
            order_id=None if row.order_id is None else str(row.order_id),
            buyer_id=row.buyer_id,
            buyer_name=row.buyer_name,
            content=row.content,
            lang=row.lang,
            status=row.status,
            sla_level=sla_level(row.sla_deadline, now, warn),
            sla_deadline=row.sla_deadline,
            received_at=row.received_at,
            console_url=row.console_url,
            order=order,
        )

    async def _order_context(self, order: SalesOrder) -> CsOrderContext:
        amount = money_to_str(order.total_amount) or "0.000000"
        buyer = order.buyer_info.get("name") if isinstance(order.buyer_info, dict) else None
        buyer_name = buyer.strip() if isinstance(buyer, str) and buyer.strip() else None
        return CsOrderContext(
            order_id=str(order.id),
            platform_order_id=order.platform_order_id,
            unified_status=order.unified_status,
            buyer_name=buyer_name,
            tracking_no=await self.repo.tracking_no(order.id),
            total_amount=amount,
            currency=order.currency.strip(),
        )

    async def _variables(self, message: CsMessage) -> dict[str, str]:
        order_no = message.platform_order_id or ""
        tracking = ""
        buyer = message.buyer_name or ""
        if message.order_id is not None:
            order = await self.repo.get_order(int(message.order_id))
            if order is not None:
                order_no = order.platform_order_id
                tracking = await self.repo.tracking_no(order.id) or ""
                if not buyer:
                    context = await self._order_context(order)
                    buyer = context.buyer_name or ""
        return {"order_no": order_no, "tracking_no": tracking, "buyer_name": buyer}

    async def _ticket_order(
        self,
        shop_id: int,
        message: CsMessage | None,
        order_id: str | None,
    ) -> SalesOrder | None:
        if message is not None and message.order_id is not None:
            order = await self.repo.get_order(int(message.order_id))
            if order is None or int(order.shop_id) != shop_id:
                raise NotFoundError()
            return order
        if not order_id:
            return None
        order = await self.repo.get_order(_required_id(order_id))
        if order is None or int(order.shop_id) != shop_id:
            raise NotFoundError()
        return order

    async def _linked_return(self, order: SalesOrder | None, return_id: str | None) -> ReturnOrder | None:
        if not return_id:
            return None
        if order is None:
            raise ParamInvalidError("关联退货单时必须先有订单")
        row = await self.repo.get_return(_required_id(return_id))
        if row is None or int(row.order_id) != int(order.id):
            raise NotFoundError()
        return row

    async def _open_ticket(self, ticket_id: int, action: str, actor_id: int | None) -> CsTicket:
        row = await self.repo.get_ticket(ticket_id)
        if row is None:
            raise NotFoundError()
        try:
            row.status = next_ticket_status(row.status, action)
        except ValueError as exc:
            raise CsTicketStateError(str(exc)) from exc
        row.updated_by = actor_id
        return row

    async def _ticket_view(self, row: CsTicket, *, with_detail: bool) -> CsTicketView:
        order = None
        linked = None
        notes: list[CsTicketNoteView] = []
        if with_detail:
            if row.order_id is not None:
                found = await self.repo.get_order(int(row.order_id))
                if found is not None:
                    order = await self._order_context(found)
            if row.return_order_id is not None:
                found_return = await self.repo.get_return(int(row.return_order_id))
                if found_return is not None:
                    linked = _return_link(found_return)
            notes = [
                CsTicketNoteView(
                    id=str(note.id),
                    body=note.body,
                    author_name=author,
                    created_at=note.created_at,
                )
                for note, author in await self.repo.list_notes(row.id)
            ]
        return CsTicketView(
            id=str(row.id),
            shop_id=str(row.shop_id),
            message_id=None if row.message_id is None else str(row.message_id),
            order_id=None if row.order_id is None else str(row.order_id),
            return_order_id=None if row.return_order_id is None else str(row.return_order_id),
            buyer_id=row.buyer_id,
            buyer_name=row.buyer_name,
            ticket_type=row.ticket_type,
            status=row.status,
            assignee_user_id=None if row.assignee_user_id is None else str(row.assignee_user_id),
            title=row.title,
            resolution=row.resolution,
            order=order,
            return_order=linked,
            notes=notes,
        )


def _return_link(row: ReturnOrder) -> CsReturnLink:
    amount = money_to_str(row.refund_amount) or "0.000000"
    return CsReturnLink(
        return_id=str(row.id),
        status=row.status,
        reason=row.reason,
        refund_amount=amount,
        currency=row.currency.strip(),
        restock_status=row.restock_status,
    )


def _template_view(row: CsTemplate) -> CsTemplateView:
    return CsTemplateView(id=str(row.id), scene=row.scene, lang=row.lang, name=row.name, body=row.body)


def _template_fields(scene: str, lang: str, name: str, body: str) -> tuple[str, str, str, str]:
    scene_code = _required_choice(scene, TEMPLATE_SCENES, "模板场景不合法")
    try:
        cleaned_lang = clean_lang(lang)
    except ValueError as exc:
        raise ParamInvalidError(str(exc)) from exc
    return scene_code, cleaned_lang, _text(name, "模板名称不能为空", 64), _text(body, "模板内容不能为空", 4000)


def _sla_hours(platform_code: str) -> int:
    table = settings.cs_sla_hours_by_platform
    raw = table.get(platform_code, settings.cs_sla_default_hours)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ParamInvalidError("SLA 小时数配置不合法") from exc


def _choice(value: str | None, allowed: tuple[str, ...], message: str) -> str | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned not in allowed:
        raise ParamInvalidError(message)
    return cleaned


def _required_choice(value: str, allowed: tuple[str, ...], message: str) -> str:
    picked = _choice(value, allowed, message)
    if picked is None:
        raise ParamInvalidError(message)
    return picked


def _text(value: str, message: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ParamInvalidError(message)
    if len(cleaned) > limit:
        raise ParamInvalidError(message)
    return cleaned


def _required_id(value: str) -> int:
    try:
        return parse_id(value)
    except ValueError as exc:
        raise ParamInvalidError("ID 不合法") from exc


def _optional_id(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    return _required_id(value)


def _before_id(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = decode_cursor(cursor).get("id")
    if raw is None:
        return None
    try:
        return parse_id(raw)
    except ValueError:
        return None


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 128:
        raise ParamInvalidError("幂等键过长")
    return cleaned
