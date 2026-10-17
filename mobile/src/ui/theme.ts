import {useColorScheme} from 'react-native';

/** Dervo's design tokens (evidence/design-system/tokens.css and the 8 Oct whole-app mockup), as the phone reads them.
 * One accent: rust means "this needs you". Green means running, sage means done. Everything else is ink and paper.
 * The "Make Dervo's design exceptional" thread owns these values; this file only mirrors them. ``faint`` is for strokes
 * and glyphs; text that's meant to be read quietly uses ``textFaint``, which keeps 4.5:1 on canvas and raised. */
const light={
  paper:'#f6f5f2',canvas:'#fdfdfc',raised:'#ffffff',sunk:'#f0efeb',hover:'rgba(27,27,25,0.045)',press:'rgba(27,27,25,0.08)',
  ink:'#1b1b19',ink2:'#3c3c38',muted:'#6d6c66',faint:'#a3a29b',textFaint:'#76756f',line:'#ebeae5',lineStrong:'#dddcd5',
  pine:'#22382b',pineInk:'#f8f8f5',sage:'#5e7351',live:'#2f7844',liveWash:'#e8f1ea',rust:'#b0461f',rustWash:'#f8ebe4',
  shadow:'rgb(28,41,33)',bubble:'#f0efeb',
};
const dark:typeof light={
  paper:'#191918',canvas:'#1f1f1e',raised:'#282827',sunk:'#2b2b29',hover:'rgba(236,236,233,0.05)',press:'rgba(236,236,233,0.09)',
  ink:'#ecece9',ink2:'#c9c8c3',muted:'#9b9a93',faint:'#6f6e68',textFaint:'#93928c',line:'#2e2e2c',lineStrong:'#3b3b38',
  pine:'#e4ecdc',pineInk:'#1b1b19',sage:'#a9bd98',live:'#7cc58f',liveWash:'#20332a',rust:'#e48a62',rustWash:'#3b281f',
  shadow:'rgb(0,0,0)',bubble:'#323230',
};
export type Colors=typeof light;

export function useColors():Colors{return useColorScheme()==='dark'?dark:light;}

/** Instrument Serif only for the greeting and brand moments; Instrument Sans for everything you read and use. */
export const font={
  display:'InstrumentSerif_400Regular',
  text:'InstrumentSans_400Regular',
  medium:'InstrumentSans_500Medium',
  semibold:'InstrumentSans_600SemiBold',
};
/** The desktop's sizes, one step up where a thumb or a phone held at arm's length needs it. */
export const size={caption:12,small:13.5,body:15,read:16,title:17,h1:34};
export const space={s1:4,s2:8,s3:12,s4:16,s5:24,s6:32};
export const radius={r1:6,r2:10,r3:14,card:12,composer:20,pill:999};
export const ease={fast:120,calm:220};
