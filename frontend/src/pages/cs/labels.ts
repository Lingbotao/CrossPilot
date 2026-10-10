export function label(map: object, key: string): string {
  const value = (map as Record<string, string | undefined>)[key];
  return value ?? key;
}
