// Small hand-written Python tokenizer for display purposes only.
// Invariant: for every line, the token texts concatenate to the original line.

const KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
  'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in',
  'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
]);

const BUILTINS = new Set([
  'print', 'len', 'range', 'int', 'float', 'str', 'bool', 'list', 'dict', 'set', 'tuple', 'sum', 'min',
  'max', 'abs', 'sorted', 'reversed', 'enumerate', 'zip', 'map', 'filter', 'any', 'all', 'isinstance',
  'type', 'input', 'open', 'id', 'iter', 'next',
]);

const PREFIX = /^(?:r|b|u|f|rb|br|fr|rf)$/i;
const WORD = /[A-Za-z_][A-Za-z0-9_]*/y;
const NUMBER = /(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?[jJ]?)/y;
const DECORATOR = /@[A-Za-z_][A-Za-z0-9_.]*/y;

export function splitLines(source) {
  const lines = String(source ?? '').replace(/\r\n/g, '\n').split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

export function lineCount(source) {
  return splitLines(source).length;
}

function matchAt(re, line, i) {
  re.lastIndex = i;
  const m = re.exec(line);
  return m && m.index === i ? m[0] : null;
}

// Index just after the closing delimiter of a string, or -1 if it does not close on this line.
function findClose(line, from, delim) {
  let j = from;
  while (j < line.length) {
    if (line[j] === '\\') {
      j += 2;
    } else if (line.startsWith(delim, j)) {
      return j + delim.length;
    } else {
      j += 1;
    }
  }
  return -1;
}

function tokenizeLine(line, state) {
  const tokens = [];
  const push = (type, text) => {
    if (!text) return;
    const last = tokens[tokens.length - 1];
    if (last && last.type === type && type === 'plain') last.text += text;
    else tokens.push({ type, text });
  };

  let i = 0;
  if (state.triple) {
    const end = findClose(line, 0, state.triple);
    if (end === -1) {
      push('string', line);
      return tokens;
    }
    push('string', line.slice(0, end));
    state.triple = null;
    i = end;
  }

  let expectName = false;
  while (i < line.length) {
    const c = line[i];

    if (c === '#') {
      push('comment', line.slice(i));
      return tokens;
    }

    if (c === '"' || c === "'") {
      i = readString(line, i, i, state, push);
      expectName = false;
      continue;
    }

    const word = /[A-Za-z_]/.test(c) ? matchAt(WORD, line, i) : null;
    if (word) {
      const next = line[i + word.length];
      if (word.length <= 2 && PREFIX.test(word) && (next === '"' || next === "'")) {
        i = readString(line, i, i + word.length, state, push);
        expectName = false;
        continue;
      }
      if (expectName) push('function', word);
      else if (KEYWORDS.has(word)) push('keyword', word);
      else if (BUILTINS.has(word) && line[i - 1] !== '.') push('builtin', word);
      else push('plain', word);
      expectName = word === 'def' || word === 'class';
      i += word.length;
      continue;
    }

    if (/[0-9]/.test(c) || (c === '.' && /[0-9]/.test(line[i + 1] ?? ''))) {
      const num = matchAt(NUMBER, line, i);
      if (num) {
        push('number', num);
        i += num.length;
        expectName = false;
        continue;
      }
    }

    if (c === '@' && line.slice(0, i).trim() === '') {
      const deco = matchAt(DECORATOR, line, i);
      if (deco) {
        push('decorator', deco);
        i += deco.length;
        continue;
      }
    }

    if (c !== ' ' && c !== '\t') expectName = false;
    push('plain', c);
    i += 1;
  }
  return tokens;
}

// Reads a string literal starting with an optional prefix at `start`; the quote is at `quoteAt`.
function readString(line, start, quoteAt, state, push) {
  const q = line[quoteAt];
  const triple = q.repeat(3);
  if (line.startsWith(triple, quoteAt)) {
    const end = findClose(line, quoteAt + 3, triple);
    if (end === -1) {
      state.triple = triple;
      push('string', line.slice(start));
      return line.length;
    }
    push('string', line.slice(start, end));
    return end;
  }
  const end = findClose(line, quoteAt + 1, q);
  if (end === -1) {
    push('string', line.slice(start));
    return line.length;
  }
  push('string', line.slice(start, end));
  return end;
}

export function tokenizePython(source) {
  const state = { triple: null };
  return splitLines(source).map((line) => tokenizeLine(line, state));
}
