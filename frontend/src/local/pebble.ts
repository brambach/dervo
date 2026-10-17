/** The forest pebble's geometry, shared by the DervoMark component, message avatars and the app icon's source. From
 * the design sweep's brand/mark.js: the landing page's smile outline with its small bumps halved. */
export const PEBBLE='M4.3 45.3C4.7 43.0 5.3 41.1 5.9 39.1C6.4 37.1 6.9 35.3 7.7 33.1C8.5 31.0 9.4 28.4 10.4 26.5C11.4 24.5 12.3 23.0 13.8 21.4C15.3 19.8 17.5 18.0 19.2 16.8C21.0 15.5 22.7 14.7 24.3 13.7C26.0 12.7 27.5 11.8 29.3 10.8C31.2 9.8 33.4 8.5 35.3 7.7C37.3 6.9 38.9 6.4 40.9 6.0C43.0 5.6 45.7 5.3 47.8 5.3C49.9 5.2 51.7 5.4 53.7 5.6C55.6 5.9 57.4 6.3 59.4 6.9C61.4 7.6 63.8 8.7 65.8 9.6C67.7 10.5 69.1 11.3 71.0 12.3C73.0 13.3 75.5 14.5 77.4 15.7C79.3 16.9 81.0 18.0 82.5 19.5C84.0 21.0 85.3 22.6 86.4 24.6C87.6 26.6 88.7 29.3 89.6 31.5C90.5 33.7 91.2 35.4 91.8 37.7C92.4 40.0 93.1 42.9 93.3 45.3C93.5 47.7 93.3 49.8 93.0 52.0C92.6 54.2 92.0 56.2 91.2 58.5C90.5 60.8 89.3 63.5 88.3 65.7C87.3 67.9 86.5 69.6 85.2 71.7C83.9 73.7 82.2 76.2 80.5 78.0C78.8 79.8 77.1 81.0 75.2 82.3C73.3 83.5 71.4 84.4 69.2 85.4C67.0 86.5 64.3 87.6 62.1 88.5C59.8 89.4 58.1 90.2 55.7 90.8C53.3 91.4 50.2 92.0 47.8 92.1C45.3 92.2 43.2 91.9 40.9 91.5C38.7 91.2 36.6 90.6 34.2 90.0C31.8 89.4 28.8 88.6 26.4 87.8C24.0 86.9 22.0 86.2 19.9 84.8C17.9 83.4 15.5 81.2 13.8 79.3C12.2 77.3 11.2 75.3 10.1 73.2C9.0 71.2 8.2 69.3 7.3 67.0C6.3 64.7 5.3 61.9 4.7 59.5C4.1 57.2 3.7 55.3 3.7 53.0C3.6 50.6 3.9 47.6 4.3 45.3Z';
/** The pebble's viewBox: the outline's bounds, centred. */
export const PEBBLE_BOX='0.5 0.4 96 96';
/** The face as SVG markup in one colour: the original above 20px, the pixel-grid one at 20px and under. */
export function pebbleFace(size:number,colour:string):string{
  return size<=20
    ?`<g fill="${colour}"><ellipse cx="36.5" cy="42.4" rx="6" ry="6.5"/><ellipse cx="60.5" cy="36.4" rx="6" ry="6.5"/><path d="M32.5 60Q53.5 77 66 52.5" fill="none" stroke="${colour}" stroke-width="10" stroke-linecap="round"/></g>`
    :`<g transform="rotate(-12 49 50)" fill="${colour}"><ellipse cx="35" cy="39" rx="4" ry="5"/><ellipse cx="61" cy="37" rx="4" ry="5"/><path d="M30 56Q48 79 68 54" fill="none" stroke="${colour}" stroke-width="6" stroke-linecap="round"/></g>`;
}

/** The pebble as a standalone SVG in the token colours, for places that can't read CSS (a data URL avatar, the icon).
 * Pine with the cream face in light, the pale pine with dark ink in dark, as DervoTokens.css has them. */
export function pebbleSvg(size:number,dark:boolean):string{
  const body=dark?'#dfe8d6':'#22382b',face=dark?'#161a14':'#f8f8f5';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${PEBBLE_BOX}"><path fill="${body}" d="${PEBBLE}"/>${pebbleFace(size,face)}</svg>`;
}
