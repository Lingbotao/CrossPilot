/**
 * 补齐 node_modules/.bin 软链（本机 npm 的「临时名+重命名」被文件代理拦截，只能用直接建链绕开）。
 *
 * 用法：node tools/link-bins.mjs   （在 frontend/ 下执行）
 */

import { readFileSync, readdirSync, existsSync, chmodSync, symlinkSync, unlinkSync } from 'node:fs';
import { join, dirname, relative, basename } from 'node:path';
import process from 'node:process';

const root = process.cwd();
const nm = join(root, 'node_modules');
const binDir = join(nm, '.bin');

if (!existsSync(nm)) {
  console.error('node_modules 不存在，先跑 npm install');
  process.exit(1);
}

let linked = 0;
let skipped = 0;

/** 遍历一个包的目录，按 package.json 的 bin 字段建链 */
function linkPackage(pkgDir) {
  const manifestPath = join(pkgDir, 'package.json');
  if (!existsSync(manifestPath)) return;

  let manifest;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  } catch {
    return;
  }
  if (!manifest.bin) return;

  const entries =
    typeof manifest.bin === 'string'
      ? [[manifest.name.replace(/^@[^/]+\//, ''), manifest.bin]]
      : Object.entries(manifest.bin);

  for (const [name, rel] of entries) {
    const target = join(pkgDir, rel);
    if (!existsSync(target)) {
      skipped += 1;
      continue;
    }
    const linkPath = join(binDir, name);
    const relTarget = relative(binDir, target);
    try {
      if (existsSync(linkPath)) unlinkSync(linkPath);
      symlinkSync(relTarget, linkPath);
      chmodSync(target, 0o755);
      linked += 1;
    } catch (err) {
      skipped += 1;
      console.error(`  跳过 ${name}: ${err.code ?? err.message}`);
    }
  }
}

for (const entry of readdirSync(nm, { withFileTypes: true })) {
  if (!entry.isDirectory() || entry.name === '.bin') continue;
  if (entry.name.startsWith('@')) {
    const scopeDir = join(nm, entry.name);
    for (const sub of readdirSync(scopeDir, { withFileTypes: true })) {
      if (sub.isDirectory()) linkPackage(join(scopeDir, sub.name));
    }
  } else {
    linkPackage(join(nm, entry.name));
  }
}

console.log(`已建立 ${linked} 个软链（跳过 ${skipped}）→ ${basename(dirname(binDir))}/node_modules/.bin`);
