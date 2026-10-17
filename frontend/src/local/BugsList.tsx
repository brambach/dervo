/** Bugs and ideas went away in beta.16 (b16/lifecycle; Bryce, 24 Sep): each project has one Next up plan instead
 * (NextUp.tsx). ConnectedApp still opens the page by this name from the sidebar's Next up, so this file only points it
 * at Next up until ConnectedApp imports NextUp itself (asked of b16/thread); then it goes.
 *
 * The item types stay here for BugCapture, whose ⌘⇧B still saves a record (for its number and its Undo) and now lands
 * as a line under Not decided too. */
export {NextUp as BugsList} from './NextUp';

export type ItemKind='bug'|'idea';
export type ItemState='planned'|'building'|'done'|'closed';
export type Note={id:string;at:string;actor:string;text:string};
export type Item={id:string;kind:ItemKind;number:number;name:string;state:ItemState;target:string|null;archived_at:string|null;
 created_at:string;updated_at:string;revision:number;product?:'project'|'dervo';notes:Note[];notes_dropped:number};
export type ItemListing={items:Item[];archived:Item[];next_number:number;kinds:ItemKind[];states:ItemState[];
 limits:{name:number;note:number;notes:number;target:number}};
