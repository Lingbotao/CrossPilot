"""客服接口模型。金额是字符串，主键是字符串。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import MoneyStr


class CsSyncRequest(BaseModel):
    shop_id: str


class CsSyncView(BaseModel):
    task_id: str
    shop_id: str
    messages: int


class CsShopView(BaseModel):
    id: str
    shop_name: str
    platform_code: str
    site_code: str


class CsAssigneeView(BaseModel):
    user_id: str
    display_name: str | None


class CsOrderContext(BaseModel):
    order_id: str
    platform_order_id: str
    unified_status: str
    buyer_name: str | None
    tracking_no: str | None
    total_amount: MoneyStr
    currency: str


class CsReturnLink(BaseModel):
    return_id: str
    status: str
    reason: str
    refund_amount: MoneyStr
    currency: str
    restock_status: str


class CsMessageView(BaseModel):
    id: str
    shop_id: str
    platform_code: str
    platform_message_id: str
    platform_order_id: str | None
    order_id: str | None
    buyer_id: str | None
    buyer_name: str | None
    content: str
    lang: str | None
    status: str
    sla_level: str
    sla_deadline: datetime
    received_at: datetime
    console_url: str
    order: CsOrderContext | None = None


class CsTemplateWrite(BaseModel):
    scene: str
    lang: str
    name: str = Field(max_length=64)
    body: str = Field(max_length=4000)


class CsTemplatePatch(BaseModel):
    scene: str | None = None
    lang: str | None = None
    name: str | None = Field(default=None, max_length=64)
    body: str | None = Field(default=None, max_length=4000)


class CsTemplateView(BaseModel):
    id: str
    scene: str
    lang: str
    name: str
    body: str


class CsTemplatePreview(BaseModel):
    text: str
    missing: list[str]


class CsTicketCreate(BaseModel):
    title: str = Field(max_length=128)
    ticket_type: str
    shop_id: str | None = None
    message_id: str | None = None
    order_id: str | None = None
    return_order_id: str | None = None


class CsTicketAssign(BaseModel):
    assignee_user_id: str


class CsTicketNoteWrite(BaseModel):
    body: str = Field(max_length=2000)


class CsTicketClose(BaseModel):
    resolution: str = Field(max_length=2000)


class CsTicketNoteView(BaseModel):
    id: str
    body: str
    author_name: str | None
    created_at: datetime


class CsTicketView(BaseModel):
    id: str
    shop_id: str
    message_id: str | None
    order_id: str | None
    return_order_id: str | None
    buyer_id: str | None
    buyer_name: str | None
    ticket_type: str
    status: str
    assignee_user_id: str | None
    title: str
    resolution: str | None
    order: CsOrderContext | None = None
    return_order: CsReturnLink | None = None
    notes: list[CsTicketNoteView] = Field(default_factory=list)
