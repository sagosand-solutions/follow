// One symbol per course: our own line icons, drawn on a 24-unit grid with the same stroke (1.75, round caps and joins),
// so they read alike at 32–48 px in every theme. They are decoration (the course name is always written next to them).
// The progress badge puts a thin ring around the symbol that fills with the share of the course's questions answered.
import { h, svgNode } from '../dom.js';

const STROKE = 1.75;

// Each icon is a list of [tag, attributes]. Fill is none unless an attribute says otherwise.
export const COURSE_ICONS = {
  // Read like a computer: an eye, and an arrow that follows the lines of code from the top down.
  'read-like-a-computer': [
    ['path', { d: 'M21 8.5C18.4 4.3 5.6 4.3 3 8.5C5.6 12.7 18.4 12.7 21 8.5Z' }],
    ['circle', { cx: 12, cy: 8.5, r: 1.9 }],
    ['path', { d: 'M4.5 14.5V20.5M2.8 18.8L4.5 20.5L6.2 18.8' }],
    ['path', { d: 'M9 15.5H19M9 19.5H15.5' }],
  ],
  // Small programs from everyday life: a shopping list with two items ticked off.
  'small-programs': [
    ['rect', { x: 5, y: 4, width: 14, height: 17.5, rx: 2 }],
    ['rect', { x: 9, y: 2.5, width: 6, height: 3, rx: 1 }],
    ['path', { d: 'M8 10.6L9.3 11.9L11.4 9.6M13.5 11H16M8 15.6L9.3 16.9L11.4 14.6M13.5 16H16' }],
  ],
  // Python: reading code. A snake that winds down like a flow and ends as an arrow (one snake, no logo).
  python: [
    ['path', { d: 'M5.5 5H13A3.5 3.5 0 0 1 13 12H11A3.5 3.5 0 0 0 11 19H19.5M17 16.5L19.5 19L17 21.5' }],
    ['circle', { cx: 4.2, cy: 5, r: 1.4, fill: 'currentColor', stroke: 'none' }],
  ],
  // Production patterns: a gear with an hourglass (timeouts and retries).
  production: [
    ['path', { d: 'M19.11 9.96L21.45 10.33L21.45 13.67L19.11 14.04L18.47 15.59L19.86 17.51L17.51 19.86L15.59 18.47L14.04 19.11L13.67 21.45L10.33 21.45L9.96 19.11L8.41 18.47L6.49 19.86L4.14 17.51L5.53 15.59L4.89 14.04L2.55 13.67L2.55 10.33L4.89 9.96L5.53 8.41L4.14 6.49L6.49 4.14L8.41 5.53L9.96 4.89L10.33 2.55L13.67 2.55L14.04 4.89L15.59 5.53L17.51 4.14L19.86 6.49L18.47 8.41Z' }],
    ['path', { d: 'M9.6 8.2H14.4M9.6 15.8H14.4M10.1 8.2L13.9 15.8M13.9 8.2L10.1 15.8' }],
  ],
  // SQL: two overlapping tables, drawn as circles (a join).
  sql: [
    ['circle', { cx: 9, cy: 12, r: 6 }],
    ['circle', { cx: 15, cy: 12, r: 6 }],
  ],
  // Courses not published yet, drawn in the same style; not used on the page until the course exists.
  git: [
    ['circle', { cx: 7, cy: 5, r: 2 }],
    ['circle', { cx: 7, cy: 19, r: 2 }],
    ['circle', { cx: 17, cy: 8, r: 2 }],
    ['path', { d: 'M7 7V17M17 10C17 14 7 13 7 17' }],
  ],
  http: [
    ['circle', { cx: 12, cy: 12, r: 8.5 }],
    ['path', { d: 'M20.5 12H3.5M12 3.5C9.4 6.2 9.4 17.8 12 20.5M12 3.5C14.6 6.2 14.6 17.8 12 20.5' }],
  ],
  testing: [
    ['path', { d: 'M9.5 3H14.5M10.5 3V9L5.2 18.4A1.8 1.8 0 0 0 6.8 21H17.2A1.8 1.8 0 0 0 18.8 18.4L13.5 9V3' }],
    ['path', { d: 'M9.3 16.2L11.1 18L14.7 14.2' }],
  ],
  regex: [
    ['circle', { cx: 6, cy: 18, r: 1.4, fill: 'currentColor', stroke: 'none' }],
    ['path', { d: 'M15.5 5V15M11.2 7.5L19.8 12.5M19.8 7.5L11.2 12.5' }],
  ],
  review: [
    ['path', { d: 'M4 6H13M4 10H9M4 14H8' }],
    ['circle', { cx: 15, cy: 14, r: 4.2 }],
    ['path', { d: 'M18 17L21 20' }],
  ],
  // Any course without its own symbol: code brackets.
  course: [
    ['path', { d: 'M9 6.5L3.5 12L9 17.5M15 6.5L20.5 12L15 17.5' }],
  ],
};

// The courses on the page today, and the path's coming-soon steps (which have no course id yet).
const PUBLISHED = new Set(['read-like-a-computer', 'small-programs', 'python', 'production', 'sql']);
export const iconName = (id) => (PUBLISHED.has(id) ? id : 'course');

function shapes(name) {
  return (COURSE_ICONS[name] || COURSE_ICONS.course).map(([tag, attrs]) => svgNode(tag, attrs));
}

// The symbol alone (decorative).
export function courseIcon(id) {
  return svgNode('svg', { viewBox: '0 0 24 24', class: `course-icon course-icon--${iconName(id)}`, fill: 'none', stroke: 'currentColor', 'stroke-width': STROKE, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false' }, ...shapes(iconName(id)));
}

// The symbol inside a progress ring. With `label` the badge is an image with that name (it replaces the written count);
// without one it is decoration. The ring's track is thin and its filled arc thicker, so progress shows by form as well
// as colour.
export function progressBadge(id, { answered = 0, total = 0, label = null } = {}) {
  const share = total > 0 ? Math.min(1, Math.max(0, answered / total)) : 0;
  const ring = [svgNode('circle', { class: 'progress-ring__track', cx: 24, cy: 24, r: 22, fill: 'none' })];
  if (share > 0) ring.push(svgNode('circle', { class: 'progress-ring__fill', cx: 24, cy: 24, r: 22, fill: 'none', pathLength: 1, 'stroke-dasharray': `${share} 1`, transform: 'rotate(-90 24 24)' }));
  const icon = svgNode('g', { class: `course-icon course-icon--${iconName(id)}`, transform: 'translate(10 10) scale(1.1667)', fill: 'none', stroke: 'currentColor', 'stroke-width': STROKE, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, ...shapes(iconName(id)));
  const svg = svgNode('svg', { viewBox: '0 0 48 48', class: 'progress-ring', 'aria-hidden': 'true', focusable: 'false' }, ...ring, icon);
  const state = `${share > 0 ? ' course-badge--started' : ''}${share >= 1 ? ' course-badge--done' : ''}`;
  return h('span', { class: `course-badge${state}`, ...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': 'true' }) }, svg);
}
