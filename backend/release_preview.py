"""User-operated preview builds. These operations aren't agent tools."""
import datetime
import json
import os
from pathlib import Path
import platform_os
import signal
import subprocess
import sys
from dervo_connections import Rejected
from release_storage import BuildStorage, require_space
from release_retention import status as retention_status

FIELDS={'preview_status':{'projectId'},'preview_start':{'projectId','tools'},'preview_cancel':{'projectId'},'preview_open':{'projectId'},'preview_folder':{'projectId'},'preview_storage_folder':{'projectId'}}
TOOL_KEYS={'node','rustToolchain','cargoHome','rustupHome'}

def running_release(resources=None):
    """Read this host's bundled marker, never the latest preview job's version."""
    resources=Path(resources) if resources is not None else Path(__file__).resolve().parent.parent
    try:
        record=json.loads((resources/'runtime/dervo-preview.json').read_text())
        identifier=record.get('releaseId')
        if type(identifier) is not str or not identifier.strip() or len(identifier)>120 or any(ord(c)<32 for c in identifier):
            return None
        result={'id':identifier}
        if (resources/'runtime/dervo-release-profile.json').exists():
            from release_profiles import read
            profile=read(resources)
            result.update(profile=profile['profile'],label=profile['label'])
        return result
    except (OSError,ValueError,AttributeError):
        return None

class Previews:
    def __init__(self,host):
        self.host=host;self.jobs={};self.processes={};self.launched={};self.lifelines={}
        self.path=host.root/'preview-jobs.json'
        if self.path.exists():
            self.jobs=json.loads(self.path.read_text())
            for job in self.jobs.values():
                if job.get('state') in ('preparing','building'):
                    job.update(state='interrupted',error='Dervo closed before the build finished. Start a new preview.')
    def save(self):
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.jobs,indent=2));temp.replace(self.path)
    def read(self,pid):
        job=self.jobs.get(pid)
        if not job:return None
        process=self.processes.get(pid)
        if process:
            record=Path(job['directory'])/'release.json'
            if record.exists():
                try:
                    state=json.loads(record.read_text())
                    job.update({k:state[k] for k in ['state','step','bundle','error','steps','verification'] if k in state})
                except (ValueError,OSError):pass
            if process.poll() is not None:
                if job['state'] not in ('built','failed','cancelled'):
                    job.update(state='failed',error='The preview builder stopped. Open the release folder for its log.')
                self.stop(pid)
            self.save()
        return job
    def dispatch(self,op,args):
        if type(args) is not dict or set(args)!=FIELDS[op]:raise Rejected('Unsupported preview fields')
        pid=args['projectId']
        if type(pid) is not str:raise Rejected('Choose a project')
        project=self.host.store.db.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not project:raise Rejected('Unknown project')
        job=self.read(pid)
        if op=='preview_status':
            config=Path(project['cwd'])/'tauri.conf.json'
            try:supported=json.loads(config.read_text()).get('identifier')=='local.dervo.connected'
            except (OSError,ValueError):supported=False
            tools_path=self.host.root/'preview-tools.json'
            tools=json.loads(tools_path.read_text()) if tools_path.exists() else {}
            # Retire the old cache import setting without touching saved settings on read.
            tools.pop('cacheRoot', None)
            from release_history import rollback_choice
            version = running_release()
            return {'supported':supported,'job':job,'tools':tools,'running_release':version,
                    'storage':retention_status(Path.home()/'Dervo Releases'),
                    'rollback':rollback_choice(self.host.root, version['id'] if version else None)}
        if op=='preview_storage_folder':
            from release_storage import normal_path
            root=normal_path(Path.home()/'Dervo Releases')
            if not root.is_dir():raise Rejected('No saved release folder exists yet.')
            subprocess.Popen(['/usr/bin/open',str(root)])
            return {'opened':True}
        if op=='preview_start':
            for active in list(self.processes): self.read(active)
            if self.processes:raise Rejected('Wait for the current preview build or cancel it first.')
            tools=args['tools']
            if type(tools) is not dict or set(tools)-TOOL_KEYS:raise Rejected('Unsupported preview build tools')
            tools={k:v for k,v in tools.items() if v}
            if any(type(v) is not str or not Path(v).is_absolute() or not Path(v).exists() for v in tools.values()):raise Rejected('Build-tool locations must be existing absolute paths.')
            source=Path(project['cwd']).resolve()
            if source==self.host.root.resolve() or self.host.root.resolve().is_relative_to(source):raise Rejected('The preview source must not contain Dervo workspace data.')
            config=source/'tauri.conf.json'
            if not config.is_file() or config.is_symlink() or json.loads(config.read_text()).get('identifier')!='local.dervo.connected':raise Rejected('This preview flow currently builds Dervo projects only.')
            release_id='0.1.0-preview.'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S%f')
            output=Path.home()/'Dervo Releases'/release_id
            try:
                require_space(output.parent)
                require_space(BuildStorage(source).directory)
            except RuntimeError as exc:
                raise Rejected(str(exc)) from exc
            retained=retention_status(output.parent)
            if not retained['canBuild']:
                raise Rejected(retained.get('error') or 'Review saved versions before building another preview. Keep the running app and rollback versions.')
            output.parent.mkdir(parents=True,exist_ok=True)
            config_path=self.host.root/'preview-tools.json';config_path.write_text(json.dumps(tools,indent=2))
            log_path=output.parent/(release_id+'.log')
            env={'HOME':str(Path.home()),'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}
            read_fd, write_fd = os.pipe()
            try:
                with log_path.open('w') as log:
                    process=subprocess.Popen([sys.executable,'-E','-s','-B',str(Path(__file__).with_name('release_builder.py')),
                        '--source',str(source),'--output',str(output),'--config',str(config_path),'--parent-fd',str(read_fd)],
                        env=env,stdout=log,stderr=subprocess.STDOUT,**platform_os.new_group(),pass_fds=(read_fd,))
            except BaseException:
                os.close(write_fd)
                raise
            finally:
                os.close(read_fd)
            self.lifelines[pid]=write_fd
            self.processes[pid]=process
            self.jobs[pid]={'id':release_id,'directory':str(output),'state':'preparing','step':'Preparing source snapshot','log':str(log_path),'bundle':None,'storage_version':1,'source':str(source)}
            self.save();return self.jobs[pid]
        if not job:raise Rejected('Build a preview first.')
        if op=='preview_cancel':
            self.stop(pid);job.update(state='cancelled',error='Preview build cancelled. Your project files were not changed.');self.save();return job
        if op=='preview_folder':
            path=Path(job['directory']);path=path if path.exists() else path.parent
            subprocess.Popen(['/usr/bin/open',str(path)]);return job
        if job['state']!='built' or not job.get('bundle'):raise Rejected('Finish a successful preview build before opening it.')
        if job['id'] in self.launched and self.launched[job['id']].poll() is None:
            return job
        bundle=Path(job['bundle']);expected=Path(job['directory'])/'Dervo Local.app'
        if bundle!=expected or not bundle.is_dir():raise Rejected('The retained preview app is missing.')
        env={'HOME':str(Path.home()),'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin','DERVO_DATA_DIR':str(Path(job['directory'])/'preview-data'),'DERVO_PYTHON':sys.executable}
        with (Path(job['directory'])/'preview.log').open('a') as log:
            process=subprocess.Popen([str(bundle/'Contents/MacOS/dervo-local')],env=env,stdout=log,stderr=subprocess.STDOUT,**platform_os.new_group())
        self.launched[job['id']]=process
        job['preview_pid']=process.pid;self.save();return job
    def tick(self):
        for pid in list(self.processes): self.read(pid)
    def stop(self,pid):
        process=self.processes.pop(pid,None)
        if process:
            try: platform_os.signal_group(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: pass
            # A shell may exit before its compiler descendants; stop the whole owned group.
            try: platform_os.signal_group(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=2)
        fd=self.lifelines.pop(pid,None)
        if fd is not None: os.close(fd)
        job=self.jobs.get(pid,{})
        if job.get('storage_version')==1 and job.get('source'):
            try:
                storage=BuildStorage(job['source'])
                if storage.directory.exists():
                    with storage.acquire(): storage.clean_work()
            except (RuntimeError,OSError,ValueError) as exc:
                # Never clean another builder's active staging. Retry on its next build.
                job['storage_note']='Temporary build files will be checked on the next build: '+str(exc)
    def close(self):
        for pid in list(self.processes):
            self.stop(pid);self.jobs[pid].update(state='interrupted',error='Dervo closed during the preview build.')
        self.save()
