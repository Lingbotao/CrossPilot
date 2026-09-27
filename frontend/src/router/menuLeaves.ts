import { MENU_ITEMS, type MenuItemConfig } from '@/router/menu';

export function menuLeaves(items: readonly MenuItemConfig[] = MENU_ITEMS): MenuItemConfig[] {
  return items.flatMap((item) => (item.children && item.children.length > 0 ? menuLeaves(item.children) : [item]));
}
