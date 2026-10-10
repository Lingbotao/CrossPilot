import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { dashboardApi } from '@/api/dashboard';
import { ApiError } from '@/api/client';
import { feedback } from '@/app/feedback';
import zhCN from '@/i18n/zh-CN';

import { analyticsQuery, defaultAnalyticsRange, type AnalyticsFilterValues } from './query';

const copy = zhCN.analyticsPage;

export function useAnalyticsFilters() {
  const client = useQueryClient();
  const [values, setValues] = useState<AnalyticsFilterValues>({ range: defaultAnalyticsRange() });
  const query = analyticsQuery(values);
  const rebuild = useMutation({
    mutationFn: (next: AnalyticsFilterValues) => {
      const window = analyticsQuery(next);
      return dashboardApi.rebuild({ date_from: window.date_from, date_to: window.date_to });
    },
    onSuccess: async (result) => {
      feedback().message.success(copy.rebuilt.replace('{{count}}', String(result.shop_days)));
      await client.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (error: unknown) => {
      feedback().message.error(error instanceof ApiError ? error.message : copy.requestFailed);
    },
  });
  return { values, setValues, query, rebuild };
}
