import {t} from '@/i18n';
import {isLinuxPlatform,isWindowsPlatform} from '@/shared/lib/platform';

/** The few words that name the computer's own places, so Windows and Linux don't read "Mac" and "Finder". */
type Os='mac'|'windows'|'linux';
const os=():Os=>isWindowsPlatform()?'windows':isLinuxPlatform()?'linux':'mac';

/** The corner on the welcome and startup windows. */
export const betaCorner=()=>({mac:t('Private beta · On your Mac'),windows:t('Private beta · On your PC'),linux:t('Private beta · On your computer')})[os()];
/** The action that shows a file in the file manager (Linux opens its folder: platform_os.reveal_command). */
export const showInFileManager=()=>({mac:t('Show in Finder'),windows:t('Show in File Explorer'),linux:t('Show in Files')})[os()];
export const revealInFileManager=()=>({mac:t('Reveal in Finder'),windows:t('Show in File Explorer'),linux:t('Show in Files')})[os()];
