/** ``replace``: the text takes the whole draft's place (Edit on a sent message); ⌘Z brings the draft back. */
export type ComposerTextPaste = {text:string; from:number; to:number; document:string; replace?:boolean};
/** Returns whether the original selection was restored. A changed draft receives the text at its end. */
export type InsertComposerTextPaste = (paste:ComposerTextPaste) => boolean;
