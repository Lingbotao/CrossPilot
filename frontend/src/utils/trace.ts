/** 追踪 ID 生成（与后端 ``X-Trace-Id`` 对齐）。 */

const ENCODING = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'; // Crockford Base32（ULID 用）

/** 生成一个 ULID 风格的追踪 ID：前 10 位时间戳 + 16 位随机。 */
export function newTraceId(): string {
  const now = Date.now();
  let timePart = '';
  let remaining = now;
  for (let i = 0; i < 10; i += 1) {
    timePart = ENCODING[remaining % 32] + timePart;
    remaining = Math.floor(remaining / 32);
  }

  const randomBytes = new Uint8Array(10);
  crypto.getRandomValues(randomBytes);
  let randomPart = '';
  for (const byte of randomBytes) {
    randomPart += ENCODING[byte % 32];
  }
  return (timePart + randomPart).slice(0, 26);
}
