// The Readfirst logo: the code stage (a dark tile in every theme) with a highlighter caret at the start of the first line,
// and the wordmark "readfirst" as outlines, so it looks the same on every device. The outlines are JetBrains Mono Bold
// ("read") and Regular ("first"), SIL Open Font License 1.1. Colours come from the theme's tokens through the CSS
// classes; the favicon cannot read the page's CSS, so it gets the same tokens as values.
import { h, svgNode } from '../dom.js';

// The name the wordmark spells. Another product name in CONFIG shows as text instead.
export const WORDMARK_NAME = 'Readfirst';

const TILE = { x: 1, y: 1, width: 30, height: 30, rx: 8 };
const CARET = { x: 7, y: 8, width: 4, height: 16, rx: 1.5 };
const LINES = [{ x: 14, y: 10, width: 11, height: 3, rx: 1.5 }, { x: 14, y: 16, width: 7, height: 3, rx: 1.5 }];
const WORDMARK = {
  viewBox: '97 -777 4903 787',
  read: 'M97 0V-550H219V-443Q229 -497 268 -528Q308 -560 371 -560Q455 -560 502 -506Q549 -452 549 -355V-303H424V-349Q424 -399 398 -426Q372 -452 324 -452Q274 -452 248 -422Q222 -392 222 -337V0ZM861 10Q791 10 739 -17Q687 -44 658 -94Q630 -143 630 -210V-340Q630 -407 658 -456Q687 -506 739 -533Q791 -560 861 -560Q930 -560 982 -533Q1033 -506 1062 -458Q1090 -409 1090 -344V-244H750V-206Q750 -150 779 -122Q808 -93 861 -93Q900 -93 928 -106Q955 -120 960 -146H1084Q1071 -75 1010 -32Q949 10 861 10ZM750 -344V-325L970 -326V-345Q970 -402 942 -433Q914 -464 861 -464Q806 -464 778 -432Q750 -401 750 -344ZM1359 10Q1275 10 1226 -38Q1178 -85 1178 -162Q1178 -242 1233 -290Q1288 -337 1384 -337H1518V-376Q1518 -459 1421 -459Q1377 -459 1351 -442Q1325 -425 1322 -394H1202Q1206 -468 1264 -514Q1323 -560 1422 -560Q1527 -560 1585 -511Q1643 -462 1643 -373V0H1521V-103Q1514 -50 1471 -20Q1428 10 1359 10ZM1399 -92Q1454 -92 1486 -119Q1518 -146 1518 -191V-256H1390Q1349 -256 1325 -233Q1301 -210 1301 -174Q1301 -137 1326 -114Q1352 -92 1399 -92ZM1930 10Q1850 10 1800 -47Q1751 -104 1751 -200V-349Q1751 -446 1800 -503Q1849 -560 1930 -560Q1987 -560 2026 -531Q2066 -502 2082 -450L2078 -576V-730H2203V0H2081V-100Q2066 -48 2027 -19Q1988 10 1930 10ZM1977 -98Q2025 -98 2052 -127Q2078 -156 2078 -209V-341Q2078 -394 2052 -423Q2025 -452 1977 -452Q1929 -452 1902 -426Q1876 -400 1876 -346V-204Q1876 -150 1902 -124Q1929 -98 1977 -98Z',
  first: 'M2455 0V-393H2295V-475H2455V-590Q2455 -655 2496 -692Q2537 -730 2609 -730H2770V-648H2609Q2545 -648 2545 -590V-475H2770V-393H2545V0ZM2885 0V-82H3080V-468H2905V-550H3170V-82H3355V0ZM3115 -649Q3082 -649 3063 -666Q3044 -683 3044 -712Q3044 -742 3063 -760Q3082 -777 3115 -777Q3148 -777 3167 -760Q3186 -742 3186 -712Q3186 -683 3167 -666Q3148 -649 3115 -649ZM3472 0V-550H3562V-445H3564Q3571 -498 3609 -529Q3647 -560 3711 -560Q3797 -560 3844 -508Q3890 -457 3890 -362V-315H3800V-362Q3800 -482 3683 -482Q3624 -482 3593 -448Q3562 -414 3562 -350V0ZM4198 8Q4106 8 4056 -34Q4005 -75 4005 -151H4095Q4095 -113 4122 -92Q4149 -70 4198 -70H4242Q4292 -70 4320 -92Q4347 -114 4347 -154Q4347 -225 4282 -234L4130 -256Q4074 -264 4043 -302Q4012 -341 4012 -404Q4012 -476 4062 -517Q4111 -558 4198 -558H4242Q4322 -558 4372 -518Q4423 -479 4427 -415H4336Q4334 -444 4308 -463Q4283 -482 4242 -482H4198Q4152 -482 4126 -461Q4100 -440 4100 -404Q4100 -346 4153 -338L4295 -318Q4435 -298 4435 -154Q4435 -77 4384 -34Q4334 8 4242 8ZM4835 0Q4767 0 4726 -40Q4685 -79 4685 -145V-468H4527V-550H4685V-705H4775V-550H5000V-468H4775V-145Q4775 -117 4792 -100Q4808 -82 4835 -82H4995V0Z',
};

const svg = (viewBox, className, ...children) => svgNode('svg', { viewBox, class: className, 'aria-hidden': 'true', focusable: 'false' }, ...children);

export function brandMark() {
  return svg('0 0 32 32', 'brand__mark',
    svgNode('rect', { ...TILE, class: 'brand__tile' }),
    svgNode('rect', { ...CARET, class: 'brand__caret' }),
    ...LINES.map((line, i) => svgNode('rect', { ...line, class: `brand__line brand__line--${i + 1}` })));
}

export function wordmark() {
  return svg(WORDMARK.viewBox, 'brand__wordmark',
    svgNode('path', { d: WORDMARK.read, class: 'brand__read' }),
    svgNode('path', { d: WORDMARK.first, class: 'brand__first' }));
}

// Whether the header draws the wordmark for this product name.
export const drawsWordmark = (productName) => productName === WORDMARK_NAME;

// The header's home link: the mark, then the drawn wordmark with the name as its text for assistive technology, or, for
// any other name (or none, with the fallback), the name as text.
export function brandLink(productName, fallback) {
  const name = productName || fallback;
  return drawsWordmark(productName)
    ? h('a', { class: 'brand', href: '#/' }, brandMark(), wordmark(), h('span', { class: 'visually-hidden' }, name))
    : h('a', { class: 'brand', href: '#/' }, brandMark(), h('span', { class: 'brand__text' }, name));
}

const HEX = /^#[0-9a-f]{3}([0-9a-f]{3})?$/i;
const rect = (r, fill, extra = '') => `<rect x="${r.x}" y="${r.y}" width="${r.width}" height="${r.height}" rx="${r.rx}" fill="${fill}"${extra}/>`;

// The favicon as SVG text in the given colours (hex tokens: tile = --stage-bg, caret = --accent-attention, ink = --stage-ink),
// or null when a colour is not a plain hex value.
export function faviconSvg({ tile, caret, ink }) {
  if (![tile, caret, ink].every((c) => typeof c === 'string' && HEX.test(c))) return null;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">${rect(TILE, tile)}${rect(CARET, caret)}${rect(LINES[0], ink)}${rect(LINES[1], ink, ' opacity=".55"')}</svg>\n`;
}
