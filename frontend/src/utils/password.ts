/** 与 M1 后端保持一致的密码强度规则。 */

export const PASSWORD_MIN_LENGTH = 10;
export const PASSWORD_MAX_LENGTH = 128;

export interface PasswordCheck {
  valid: boolean;
  errors: string[];
}

export function checkPassword(password: string): PasswordCheck {
  const errors: string[] = [];

  if (password.length < PASSWORD_MIN_LENGTH) errors.push(`至少 ${PASSWORD_MIN_LENGTH} 位`);
  if (password.length > PASSWORD_MAX_LENGTH) errors.push(`最多 ${PASSWORD_MAX_LENGTH} 位`);
  const classCount = [
    /\p{Ll}/u.test(password),
    /\p{Lu}/u.test(password),
    /\p{Nd}/u.test(password),
    /[^\p{L}\p{N}]/u.test(password),
  ].filter(Boolean).length;
  if (classCount < 3) errors.push('大写字母、小写字母、数字、特殊字符中至少包含三类');

  return { valid: errors.length === 0, errors };
}

export function passwordHelp(password: string): string {
  return checkPassword(password).errors.join('、');
}
