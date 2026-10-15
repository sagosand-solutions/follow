// Plain-text rendering for question content.
// Content is plain text. Backtick pairs are display hints for inline code; nothing else is interpreted.
// Nodes are created with createElement/createTextNode only, never by parsing markup.

export function parseInline(str) {
  const s = String(str ?? '');
  const out = [];
  let text = '';
  const flush = () => {
    if (text) out.push({ kind: 'text', value: text });
    text = '';
  };
  const runEnd = (from) => {
    let k = from;
    while (s[k] === '`') k += 1;
    return k;
  };
  let i = 0;
  while (i < s.length) {
    if (s[i] !== '`') {
      text += s[i];
      i += 1;
      continue;
    }
    const end = runEnd(i);
    if (end - i !== 1) {
      // Runs of two or more backticks stay literal.
      text += s.slice(i, end);
      i = end;
      continue;
    }
    // Look for the next lone backtick on the same line.
    let j = i + 1;
    let close = -1;
    while (j < s.length && s[j] !== '\n') {
      if (s[j] === '`') {
        const e = runEnd(j);
        if (e - j === 1) {
          close = j;
          break;
        }
        j = e;
      } else {
        j += 1;
      }
    }
    if (close > i + 1) {
      flush();
      out.push({ kind: 'code', value: s.slice(i + 1, close) });
      i = close + 1;
    } else {
      text += '`';
      i += 1;
    }
  }
  flush();
  return out;
}

export function parseBlocks(str) {
  const lines = String(str ?? '').replace(/\r\n?/g, '\n').split('\n');
  const blocks = [];
  let current = [];
  const end = () => {
    if (current.length) {
      const p = current.map((l) => l.trim()).join(' ').trim();
      if (p) blocks.push(p);
    }
    current = [];
  };
  for (const line of lines) {
    if (line.trim() === '') end();
    else current.push(line);
  }
  end();
  return blocks;
}

function appendInline(parent, str, doc) {
  for (const seg of parseInline(str)) {
    if (seg.kind === 'code') {
      const code = doc.createElement('code');
      code.textContent = seg.value;
      parent.appendChild(code);
    } else {
      parent.appendChild(doc.createTextNode(seg.value));
    }
  }
}

export function renderText(str, doc = document) {
  const frag = doc.createDocumentFragment();
  for (const block of parseBlocks(str)) {
    const p = doc.createElement('p');
    appendInline(p, block, doc);
    frag.appendChild(p);
  }
  return frag;
}

export function renderInline(str, doc = document) {
  const frag = doc.createDocumentFragment();
  appendInline(frag, parseBlocks(str).join(' '), doc);
  return frag;
}
