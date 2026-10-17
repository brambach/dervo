import './Moss.css';

/** The moss squircle: Dervo's one working indicator, a Dervo derivation of the ldrs "squircle" loader (MIT, Griffin
 * Johnston). A short dash travels a rounded-square track in moss green. Under reduced motion the dash stands still, so
 * the mark still reads as "running" without moving. Decorative only: the text beside it carries the meaning. */
const SQUIRCLE='M0.5,25C0.5,7.8,7.8,0.5,25,0.5S49.5,7.8,49.5,25S42.2,49.5,25,49.5S0.5,42.2,0.5,25';
export function Moss({size=14,className=''}:{size?:number;className?:string}){
  return <span className={`dervo-moss ${className}`} style={{width:size,height:size}} aria-hidden="true" data-testid="moss">
    <svg viewBox="-4 -4 58 58" width={size} height={size}>
      <path className="dervo-moss-track" strokeWidth={7} pathLength={100} d={SQUIRCLE}/>
      <path className="dervo-moss-car" strokeWidth={7} pathLength={100} d={SQUIRCLE}/>
    </svg>
  </span>;
}
