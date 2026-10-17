// The unreachable-component check (20 Sep). Each rule has a test and a negative control, and the last test is the one
// that matters: run the real check over a tree shaped like the day QuestionTray, ProjectCatchup and its opener were all
// invisible, and it has to name them.
import assert from "node:assert/strict";
import { test } from "node:test";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { ALLOWED, componentsIn, importsIn, resolveFrom, scanProject, usedInOwnFile } from "./check-unreachable.mjs";

test("a component is an exported capitalised function in a .tsx file, and nothing else is", () => {
  assert.deepEqual(componentsIn("export function QuestionTray({question}){return <div/>;}"), ["QuestionTray"]);
  assert.deepEqual(componentsIn("export default function App(){return null;}"), ["App"]);
  assert.deepEqual(componentsIn("export async function Slow(){}"), ["Slow"]);
  assert.deepEqual(componentsIn("function Inner(){}\nexport {Inner};"), ["Inner"], "declared quietly, exported at the foot");
  // The controls: a helper, a constant, a type, a re-export of something that isn't a function here.
  assert.deepEqual(componentsIn("export function pendingQuestion(entries){return null;}"), [], "lower case is a helper");
  assert.deepEqual(componentsIn("export const LIMITS={ask:200};"), [], "a constant is not a component");
  assert.deepEqual(componentsIn("export type Question={ask:string};"), []);
  assert.deepEqual(componentsIn("export {answerFor};"), [], "a re-export this file doesn't declare as a function");
});

test("a component its own file renders is on a screen already, whatever its export says", () => {
  const list = "export function AccountRow(){return <li/>;}\nexport function Accounts(){return <ul>{rows.map(r=><AccountRow key={r.id}/>)}</ul>;}";
  assert.equal(usedInOwnFile(list, "AccountRow"), true);
  assert.equal(usedInOwnFile(list, "Accounts"), false, "the file never renders itself");
  assert.equal(usedInOwnFile("export function Lazy(){return null;}\nconst R=React.lazy(()=>Lazy);", "Lazy"), true, "handed on as a value");
  assert.equal(usedInOwnFile("export function QuestionTray(){return <div/>;}", "QuestionTray"), false);
});

test("imports are read by name, and an alias or a dynamic import still counts", () => {
  assert.deepEqual(importsIn("import {QuestionTray} from './Questions';"), [["QuestionTray", "./Questions"]]);
  assert.deepEqual(importsIn("import type {Idea, IdeaListing} from './ProjectRoom';"),
    [["Idea", "./ProjectRoom"], ["IdeaListing", "./ProjectRoom"]], "a type import is an import of that name, not of the screen beside it");
  assert.deepEqual(importsIn("import Home from './Home';"), [["default", "./Home"]]);
  assert.deepEqual(importsIn("import * as all from './Home';"), [["*", "./Home"]]);
  assert.deepEqual(importsIn("const m=await import('./Home');"), [["*", "./Home"]], "React.lazy reaches in a way this can't read, so it counts for everything");
  assert.deepEqual(importsIn("export {QuestionTray} from './Questions';"), [["QuestionTray", "./Questions"]]);
  assert.deepEqual(importsIn("import {useState} from 'react';"), [["useState", "react"]]);
  assert.equal(resolveFrom("frontend/src/main.tsx", "@/local/ConnectedApp"), "frontend/src/local/ConnectedApp",
    "tsconfig's @/ alias, or main.tsx would have made the whole app read as unreachable");
  assert.equal(resolveFrom("frontend/src/local/Home.tsx", "./Questions"), "frontend/src/local/Questions");
  assert.equal(resolveFrom("frontend/src/local/Home.tsx", "react"), null, "a package is not a file in this tree");
});

/** A tree in the shape of the real one: src/local with the components, src/main.tsx reaching in through the alias. */
async function tree(files) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "unreachable-"));
  for (const [name, text] of Object.entries(files)) {
    await fs.mkdir(path.join(root, path.dirname(name)), { recursive: true });
    await fs.writeFile(path.join(root, name), text);
  }
  return root;
}
const names = (rows) => rows.map((row) => row.name).sort();

test("the day three finished components were invisible: the check names each one, and the tests do not save them", async () => {
  const root = await tree({
    "frontend/src/main.tsx": "import {ConnectedApp} from '@/local/ConnectedApp';\nexport const app=<ConnectedApp/>;",
    "frontend/src/local/ConnectedApp.tsx": "import {Home} from './Home';\nexport function ConnectedApp(){return <Home/>;}",
    "frontend/src/local/Home.tsx": "export function Home(){return <div/>;}",
    // Built, tested, reviewed, mounted by nothing.
    "frontend/src/local/Questions.tsx": "export function QuestionTray(){return <div/>;}",
    "frontend/src/local/ProjectCatchup.tsx": "export function ProjectCatchup(){return <main/>;}",
    "frontend/src/local/CatchupOpener.tsx": "export function CatchupOpener(){return <button/>;}",
    // Their own tests import them directly, which is exactly why the suites were green.
    "frontend/src/local/questions.test.mjs": "import {QuestionTray} from './Questions.tsx';",
    "frontend/src/local/catchup.test.mjs": "import {ProjectCatchup} from './ProjectCatchup.tsx';\nimport {CatchupOpener} from './CatchupOpener.tsx';",
  });
  const { unreachable } = await scanProject(root, new Map());
  assert.deepEqual(names(unreachable), ["CatchupOpener", "ProjectCatchup", "QuestionTray"]);
  await fs.rm(root, { recursive: true, force: true });
});

test("the controls: one import from the app is enough, and a helper, a type import or a .ts file is not a component", async () => {
  const root = await tree({
    "frontend/src/main.tsx": "import {ConnectedApp} from '@/local/ConnectedApp';\nexport const app=<ConnectedApp/>;",
    // Home is wired now, exactly as this branch wired it.
    "frontend/src/local/ConnectedApp.tsx": "import {Home} from './Home';\nexport function ConnectedApp(){return <Home/>;}",
    "frontend/src/local/Home.tsx": "import {QuestionTray} from './Questions';\nimport {pendingQuestion} from './questionShape';\n"
      + "export function Home(){return <QuestionTray question={pendingQuestion([])}/>;}",
    "frontend/src/local/Questions.tsx": "export function QuestionTray(){return <div/>;}",
    // A .ts file holds the rules, not a screen, so nothing in it is ever a component.
    "frontend/src/local/questionShape.ts": "export function pendingQuestion(){return null;}\nexport function TRAY(){return null;}",
    // A file whose types are imported but whose screen is not: the ProjectRoom shape.
    "frontend/src/local/ProjectRoom.tsx": "export type Idea={id:string};\nexport function ProjectRoom(){return <section/>;}",
    "frontend/src/local/RoomIdeas.tsx": "import type {Idea} from './ProjectRoom';\nexport function RoomIdeas(props:{idea:Idea}){return <div/>;}",
    "frontend/src/local/questions.test.mjs": "import {QuestionTray} from './Questions.tsx';",
  });
  const { unreachable } = await scanProject(root, new Map());
  assert.deepEqual(names(unreachable), ["ProjectRoom", "RoomIdeas"],
    "QuestionTray is mounted; TRAY in a .ts file is never counted; importing a type is not mounting the screen beside it");
  await fs.rm(root, { recursive: true, force: true });
});

test("the allowlist has to stay honest: every line carries a reason, and a line that outlived its finding is reported", async () => {
  for (const [entry, reason] of ALLOWED) {
    assert.equal(typeof reason, "string");
    assert.ok(reason.trim().length >= 20, `${entry} needs a written reason, not a line on a list`);
    assert.match(entry, /^frontend\/src\/local\/[\w.]+\.tsx:[A-Z]\w*$/, `${entry} is not path:ExportName`);
  }
  const root = await tree({
    "frontend/src/main.tsx": "export const app=null;",
    "frontend/src/local/ProjectRoom.tsx": "export function ProjectRoom(){return <section/>;}",
  });
  const { stale } = await scanProject(root);
  assert.ok(!stale.includes("frontend/src/local/ProjectRoom.tsx:ProjectRoom"), "still unreachable here, so the line still earns its place");
  assert.ok(stale.includes("frontend/src/local/SettingsParts.tsx:SettingsGroup"), "no such file in this tree, so that line is stale and says so");
  await fs.rm(root, { recursive: true, force: true });
});
