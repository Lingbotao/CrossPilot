/** M1 内置角色与权限矩阵接口。 */

import { api } from './client';
import type { Role } from './types';

export const rolesApi = {
  list: () => api.get<Role[]>('/roles'),
};
