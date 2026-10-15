import test from 'node:test';
import assert from 'node:assert/strict';
import { parseInline, parseBlocks, renderText, renderInline } from '../public/js/text.js';

// Minimal fake document: enough to build and inspect trees.
function node(type, tag) {
  return {
    type, tag, children: [], text: '',
    appendChild(c) { this.children.push(c); return c; },
    set textContent(v) { this.text = v; },
    get textContent() { return this.text; },
  };
}
const doc = {
  createElement: (tag) => node('el', tag),
  createTextNode: (text) => ({ type: 'text', text }),
  createDocumentFragment: () => node('frag', null),
};
const shape = (n) => (n.type === 'text' ? n.text : n.type === 'el' && n.tag === 'code' ? `<code>${n.text}</code>` : `${n.tag ?? 'frag'}[${n.children.map(shape).join('')}]`);

const roundTrip = (segs) => segs.map((s) => (s.kind === 'code' ? `\`${s.value}\`` : s.value)).join('');

test('parseInline: plain text', () => {
  assert.deepEqual(parseInline('hello'), [{ kind: 'text', value: 'hello' }]);
  assert.deepEqual(parseInline(''), []);
});

test('parseInline: code span', () => {
  assert.deepEqual(parseInline('use `range(1, n)` here'), [
    { kind: 'text', value: 'use ' },
    { kind: 'code', value: 'range(1, n)' },
    { kind: 'text', value: ' here' },
  ]);
});

test('parseInline: lone, empty and double backticks stay literal', () => {
  for (const s of ['a ` b', '``', 'x `` y', '`', 'a `b', 'a ``b`` c']) {
    assert.equal(roundTrip(parseInline(s)), s);
    assert.ok(parseInline(s).every((seg) => seg.kind === 'text'), s);
  }
});

test('parseInline: no empty segments and round trip', () => {
  for (const s of ['`a``b`', '`a` `b`', 'x`y`z', '``a`', '`a\nb`', 'ünï `cödé` 日本']) {
    const segs = parseInline(s);
    assert.ok(segs.every((x) => x.value !== ''));
    assert.equal(roundTrip(segs), s);
  }
});

test('parseInline: markup is not interpreted', () => {
  const segs = parseInline('<script>alert(1)</script> &amp; `<b>`');
  assert.equal(segs[0].value, '<script>alert(1)</script> &amp; ');
  assert.deepEqual(segs[1], { kind: 'code', value: '<b>' });
});

test('parseBlocks', () => {
  assert.deepEqual(parseBlocks('a\nb\n\nc'), ['a b', 'c']);
  assert.deepEqual(parseBlocks('\n\n  a  \n \t \n\n\n b\r\nc\r\n'), ['a', 'b c']);
  assert.deepEqual(parseBlocks('   \n\n'), []);
  assert.deepEqual(parseBlocks(''), []);
});

test('renderText and renderInline build nodes only', () => {
  assert.equal(shape(renderText('one `x`\n\ntwo', doc)), 'frag[p[one <code>x</code>]p[two]]');
  assert.equal(shape(renderInline('a\nb `c`', doc)), 'frag[a b <code>c</code>]');
});

test('large input is processed in linear time', () => {
  const big = 'a `b` '.repeat(20000);
  const t0 = Date.now();
  parseInline(big);
  parseBlocks(big);
  assert.ok(Date.now() - t0 < 500);
});
