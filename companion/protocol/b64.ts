/** Base64url without padding, written out so the same code runs in Node, a Worker and React Native (no Buffer, no atob). */
const ALPHABET='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const LOOKUP=new Map([...ALPHABET].map((char,index)=>[char,index]));

export function encode(bytes:Uint8Array):string{
  let out='';
  for(let i=0;i<bytes.length;i+=3){
    const a=bytes[i],b=bytes[i+1]??0,c=bytes[i+2]??0,n=(a<<16)|(b<<8)|c;
    out+=ALPHABET[(n>>18)&63]+ALPHABET[(n>>12)&63];
    if(i+1<bytes.length)out+=ALPHABET[(n>>6)&63];
    if(i+2<bytes.length)out+=ALPHABET[n&63];
  }
  return out;
}

/** Null for anything that isn't strict base64url, so a malformed field is refused rather than half-read. */
export function decode(text:string):Uint8Array|null{
  if(typeof text!=='string'||text.length%4===1)return null;
  const out=new Uint8Array(Math.floor(text.length*3/4));
  let bits=0,value=0,at=0;
  for(const char of text){
    const index=LOOKUP.get(char);
    if(index===undefined)return null;
    value=(value<<6)|index;bits+=6;
    if(bits>=8){bits-=8;out[at++]=(value>>bits)&255;}
  }
  // Leftover bits must be zero, so every byte string has exactly one spelling.
  if(value&((1<<bits)-1))return null;
  return out;
}

/** UTF-8 by hand: React Native's Hermes doesn't promise TextDecoder, and these must agree on every platform. */
export function utf8(text:string):Uint8Array{
  const out:number[]=[];
  for(const char of text){
    let c=char.codePointAt(0)!;
    if(c>=0xd800&&c<=0xdfff)c=0xfffd;  // a lone surrogate
    if(c<0x80)out.push(c);
    else if(c<0x800)out.push(0xc0|c>>6,0x80|c&63);
    else if(c<0x10000)out.push(0xe0|c>>12,0x80|c>>6&63,0x80|c&63);
    else out.push(0xf0|c>>18,0x80|c>>12&63,0x80|c>>6&63,0x80|c&63);
  }
  return Uint8Array.from(out);
}

/** Throws on anything that isn't well-formed UTF-8, so a damaged message is refused, not half-read. */
export function fromUtf8(bytes:Uint8Array):string{
  let out='';
  for(let i=0;i<bytes.length;){
    const a=bytes[i];
    let c:number,n:number,min:number;
    if(a<0x80){c=a;n=0;min=0;}
    else if(a>>5===6){c=a&31;n=1;min=0x80;}
    else if(a>>4===14){c=a&15;n=2;min=0x800;}
    else if(a>>3===30){c=a&7;n=3;min=0x10000;}
    else throw new Error('Malformed UTF-8');
    for(let k=1;k<=n;k++){
      const b=bytes[i+k];
      if(b===undefined||b>>6!==2)throw new Error('Malformed UTF-8');
      c=(c<<6)|(b&63);
    }
    if(c<min||c>0x10ffff||(c>=0xd800&&c<=0xdfff))throw new Error('Malformed UTF-8');
    out+=String.fromCodePoint(c);
    i+=n+1;
  }
  return out;
}
