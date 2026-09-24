import { checkPassword, PASSWORD_MAX_LENGTH } from './password';

describe('checkPassword', () => {
  it('accepts a password satisfying every rule', () => {
    expect(checkPassword('CrossPilot1!')).toEqual({ valid: true, errors: [] });
  });

  it.each([
    ['Short1!', '至少 10 位'],
    ['crosspilota', '大写字母、小写字母、数字、特殊字符中至少包含三类'],
    ['1234567890', '大写字母、小写字母、数字、特殊字符中至少包含三类'],
    ['!!!!!!!!!!', '大写字母、小写字母、数字、特殊字符中至少包含三类'],
  ])('rejects %s', (password, expectedError) => {
    expect(checkPassword(password).errors).toContain(expectedError);
  });

  it.each(['CrossPilot1', 'crosspilot1!', 'CROSSPILOT1!'])(
    'accepts any three character classes: %s',
    (password) => {
      expect(checkPassword(password).valid).toBe(true);
    },
  );

  it('rejects passwords beyond the backend maximum', () => {
    expect(checkPassword(`Aa1!${'x'.repeat(PASSWORD_MAX_LENGTH)}`).valid).toBe(false);
  });
});
