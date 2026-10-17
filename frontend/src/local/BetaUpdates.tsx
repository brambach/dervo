import {useEffect, useRef, useState} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {useQuery} from '@tanstack/react-query';
import {local} from './api';
import {SettingRow, SettingsSection} from './DervoControls';
import {Confirm, OpenCard} from './SettingsKit';
import {flushWorkspaceDrafts} from './workspaceDraftPersistence';
import {buildChanges} from './buildChanges';
import {isWindowsPlatform} from '@/shared/lib/platform';
import {t,tr} from '@/i18n';

type Update = {available:boolean; context:string; current:{releaseId:string}|null;
  state:string; detail:string; busy:boolean; canCheck:boolean; canDownload:boolean;
  release:{release_id:string; title:string; notes:string; bytes:number; format:string}|null;
  received:number; savedPath:string|null; cleanupWarning?:string;
  update?:{source:'local'|'download'|'package'; releaseId:string}|null; canUpdate?:boolean; canFetch?:boolean;
  /** Which download the card describes while one runs or has saved: Download update ('fetch') or the installer ('download'). */
  mode?:'fetch'|'download'|null;
  /** Why an update couldn't replace this app where it runs, or null (beta_update_host.py install_problem). */
  installProblem?:{kind:'move'|'permission';line:string}|null};
type Rollback = {switchId:string; version:string; detail:string};

const mb = (bytes:number) => `${(bytes / 1000000).toFixed(1)} MB`;
/** "beta.9" from "0.1.0-beta.9", for the short sentences on the card. */
const short = (id:string) => id.replace(/^\d+\.\d+\.\d+-/, '');
const notes = (text:string) => text.split('\n').map(line => line.replace(/^\s*[-*•]\s*/, '').trim()).filter(Boolean);

function Changelog({releaseId}:{releaseId?:string}) {
  return <SettingsSection title={t('Changelog')} caption={releaseId ?? t('This build')} id="dv-updates-changelog">
    <div className="dv-report"><ul>{buildChanges.map(note=><li key={note}>{note}</li>)}</ul></div>
  </SettingsSection>;
}

const FETCH_LINE = t('Download update gets it ready to switch to. The version you’re using stays until you restart.');
/** The open card's sentence. It follows the download that's running, from the host's mode: canFetch is false for the
 * whole of a download, so a normal Download update used to say "Choose a folder for the installer" (review, 23 Sep).
 * With nothing running it offers what the buttons do. */
export function offerLine(status:Pick<Update,'mode'|'canFetch'>):string {
  if (status.mode === 'fetch') return FETCH_LINE;
  if (status.mode === 'download') return t('The installer goes to the folder you chose. It won’t replace the app you’re using.');
  return status.canFetch ? FETCH_LINE : t('Choose a folder for the installer. Downloading won’t replace the app you’re using.');
}

/** Updates, in Settings (settings/updates.html, updates-ready.html): this version as a row with Check for updates, a newer
 * beta as the page's one open card, and the previous version with Go back. Status comes from the host every second while
 * the page is open; nothing checks or downloads until you ask. */
export function BetaUpdates({projectId, onAccount}:{projectId?:string; onAccount?:()=>void}) {
  const [status, setStatus] = useState<Update|null>(null);
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState('');
  const [later, setLater] = useState(false);
  const alive = useRef(false);
  const sequence = useRef(0);
  const working = useRef(false);
  useEffect(() => {
    alive.current = true;
    let disposed = false;
    let timer:ReturnType<typeof setTimeout>;
    async function poll() {
      if (!working.current) {
        const request = ++sequence.current;
        try {
          const next = await local<Update>('beta_update_status');
          if (!disposed && request === sequence.current) {setStatus(next); setError('');}
        } catch {
          if (!disposed && request === sequence.current) {setStatus(null); setError(t('Dervo couldn’t check or download this update. Check your account and connection, then retry.'));}
        }
      }
      if (!disposed) timer = setTimeout(poll, 1000);
    }
    void poll();
    return () => {disposed = true; alive.current = false; ++sequence.current; clearTimeout(timer);};
  }, []);
  // Update and restart: the native app asks the host for the newer app it found, backs up, quits and opens it.
  // Nothing here names a path or version, and on success this window closes.
  async function install() {
    if (!status?.canUpdate || installing) return;
    setInstalling(true); setInstallError('');
    try {
      await invoke('install_update');
    } catch (failure) {
      if (alive.current) {setInstallError(String(failure)); setInstalling(false);}
    }
  }
  async function action(kind:'check'|'download'|'cancel'|'fetch') {
    if (!status || working.current) return;
    working.current = true;
    const request = ++sequence.current;
    setSending(true); setError('');
    try {
      if (kind === 'download') {
        await invoke('download_beta_update', {context:status.context, releaseId:status.release?.release_id});
      } else if (kind === 'fetch') {
        await local('beta_update_fetch', {context:status.context, releaseId:status.release?.release_id});
      } else {
        await local(`beta_update_${kind}`, {context:status.context});
      }
      if (kind === 'check') setLater(false);
      // Read current host state, never reuse a result from before an account change.
      const next = await local<Update>('beta_update_status');
      if (alive.current && request === sequence.current) setStatus(next);
    } catch {
      if (alive.current && request === sequence.current) {
        setStatus(null); setError(t('Dervo couldn’t check or download this update. Check your account and connection, then retry.'));
      }
    } finally {
      working.current = false;
      if (alive.current) setSending(false);
    }
  }
  if (status && !status.available) return <Changelog releaseId={status.current?.releaseId}/>;
  const release = status?.release;
  const ready = !!status?.update && status.state === 'ready';
  const offered = !ready && !!release && ['available', 'downloading', 'unpacking', 'downloaded', 'cancelled'].includes(status!.state);
  const signedOut = !!status && !status.canCheck && !status.busy && status.state === 'unchecked';
  const current = status?.current?.releaseId;
  // An update can't replace this app where it runs (Harrison, 25 Sep): say how to fix it, and don't offer a restart that fails.
  const blocked = status?.installProblem ?? null;
  const line = !status ? (error ? '' : t('Checking this version…'))
    : ready && !later ? t('The version you’re using now.')
    : ready ? t('{releaseId} is ready to install.',{releaseId:status.update!.releaseId})
    : signedOut ? t('Sign in with approved beta access to check for updates.')
    : status.detail;
  const rowButton = !status ? null
    : ready && later ? <button type="button" className="dv-btn dv-btn-primary dv-btn-sm" disabled={installing || !status.canUpdate || !!blocked} onClick={() => void install()}>{installing ? t('Updating…') : t('Update and restart')}</button>
    : status.busy && !offered ? <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={sending} onClick={() => void action('cancel')}>{t('Cancel')}</button>
    : ready || offered ? null
    : <>{signedOut && onAccount && <button type="button" className="dv-btn dv-btn-quiet" onClick={onAccount}>{t('Account')}</button>}
        <button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" disabled={sending || !status.canCheck} onClick={() => void action('check')}>{sending ? t('Checking…') : t('Check for updates')}</button></>;
  return <>
    <SettingsSection title={t('This version')} caption={t('Private beta')} id="dv-updates-version">
      <SettingRow name={current ? `Dervo ${current}` : 'Dervo'} line={<span role="status">{line}</span>} end={rowButton}/>
      {status?.cleanupWarning && <p role="alert" className="dv-field-note">{status.cleanupWarning}</p>}
      {blocked && (blocked.kind === 'move' || ready || offered) && <p role="alert" className="dv-field-note" data-testid="update-blocked">{blocked.line}</p>}
      {error && <div className="dv-set" role="alert"><div><p className="dv-set-line">{error}</p></div>
        <div className="dv-set-end"><button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={() => {setError(''); sequence.current++; void local<Update>('beta_update_status').then(setStatus).catch(() => setError(t('Dervo couldn’t check or download this update. Check your account and connection, then retry.')));}}>{t('Try again')}</button></div></div>}
      {ready && !later && <OpenCard title={t('{releaseId} is ready',{releaseId:status!.update!.releaseId})} caption={release ? mb(release.bytes) : undefined} onEscape={() => setLater(true)}>
        <div data-testid="update-ready" style={{display:'contents'}}>
          {release && notes(release.notes).length > 0 && <div className="dv-report"><ul>{notes(release.notes).map((note, index) => <li key={index}>{note}</li>)}</ul></div>}
          {/* A Linux .deb or .rpm installs through the desktop's software installer, which keeps no earlier copy (linux_update.py). */}
          {status!.update!.source === 'package' ? <p className="dv-card-text">{t('Dervo backs up your workspace, closes, and opens {version} in your software installer. Install it there, then open Dervo again.', {version: short(status!.update!.releaseId)})} {t('Finish or stop running threads first.')}</p>
          : <p className="dv-card-text">{status!.update!.source === 'download' ? t('Dervo backs up your workspace, closes, and opens {version} in this app’s place.', {version: short(status!.update!.releaseId)}) : t('Dervo backs up your workspace, closes, and opens {version}.', {version: short(status!.update!.releaseId)})} {current ? t('{version} is kept so you can go back.', {version: short(current).charAt(0).toUpperCase() + short(current).slice(1)}) : t('The version you’re using is kept so you can go back.')} {t('Finish or stop running threads first.')}</p>}
          {installError && <p role="alert" className="dv-field-note">{installError}</p>}
          <div className="dv-card-actions"><button type="button" className="dv-btn dv-btn-quiet" disabled={installing} onClick={() => setLater(true)}>{t('Later')}</button>
            <button type="button" className="dv-btn dv-btn-primary" disabled={installing || !status!.canUpdate || !!blocked} onClick={() => void install()}>{installing ? t('Updating…') : t('Update and restart')}</button></div>
        </div>
      </OpenCard>}
      {ready && later && installError && <p role="alert" className="dv-field-note">{installError}</p>}
      {offered && release && <OpenCard title={release.title || release.release_id} caption={mb(release.bytes)}>
        {notes(release.notes).length > 0 && <div className="dv-report"><ul>{notes(release.notes).map((note, index) => <li key={index}>{note}</li>)}</ul></div>}
        <p className="dv-card-text">{release.release_id}. {offerLine(status!)}</p>
        {status!.state === 'downloading' && <div className="dv-progress">
          <progress aria-label={t('Installer download')} max={release.bytes} value={status!.received} className="dv-meter-progress"/>
          <span className="dv-caption">{t('{done} of {size}', {done: (status!.received / 1000000).toFixed(1), size: mb(release.bytes)})}</span></div>}
        {status!.state === 'unpacking' && <p className="dv-card-text" role="status">{t('Unpacking the new version…')}</p>}
        {status!.savedPath && <p className="dv-card-text">{tr('Installer saved and checksum verified: {path}', {path: <b>{status!.savedPath}</b>})}</p>}
        <div className="dv-card-actions">
          {status!.busy ? <button type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={() => void action('cancel')}>{t('Cancel')}</button> : <>
            {/* Saving the installer uses the Mac folder picker (src/beta_updates.rs). On Windows, Download update gets the installer and runs it. */}
            {status!.canDownload && status!.canFetch && !isWindowsPlatform() && <button type="button" className="dv-btn dv-btn-quiet" disabled={sending} onClick={() => void action('download')}>{t('Save the installer instead')}</button>}
            {status!.canFetch ? <button type="button" className="dv-btn dv-btn-primary" disabled={sending || !!blocked} onClick={() => void action('fetch')}>{t('Download update')}</button>
              : status!.canDownload && <button type="button" className="dv-btn dv-btn-primary" disabled={sending} onClick={() => void action('download')}>{t('Download installer')}</button>}
          </>}
        </div>
      </OpenCard>}
    </SettingsSection>
    <Changelog releaseId={current}/>
    {projectId && <PreviousVersion projectId={projectId}/>}
  </>;
}

/** The version kept by the last update, with Go back, confirmed in place. It restores the workspace saved before that
 * update; newer work goes to a separate backup. Only shown when the host kept one. */
function PreviousVersion({projectId}:{projectId:string}) {
  const status = useQuery({queryKey:['preview', projectId], queryFn:() => local<{rollback?:Rollback|null}>('preview_status', {projectId})});
  const [confirm, setConfirm] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const rollback = status.data?.rollback;
  if (!rollback) return null;
  async function goBack() {
    setBusy(true); setError('');
    try {await flushWorkspaceDrafts(); await invoke('restore_previous_version', {switchId:rollback!.switchId});}
    catch (failure) {setError(String(failure)); setBusy(false); setConfirm(false);}
  }
  const name = short(rollback.version);
  return <SettingsSection title={t('Previous version')} id="dv-updates-previous">
    {confirm ? <Confirm title={t('Go back to {name}?',{name})} line={t('Your newer conversations and settings changes are kept in a separate backup. Project files aren’t rolled back.')}
        keep={t('Keep this version')} act={t('Back up and go back')} busy={busy} busyLabel={t('Preserving newer work…')} onKeep={() => setConfirm(false)} onAct={() => void goBack()}/>
      : <SettingRow name={rollback.version} line={t('Kept when you updated, with the workspace saved just before.')}
          end={<button type="button" className="dv-btn dv-btn-ghost dv-btn-sm" onClick={() => setConfirm(true)}>{t('Go back to {name}', {name})}</button>}/>}
    {error && <p role="alert" className="dv-field-note">{error}</p>}
  </SettingsSection>;
}

/** What Dervo is licensed under, and the Buzz credit its Apache 2.0 licence asks for. Both texts ship in the app's
 * Resources folder. */
export function Licences() {
  return <SettingsSection title={t('Licences')} id="dv-updates-licences">
    <SettingRow name="Dervo" line={t('Open source under the MIT License.')}/>
    <SettingRow name="Buzz" line={t('Parts of Dervo started from Buzz, used under the Apache License 2.0.')}/>
    <SettingRow name="xterm.js" line={t('The project terminal uses xterm.js and its fit addon, under the MIT licence. The licence text ships with Dervo.')}/>
  </SettingsSection>;
}
