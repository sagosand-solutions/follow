import { CONFIG } from './config.js';

const routes = [];
let mountEl = null;
let live = null;
let firstRender = true;
let token = 0;

export function registerRoute(pattern, viewFn) {
  const keys = [];
  const source = pattern
    .split('/')
    .filter((part) => part !== '')
    .map((part) => {
      if (part.startsWith(':')) {
        keys.push(part.slice(1));
        return '([^/]+)';
      }
      return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    })
    .join('/');
  routes.push({ pattern, viewFn, keys, regex: new RegExp(`^/${source}/?$`) });
}

export function matchRoute(list, path) {
  const clean = path.split('?')[0] || '/';
  for (const route of list) {
    const m = route.regex.exec(clean);
    if (!m) continue;
    const params = {};
    route.keys.forEach((key, i) => {
      try {
        params[key] = decodeURIComponent(m[i + 1]);
      } catch {
        params[key] = m[i + 1];
      }
    });
    return { viewFn: route.viewFn, params };
  }
  return null;
}

export function navigate(path) {
  location.hash = `#${path}`;
}

export function currentPath() {
  return location.hash.replace(/^#/, '') || '/';
}

let notFoundView = null;
export function setNotFound(viewFn) {
  notFoundView = viewFn;
}

async function render() {
  const mine = ++token;
  const path = currentPath();
  const match = matchRoute(routes, path);
  let result;
  try {
    result = match ? await match.viewFn(match.params) : await notFoundView({});
  } catch (err) {
    console.error(err);
    result = await notFoundView({ error: true });
  }
  if (mine !== token) return;
  mountEl.replaceChildren(result.node);
  document.title = result.title + (CONFIG.productName ? ` · ${CONFIG.productName}` : '');
  if (live) live.textContent = result.title;
  window.scrollTo(0, 0);
  if (!firstRender) {
    const heading = mountEl.querySelector('h1');
    if (heading) {
      heading.setAttribute('tabindex', '-1');
      heading.focus({ preventScroll: true });
    }
  }
  firstRender = false;
  if (result.after) result.after();
}

export function startRouter(mount, liveRegion) {
  mountEl = mount;
  live = liveRegion;
  window.addEventListener('hashchange', render);
  return render();
}

// Re-render the current route (used after state changes such as reset).
export function refresh() {
  return render();
}

