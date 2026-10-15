import test from 'node:test';
import assert from 'node:assert/strict';
import { tokenizePython, lineCount, splitLines } from '../public/js/highlight.js';

const SNIPPETS = [
  'x = 1',
  'print("hello")',
  "s = 'it\\'s'",
  'd = "a \\" b"  # trailing',
  '# a comment with a "quote" and \'another\'',
  's = "has # inside"  # real comment',
  'def f(a, b=2):\n    return a + b\n',
  'class Foo(Base):\n    def method(self):\n        pass\n',
  '@decorator\ndef g(): pass',
  '@pkg.mod.deco(arg)\ndef g(): pass',
  'x = """multi\nline\nstring""" + "after"',
  "y = '''a\n'quoted'\nb'''",
  'z = "unterminated',
  'w = \'unterminated too',
  'n = 0x1F + 0o17 + 0b101 + 1_000 + 3.14 + .5 + 1e-3 + 2j',
  'text = f"value={x!r}"\nraw = r"\\d+"\nb = b"bytes"\nrb = Rb"x"',
  'if a and not b or c is None:\n    pass\nelif d in e:\n    pass\nelse:\n    pass',
  'for i, x in enumerate(items):\n    total += x',
  'obj.print(1)\nprint(2)',
  'lambda x: x ** 2',
  'tab\tseparated\t= 1',
  's = "héllo wörld"  # 日本語のコメント',
  'a = [1, 2, 3][::-1]\nb = {"k": (1, 2)}',
  '\n',
  '',
  'x = 1\r\ny = 2\r\n',
  '   \n\t\n',
  'import os\nfrom a.b import c as d',
  'try:\n    pass\nexcept (A, B) as e:\n    raise\nfinally:\n    pass',
  'async def f():\n    await g()',
  'while True:\n    break\n    continue',
  '"""docstring"""\nx = 1',
  'x = "\\\\"',
];

function concat(tokens) {
  return tokens.map((t) => t.text).join('');
}

test('token texts reproduce every line exactly', () => {
  for (const src of SNIPPETS) {
    const lines = splitLines(src);
    const toks = tokenizePython(src);
    assert.equal(toks.length, lines.length, JSON.stringify(src));
    toks.forEach((l, k) => assert.equal(concat(l), lines[k], JSON.stringify(src)));
  }
});

test('invariant holds on random concatenations', () => {
  let seed = 12345;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  for (let n = 0; n < 300; n += 1) {
    let src = '';
    for (let k = 0; k < 1 + Math.floor(rnd() * 6); k += 1) src += SNIPPETS[Math.floor(rnd() * SNIPPETS.length)] + (rnd() < 0.5 ? '\n' : '');
    const lines = splitLines(src);
    const toks = tokenizePython(src);
    assert.equal(toks.length, lines.length);
    toks.forEach((l, i) => assert.equal(concat(l), lines[i]));
  }
});

test('empty and newline-only sources', () => {
  assert.deepEqual(tokenizePython(''), []);
  assert.equal(lineCount(''), 0);
  assert.deepEqual(tokenizePython('\n'), [[]]);
  assert.equal(lineCount('\n'), 1);
  assert.equal(lineCount('a\nb\n'), 2);
  assert.equal(lineCount('a\nb'), 2);
  assert.equal(lineCount('a\r\nb\r\n'), 2);
});

const types = (src, line = 0) => tokenizePython(src)[line].filter((t) => t.type !== 'plain').map((t) => `${t.type}:${t.text}`);

test('keywords, builtins, functions, numbers, decorators', () => {
  assert.deepEqual(types('def f(x): return len(x)'), ['keyword:def', 'function:f', 'keyword:return', 'builtin:len']);
  assert.deepEqual(types('class Foo: pass'), ['keyword:class', 'function:Foo', 'keyword:pass']);
  assert.deepEqual(types('x = 0x1F + 1_000 + 2.5e3 + 3j'), ['number:0x1F', 'number:1_000', 'number:2.5e3', 'number:3j']);
  assert.deepEqual(types('@a.b\ndef f(): pass'), ['decorator:@a.b']);
  assert.deepEqual(types('obj.print(1)'), ['number:1']);
  assert.deepEqual(types('x = True or None'), ['keyword:True', 'keyword:or', 'keyword:None']);
});

test('strings and comments', () => {
  assert.deepEqual(types('s = "a # b"  # c'), ['string:"a # b"', 'comment:# c']);
  assert.deepEqual(types('s = "a \\" b"'), ['string:"a \\" b"']);
  assert.deepEqual(types('# say "hi"'), ['comment:# say "hi"']);
  assert.deepEqual(types('x = f"{a}"'), ['string:f"{a}"']);
  assert.deepEqual(types('x = rb"\\d"'), ['string:rb"\\d"']);
  assert.deepEqual(types('x = "unterminated'), ['string:"unterminated']);
});

test('triple-quoted strings span lines', () => {
  const t = tokenizePython('x = """a\nb\nc""" + 1');
  assert.equal(t[0].at(-1).type, 'string');
  assert.deepEqual(t[1], [{ type: 'string', text: 'b' }]);
  assert.equal(t[2][0].type, 'string');
  assert.ok(t[2].some((tok) => tok.type === 'number'));
});

test('identifiers that merely contain a keyword are plain', () => {
  assert.deepEqual(types('iffy = form + classy'), []);
});

test('a very long line is fast', () => {
  const src = 'x = ' + '"abc" + 12 + name + '.repeat(500) + '1';
  const t0 = Date.now();
  tokenizePython(src);
  assert.ok(Date.now() - t0 < 50);
});
