// Header menu for narrow screens: a disclosure button (not an ARIA menu) that shows or hides the navigation list.
// On wide screens the button is hidden by CSS and the list is shown as it always was.
// Rules: Esc closes and returns focus to the button; a route change closes it (the router then moves focus);
// crossing the breakpoint closes it, and the Continue link moves so that the reading order always matches what is seen.

// wireNavMenu({ nav, toggle, list, continueSlot, narrow, win })
//   narrow   a MediaQueryList for the narrow layout (or anything with .matches and addEventListener('change'))
//   win      the window (hashchange)
// Returns { isOpen, open, close } for tests.
export function wireNavMenu({ nav, toggle, list, continueSlot, narrow, win = globalThis }) {
  let openState = false;
  const render = () => {
    toggle.setAttribute('aria-expanded', openState ? 'true' : 'false');
    nav.setAttribute('data-open', openState ? 'true' : 'false');
  };
  const open = () => {
    openState = true;
    render();
  };
  const close = ({ focusToggle = false } = {}) => {
    const wasOpen = openState;
    openState = false;
    render();
    if (wasOpen && focusToggle) toggle.focus();
  };
  // Narrow: Continue sits in the bar, before the button. Wide: it sits in the list after About, as before.
  const place = () => {
    if (narrow.matches) nav.insertBefore(continueSlot, toggle);
    else list.insertBefore(continueSlot, list.querySelector('.nav__account'));
  };

  toggle.addEventListener('click', () => (openState ? close() : open()));
  nav.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && openState) {
      event.preventDefault();
      close({ focusToggle: true });
    }
  });
  win.addEventListener('hashchange', () => close());
  narrow.addEventListener('change', () => {
    close();
    place();
  });
  nav.setAttribute('data-collapsible', '');
  place();
  render();
  return { isOpen: () => openState, open, close };
}
