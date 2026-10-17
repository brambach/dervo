import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const config=JSON.parse(readFileSync(new URL('./tauri.conf.json',import.meta.url)));
test('native titlebar overlays glass without debug title or custom traffic lights',()=>{
 const window=config.app.windows[0];
 assert.equal(window.title,'Dervo');
 assert.equal(window.titleBarStyle,'Overlay');
 assert.equal(window.hiddenTitle,true);
 assert.notEqual(window.decorations,false);
 assert.equal(window.transparent,false);
});
test('sidebar reserves traffic-light clearance and permits native dragging',()=>{
 const source=readFileSync(new URL('./frontend/src/local/ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(source,/<AppTopChrome /);
 const chrome=readFileSync(new URL('./frontend/src/app/AppTopChrome.tsx',import.meta.url),'utf8');
 assert.match(chrome,/pl-\[80px\]/);
 assert.match(chrome,/data-tauri-drag-region/);
 const permissions=JSON.parse(readFileSync(new URL('./capabilities/main.json',import.meta.url)));
 assert.deepEqual(permissions.windows,['main']);
 assert.ok(permissions.permissions.includes('core:window:allow-start-dragging'));
});

test('renderer storage is selected before any configured window opens',()=>{
 assert.ok(config.app.windows.length>0);
 for(const window of config.app.windows)assert.equal(window.create,false,'automatic windows open the shared store too early');
 const source=readFileSync(new URL('./src/main.rs',import.meta.url),'utf8');
 assert.match(source,/WebviewWindowBuilder::from_config/);
 assert.match(source,/\.incognito\(std::env::var_os\(launch::DATA_DIR_VARIABLE\)\.is_some\(\)\)/);
 assert.ok(source.indexOf('.incognito(')<source.indexOf('.build()?'),'storage selection precedes WebView creation');
});
test('everyday renderer storage and draft migration source remain intact',()=>{
 assert.equal(config.identifier,'local.dervo.connected');
 assert.notEqual(config.app.windows[0].incognito,true,'everyday launches must retain their persistent store');
 const source=readFileSync(new URL('./frontend/src/main.tsx',import.meta.url),'utf8');
 assert.match(source,/initDraftStore\('local-user','dervo-local'\)/,'retain existing draft keys in the everyday store');
});
