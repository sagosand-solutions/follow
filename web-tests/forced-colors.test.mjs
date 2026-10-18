import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const dir = new URL('../public/css/', import.meta.url).pathname;
const all = readdirSync(dir)
  .filter((f) => f.endsWith('.css'))
  .map((f) => readFileSync(join(dir, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, ''))
  .join('\n');

// The bodies of every @media (forced-colors: active) block, found by brace matching.
function forcedBlocks(src) {
  const out = [];
  const re = /@media\s*\(forced-colors:\s*active\)\s*\{/g;
  let m;
  while ((m = re.exec(src))) {
    let depth = 1;
    let i = re.lastIndex;
    for (; i < src.length && depth; i++) {
      if (src[i] === '{') depth++;
      else if (src[i] === '}') depth--;
    }
    out.push(src.slice(re.lastIndex, i - 1));
  }
  return out;
}

const rules = forcedBlocks(all).flatMap((body) => [...body.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((r) => ({ selector: r[1].trim(), decls: r[2] })));
const ruleFor = (selector) => rules.filter((r) => r.selector.split(',').map((s) => s.trim()).includes(selector));

test('the stylesheets have forced-colours rules to check', () => {
  assert.ok(rules.length > 10);
});

test('rules that opt out of forced colours paint only with system colours', () => {
  for (const r of rules.filter((x) => /forced-color-adjust:\s*none/.test(x.decls))) {
    for (const d of r.decls.split(';')) {
      if (/^\s*(color|background(-color)?|border(-color)?|outline(-color)?)\s*:/.test(d)) assert.doesNotMatch(d, /var\(|#[0-9a-f]{3,8}\b|rgb/i, `${r.selector}: ${d.trim()}`);
    }
  }
});

test('in forced colours the primary action is filled and aria-disabled buttons look disabled', () => {
  const primary = ruleFor('.button--primary').map((r) => r.decls).join(';');
  assert.match(primary, /forced-color-adjust:\s*none/);
  assert.match(primary, /background:\s*ButtonText/);
  const disabled = ruleFor(':root .button[aria-disabled="true"]').map((r) => r.decls).join(';');
  assert.match(disabled, /color:\s*GrayText/);
});
