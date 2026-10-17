/** Terminal input belongs to its shell, including Escape and Control key combinations. */
export function isProjectTerminalKey(event:{target:EventTarget|null}):boolean{
 return typeof Element!=='undefined'&&event.target instanceof Element&&!!event.target.closest('[data-project-terminal]');
}
