/** 客服消息、模板和售后工单。同步只读，回复在平台后台完成。 */

import { api } from './client';
import type {
  CsAssigneeView,
  CsMessageView,
  CsShopView,
  CsSyncView,
  CsTemplatePreview,
  CsTemplateView,
  CsTicketView,
  PageData,
} from './types';

export interface CsMessageQuery {
  shop_id?: string;
  status?: string;
  sla?: string;
  cursor?: string;
  limit?: number;
}

export interface CsTicketQuery {
  shop_id?: string;
  status?: string;
  cursor?: string;
  limit?: number;
}

export interface CsTemplateWrite {
  scene: string;
  lang: string;
  name: string;
  body: string;
}

export interface CsTicketCreate {
  title: string;
  ticket_type: string;
  shop_id?: string;
  message_id?: string;
  order_id?: string;
  return_order_id?: string;
}

export const csApi = {
  shops: () => api.get<CsShopView[]>('/cs/shops'),

  assignees: () => api.get<CsAssigneeView[]>('/cs/assignees'),

  syncMessages: (shopId: string) =>
    api.post<CsSyncView>('/cs/messages/sync', { shop_id: shopId }, { idempotent: true }),

  messages: (query: CsMessageQuery) => api.get<PageData<CsMessageView>>('/cs/messages', { params: query }),

  message: (messageId: string) => api.get<CsMessageView>(`/cs/messages/${encodeURIComponent(messageId)}`),

  markRead: (messageId: string) =>
    api.post<CsMessageView>(`/cs/messages/${encodeURIComponent(messageId)}/read`, {}, { idempotent: true }),

  templates: () => api.get<CsTemplateView[]>('/cs/templates'),

  createTemplate: (payload: CsTemplateWrite) =>
    api.post<CsTemplateView>('/cs/templates', payload, { idempotent: true }),

  deleteTemplate: (templateId: string) =>
    api.delete<CsTemplateView>(`/cs/templates/${encodeURIComponent(templateId)}`, { idempotent: true }),

  previewTemplate: (templateId: string, messageId?: string) =>
    api.get<CsTemplatePreview>(`/cs/templates/${encodeURIComponent(templateId)}/preview`, {
      params: messageId ? { message_id: messageId } : {},
    }),

  tickets: (query: CsTicketQuery) => api.get<PageData<CsTicketView>>('/cs/tickets', { params: query }),

  ticket: (ticketId: string) => api.get<CsTicketView>(`/cs/tickets/${encodeURIComponent(ticketId)}`),

  createTicket: (payload: CsTicketCreate) =>
    api.post<CsTicketView>('/cs/tickets', payload, { idempotent: true }),

  assignTicket: (ticketId: string, assigneeUserId: string) =>
    api.post<CsTicketView>(
      `/cs/tickets/${encodeURIComponent(ticketId)}/assign`,
      { assignee_user_id: assigneeUserId },
      { idempotent: true },
    ),

  noteTicket: (ticketId: string, body: string) =>
    api.post<CsTicketView>(`/cs/tickets/${encodeURIComponent(ticketId)}/notes`, { body }, { idempotent: true }),

  closeTicket: (ticketId: string, resolution: string) =>
    api.post<CsTicketView>(
      `/cs/tickets/${encodeURIComponent(ticketId)}/close`,
      { resolution },
      { idempotent: true },
    ),
};
