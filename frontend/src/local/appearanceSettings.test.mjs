import assert from 'node:assert/strict';
import {test, after} from 'node:test';
import {JSDOM} from 'jsdom';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url: 'http://localhost'});
for (const key of ['window', 'document', 'HTMLElement', 'Node']) globalThis[key] = dom.window[key];
Object.defineProperty(globalThis, 'navigator', {configurable: true, value: dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let systemDark = false;
const listeners = new Set();
window.matchMedia = () => ({matches: systemDark, addEventListener: (_, cb) => listeners.add(cb), removeEventListener: (_, cb) => listeners.delete(cb)});
const React = await import('react');
const {render, fireEvent, waitFor, cleanup, act} = await import('@testing-library/react');
const {ThemeProvider} = await import('@/shared/theme/ThemeProvider.tsx');
const {AppearanceSettings} = await import('./AppearanceSettings.tsx');
after(() => dom.window.close());
const mount = () => render(React.createElement(ThemeProvider, null, React.createElement(AppearanceSettings)));

const checked = (view, name) => view.getByRole('radio', {name}).getAttribute('aria-checked') === 'true';

test('appearance persists across remount and Match macOS tracks later operating system changes', async () => {
  window.localStorage.clear();
  window.localStorage.setItem('buzz-accent-color', '#c08050');
  let view = mount();
  try {
    await waitFor(() => assert.equal(checked(view, 'Match macOS'), true));
    fireEvent.click(view.getByRole('radio', {name: 'Dark'}));
    await waitFor(() => assert.equal(document.documentElement.classList.contains('dark'), true));
    assert.equal(window.localStorage.getItem('buzz-theme'), 'buzz-dark');
    assert.equal(window.localStorage.getItem('buzz-follow-system'), 'false');
    assert.equal(window.localStorage.getItem('buzz-accent-color'), '#c08050');
    cleanup();
    view = mount();
    await waitFor(() => assert.equal(checked(view, 'Dark'), true));
    fireEvent.click(view.getByRole('radio', {name: 'Light'}));
    await waitFor(() => assert.equal(document.documentElement.classList.contains('light'), true));
    fireEvent.click(view.getByRole('radio', {name: 'Match macOS'}));
    await act(async () => {systemDark = true; for (const listener of listeners) listener({matches: true});});
    await waitFor(() => assert.equal(document.documentElement.classList.contains('dark'), true));
    assert.equal(checked(view, 'Match macOS'), true);
    assert.equal(window.localStorage.getItem('buzz-follow-system'), 'true');
    assert.equal(view.queryByRole('switch', {name: 'Glass window'}), null, 'glass is Mac builds only');
  } finally {cleanup();}
});

test('the theme tiles are one radio group: one tab stop, and the arrows move and choose', async () => {
  window.localStorage.clear();
  const view = mount();
  try {
    await waitFor(() => assert.equal(checked(view, 'Match macOS'), true));
    const tiles = view.getAllByRole('radio');
    assert.deepEqual(tiles.map(tile => tile.tabIndex), [-1, -1, -1, 0], 'only the chosen tile is in the tab order');
    fireEvent.keyDown(tiles[3], {key: 'ArrowLeft'});
    await waitFor(() => assert.equal(checked(view, 'Dervo'), true));
    fireEvent.keyDown(view.getByRole('radio', {name: 'Dervo'}), {key: 'ArrowLeft'});
    await waitFor(() => assert.equal(checked(view, 'Dark'), true));
    assert.equal(document.activeElement, view.getByRole('radio', {name: 'Dark'}));
    fireEvent.keyDown(view.getByRole('radio', {name: 'Dark'}), {key: 'Home'});
    await waitFor(() => assert.equal(checked(view, 'Light'), true));
  } finally {cleanup();}
});

test('Dervo is Dark with the forest palette: it persists, and Dark or Light takes the palette off', async () => {
  window.localStorage.clear();
  document.documentElement.removeAttribute('data-dervo-palette');
  let view = mount();
  try {
    await waitFor(() => assert.equal(checked(view, 'Match macOS'), true));
    fireEvent.click(view.getByRole('radio', {name: 'Dervo'}));
    await waitFor(() => assert.equal(checked(view, 'Dervo'), true));
    assert.equal(document.documentElement.classList.contains('dark'), true);
    assert.equal(document.documentElement.getAttribute('data-dervo-palette'), 'forest');
    assert.equal(window.localStorage.getItem('dervo-palette'), 'forest');
    assert.equal(window.localStorage.getItem('buzz-theme'), 'buzz-dark');
    assert.equal(window.localStorage.getItem('buzz-follow-system'), 'false');
    assert.equal(checked(view, 'Dark'), false, 'Dark and Dervo are separate choices');
    cleanup();
    view = mount();
    await waitFor(() => assert.equal(checked(view, 'Dervo'), true));
    fireEvent.click(view.getByRole('radio', {name: 'Dark'}));
    await waitFor(() => assert.equal(checked(view, 'Dark'), true));
    assert.equal(document.documentElement.getAttribute('data-dervo-palette'), null);
    assert.equal(window.localStorage.getItem('dervo-palette'), null);
    assert.equal(document.documentElement.classList.contains('dark'), true);
    // Negative control: Light after Dervo leaves no palette behind either.
    fireEvent.click(view.getByRole('radio', {name: 'Dervo'}));
    await waitFor(() => assert.equal(checked(view, 'Dervo'), true));
    fireEvent.click(view.getByRole('radio', {name: 'Light'}));
    await waitFor(() => assert.equal(checked(view, 'Light'), true));
    assert.equal(window.localStorage.getItem('dervo-palette'), null);
  } finally {cleanup();}
});

test('on Windows the system tile says Match Windows, and still follows the system', async () => {
  window.localStorage.clear();
  Object.defineProperty(navigator, 'platform', {configurable: true, value: 'Win32'});
  const view = mount();
  try {
    await waitFor(() => assert.equal(checked(view, 'Match Windows'), true));
    assert.equal(view.queryByText(/macOS/), null);
    fireEvent.click(view.getByRole('radio', {name: 'Dark'}));
    await waitFor(() => assert.equal(checked(view, 'Dark'), true));
    fireEvent.click(view.getByRole('radio', {name: 'Match Windows'}));
    await waitFor(() => assert.equal(window.localStorage.getItem('buzz-follow-system'), 'true'));
  } finally {cleanup(); delete navigator.platform;}
  assert.notEqual(navigator.platform, 'Win32');
});
