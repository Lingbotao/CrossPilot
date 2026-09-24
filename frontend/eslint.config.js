import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage'] },

  {
    extends: [js.configs.recommended, ...tseslint.configs.recommendedTypeChecked],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // ★ 金额必须走 Decimal 字符串，禁止用 parseFloat 处理钱。
      //   C4 约束的前端侧防线：后端已用 NUMERIC(20,6)，
      //   前端只要 parseFloat 一次，精度就前功尽弃（且大整数会直接丢位）。
      'no-restricted-globals': [
        'error',
        { name: 'parseFloat', message: '金额请使用 utils/money 的字符串运算，禁止 parseFloat（精度丢失）' },
      ],

      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      // 后端契约里可空字段很多，显式 any 会被 code review 拦；这里只告警
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-misused-promises': [
        'error',
        { checksVoidReturn: { attributes: false } },
      ],
      eqeqeq: ['error', 'always'],
    },
  },

  // 配置文件与测试放开类型检查相关的限制
  {
    files: ['*.config.ts', '**/*.test.{ts,tsx}'],
    rules: {
      '@typescript-eslint/no-unsafe-assignment': 'off',
      'no-restricted-globals': 'off',
    },
  },
);
