/** M1 成员、邀请与数据范围接口。 */

import { api } from './client';
import type {
  AcceptInvitationRequest,
  AcceptedInvitationResponse,
  InviteMemberRequest,
  MemberInvitation,
  Member,
  MemberListResponse,
  UpdateDataScopeRequest,
  UpdateMemberRoleRequest,
} from './types';

export const membersApi = {
  list: () => api.get<MemberListResponse>('/members'),

  invite: (payload: InviteMemberRequest) =>
    api.post<MemberInvitation>('/members/invite', payload, { idempotent: true }),

  acceptInvitation: (payload: AcceptInvitationRequest) =>
    api.post<AcceptedInvitationResponse>('/members/invitations/accept', payload, {
      idempotent: true,
      skipAuthRefresh: true,
    }),

  revokeInvitation: (invitationId: string) =>
    api.post<{ status: string }>(`/members/invitations/${encodeURIComponent(invitationId)}/revoke`, undefined, {
      idempotent: true,
    }),

  updateRole: (memberId: string, payload: UpdateMemberRoleRequest) =>
    api.patch<Member>(`/members/${encodeURIComponent(memberId)}/role`, payload, {
      idempotent: true,
    }),

  updateDataScope: (memberId: string, payload: UpdateDataScopeRequest) =>
    api.patch<Member>(`/members/${encodeURIComponent(memberId)}/data-scope`, payload, {
      idempotent: true,
    }),

  remove: (memberId: string, confirmationToken: string) =>
    api.delete<{ status: string }>(`/members/${encodeURIComponent(memberId)}`, {
      headers: { 'X-Confirmation-Token': confirmationToken },
      idempotent: true,
    }),
};
