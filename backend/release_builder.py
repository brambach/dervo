"""Build a retained Dervo preview from a separate source snapshot. No installation."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import platform_os
from release_storage import BuildStorage, normal_path, require_space
from release_copy import copy_snapshot_file, file_hash
from release_retention import ReleaseSlot

EXCLUDED = {'.git', '.env', '.DS_Store', '__pycache__', 'node_modules', 'target', 'dist', 'build', 'evidence'}
SOURCE_ITEMS = ['backend', 'frontend', 'src', 'capabilities', 'icons', 'runtime', 'Cargo.toml', 'Cargo.lock',
                'scripts', 'preview-features.json', 'build.rs', 'tauri.conf.json', 'Info.plist', 'titlebar.test.mjs', 'LICENSE-Buzz', 'LICENSE.md',
                # Hardened-runtime entitlements for a Developer ID build (release_signing.py).
                'signing',
                # Voice for Home: the Mac audio helper's source and entitlements (scripts/voice_runtime.py builds it).
                'voice',
                # The phone link (frontend/src/local/phoneLink.ts) imports companion/mac/link.ts and the shared protocol; the
                # frontend type-check stops without them. Its node_modules is in EXCLUDED, so tweetnacl comes from frontend's.
                'companion',
                # test_codex_runtime reads the Windows installer's resources and the ignore line for runtime/codex-win/.
                # Tauri merges tauri.windows.conf.json only on Windows, so the Mac bundle is unchanged.
                'tauri.windows.conf.json', '.gitignore']
SOURCE_TOOLS = ['tools/load_parallel_runs.py', 'tools/load_parallel_host.py']


def snapshot(source, destination):
    destination.mkdir()
    def ignore(parent, names):
        return [name for name in names if name in EXCLUDED or name.startswith('.env')]
    for name in SOURCE_ITEMS:
        path = source / name
        if path.is_symlink(): raise RuntimeError('Preview source cannot link outside the project: ' + name)
        if path.is_dir():
            # Refuse source links rather than copying outside files into a release.
            for root, directories, files in os.walk(path, followlinks=False):
                directories[:] = [n for n in directories if n not in EXCLUDED]
                for item in directories + files:
                    if item not in EXCLUDED and (Path(root)/item).is_symlink():
                        raise RuntimeError('Linked source needs attention: ' + str((Path(root)/item).relative_to(source)))
            shutil.copytree(path, destination/name, ignore=ignore, copy_function=copy_snapshot_file)
        elif path.is_file(): copy_snapshot_file(path, destination/name)
    # The parallel-run load test (test_parallel_runs) drives these two tools; the rest of tools/ stays out of releases.
    for name in SOURCE_TOOLS:
        path = source / name
        if path.is_symlink(): raise RuntimeError('Preview source cannot link outside the project: ' + name)
        if path.is_file():
            (destination/name).parent.mkdir(parents=True, exist_ok=True)
            copy_snapshot_file(path, destination/name)
    schema=source/'schemas/codex-app-server'
    if any(p.is_symlink() for p in [source/'schemas',schema]):
        raise RuntimeError('Linked protocol schema is not a source snapshot.')
    if schema.is_dir():
        if any(p.is_symlink() for p in schema.rglob('*')): raise RuntimeError('Linked protocol schema file is not a source snapshot.')
        shutil.copytree(schema,destination/'schemas/codex-app-server',copy_function=copy_snapshot_file)
    dependencies = source/'frontend/node_modules'
    if not dependencies.is_dir(): raise RuntimeError('Install the frontend dependencies before building a preview.')
    (destination/'frontend/node_modules').symlink_to(dependencies.resolve(), target_is_directory=True)
    return {str(p.relative_to(destination)): file_hash(p)
            for p in destination.rglob('*') if p.is_file() and not p.is_symlink() and 'node_modules' not in p.parts}


COMMIT_LIMIT = 20000  # commits a build records from its source HEAD, newest first (Dervo has a few hundred)
SHA = __import__('re').compile(r'[0-9a-f]{40}')


def source_commits(source, limit=COMMIT_LIMIT, pass_fds=()):
    """Every commit the source checkout's HEAD contains, newest first, so a build can say which approved commits it
    holds (Bugs and ideas: "Fixed in <release>"). Nothing for a folder that isn't a repository or has no commit."""
    import task_git
    from dervo_connections import Rejected
    try:
        # task_git._git: the SAFE pins and the checkout's own filter drivers switched off (security review 20 Sep, M2).
        done = task_git._git(str(source), 'rev-list', f'--max-count={int(limit)}', 'HEAD', '--', timeout=60,
                             run=lambda argv, **kw: subprocess.run(argv, **dict(kw, pass_fds=pass_fds)))
    except (OSError, subprocess.SubprocessError, Rejected):
        return []
    # A fake or odd result (no text on stdout) reads as no commits, never as a failed build.
    if done.returncode != 0 or not isinstance(done.stdout, str):
        return []
    return [line.strip() for line in done.stdout.splitlines() if SHA.fullmatch(line.strip())]


def record_build_in_source(source, work, before):
    """Copy the stamped plan back to the source checkout after a build passes, so the next build lists this one too.
    It shows there as an uncommitted change to commit. A plan edited in the source during the build is left alone."""
    import beta_plan
    target=source/beta_plan.PLAN
    if target.is_symlink() or not target.is_file() or file_hash(target)!=before:
        return 'The source plan changed during the build, so this build wasn’t added to it.'
    temp=target.with_name(target.name+'.tmp')
    temp.write_bytes((work/beta_plan.PLAN).read_bytes());temp.replace(target)
    return 'Added to '+beta_plan.PLAN+' in the source. Commit it with the next change.'


def validate_cache(cache):
    # Compiler caches are optional. Cached links must not redirect writes or cleanup.
    if any(p.is_symlink() for p in [cache, *cache.parents]):
        raise RuntimeError('Linked build cache is not allowed. Choose a normal folder.')
    for root, directories, files in os.walk(cache, followlinks=False):
        for name in directories + files:
            if (Path(root)/name).is_symlink():
                raise RuntimeError('Linked build cache entry is not allowed: ' + str((Path(root)/name).relative_to(cache)))


def build(source, output, config, profile='private-beta'):
    import release_profiles
    release_profiles.manifest(profile)
    source, output = Path(source).resolve(), normal_path(output)
    if output.exists(): raise RuntimeError('That preview already exists. Choose a new release ID.')
    if output.is_relative_to(source): raise RuntimeError('Keep retained releases outside the source project.')
    require_space(output.parent)
    with ReleaseSlot(output.parent).acquire() as slot, BuildStorage(source).acquire() as storage:
        require_space(storage.directory)
        storage.clean_work()
        try:
            return _build(source, output, config, storage, profile, slot.child_fds())
        finally:
            storage.clean_work()


def _build(source, output, config, storage, profile='private-beta', release_fds=()):
    import release_profiles
    import account_config
    import release_version
    import beta_plan
    if output.exists(): raise RuntimeError('That preview already exists. Choose a new release ID.')
    if output.is_relative_to(source): raise RuntimeError('Keep retained releases outside the source project.')
    if json.loads((source/'tauri.conf.json').read_text()).get('identifier') != 'local.dervo.connected':
        raise RuntimeError('This preview flow currently builds Dervo projects only.')
    node = config.get('node') or shutil.which('node')
    toolchain = config.get('rustToolchain')
    cargo = str(Path(toolchain)/'bin/cargo') if toolchain else shutil.which('cargo')
    for label, executable in [('Node',node),('Cargo',cargo)]:
        if not executable or not Path(executable).is_file(): raise RuntimeError(label+' was not found. Set its build-tool location in Advanced.')
    account_project = account_config.for_build(source, config)
    output.mkdir(parents=True)
    state = {'id':output.name,'state':'preparing','source':str(source),'directory':str(output),'steps':[],'bundle':None}
    def save():
        temp=output/'release.tmp';temp.write_text(json.dumps(state,indent=2));temp.replace(output/'release.json')
    def run(label, command, cwd, timeout=1200):
        state['step']=label;save()
        with (output/(label+'.log')).open('w') as log:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout,pass_fds=storage.child_fds()+release_fds)
        state['steps'].append({'name':label,'exit':result.returncode,'log':str(output/(label+'.log'))});save()
        if result.returncode: raise RuntimeError(label+' failed. Open its log in this release folder.')
    try:
        save(); work=storage.work;state['source_sha256']=snapshot(source,work)
        # The commits this build holds, from the source HEAD: Bugs and ideas reads them to stamp "Fixed in <release>".
        state['commits']=source_commits(source,pass_fds=storage.child_fds()+release_fds);state['head']=state['commits'][0] if state['commits'] else None
        state['native_version']=release_version.stage(work, output.name, storage.next_build_number())
        for name in ('tauri.conf.json', 'Info.plist'):
            state['source_sha256'][name]=hashlib.sha256((work/name).read_bytes()).hexdigest()
        # The Releases page ships with this build recorded as the newest version, read from git at the source's HEAD.
        plan_before=state['source_sha256'].get(beta_plan.PLAN)
        try:
            stamped=beta_plan.stamp(work,source,output.name,datetime.date.today().isoformat())
        except Exception as exc:
            raise RuntimeError('The beta plan needs attention: '+str(exc)) from None
        if stamped is not None:
            state['source_sha256'][beta_plan.PLAN]=hashlib.sha256((work/beta_plan.PLAN).read_bytes()).hexdigest()
        state['release_profile']=release_profiles.stage(work,profile)
        account_config.stage(work, account_project)
        account_marker='runtime/'+account_config.NAME
        state['source_sha256'][account_marker]=hashlib.sha256((work/account_marker).read_bytes()).hexdigest()
        marker='runtime/'+release_profiles.MARKER
        state['source_sha256'][marker]=hashlib.sha256((work/marker).read_bytes()).hexdigest()
        state['source_archive']=str(storage.archive_source(output))
        state['build_storage']={'cache':str(storage.target),'temporary_source':str(work),'policy':'One shared cache per project; temporary source is cleaned after each attempt.'}
        save()
        env={'HOME':str(Path.home()),'PATH':str(Path(cargo).parent)+':'+str(Path(node).parent)+':/usr/bin:/bin:/usr/sbin:/sbin',
             'CARGO_TARGET_DIR':str(storage.target),'CARGO_BUILD_JOBS':'1','CARGO_NET_OFFLINE':'true','CI':'true',
             'PYTHONDONTWRITEBYTECODE':'1'}
        if config.get('cargoHome'): env['CARGO_HOME']=config['cargoHome']
        if config.get('rustupHome'): env['RUSTUP_HOME']=config['rustupHome']
        # Cargo build outputs can embed absolute paths. Reuse only this cache
        # in place; importing another target can break after that folder is removed.
        validate_cache(storage.target)
        state['state']='building';save()
        # Check the new behaviors as well as compiling the complete frontend and native app.
        # This suite has exceeded 20 minutes on a busy Mac. Keep its budget
        # bounded without changing the limits for individual tests or other steps.
        run('feature-checks',[sys.executable,'release_checks.py','test_beta_feedback','test_release_copy','test_beta_updates','test_app_update','test_beta_download','test_beta_update_host','test_project_lifecycle','test_project_terminal','test_project_files','test_reply_actions','test_keep_awake','test_workspace_drafts','test_image_attachments','test_document_attachments','test_image_delivery','test_project_model','test_conversation_model','test_conversation_profile','test_provider_keys','test_profile_names','test_claude_version','test_provider_usage','test_provider_failure','test_task_git','test_conversation_lifecycle','test_conversation_activity','test_project_setup','test_project_reading','test_codex_coding','test_coding_integration','test_claude_coding','test_harness_claude','test_conversation_coding','test_workspace_search','test_workspace_briefing','test_task_activity','test_message_activity','test_release_preview','test_release_storage','test_release_retention','test_release_handoff','test_release_switch','test_release_history','test_release_selection','test_release_profiles','test_release_package','test_release_version','test_account_packaging','test_account_tls','test_supabase_client','test_supabase_account','test_account_races','test_openrouter_connection','test_openrouter_host','test_openrouter_budget','test_openrouter_routes','test_openrouter_receipts','test_account_photo','test_reviewer_setup','test_independent_review','test_review_evidence','test_knowledge_delivery','test_human_acceptance','test_manual_review','test_account_keychain','test_native_keychain','test_build_prerequisites','test_chat_slice','test_check_duration','test_claude_login','test_codex_coding_policy','test_codex_live','test_codex_login','test_codex_runtime','test_collab_channel','test_collaboration','test_collaboration_acceptance','test_collaboration_harness','test_collaboration_safety','test_connections_ui','test_conversation_feed','test_conversation_memory','test_conversation_organisation','test_coordination_search','test_dervo_account','test_file_recovery','test_harness_codex','test_harness_help','test_harness_host','test_host','test_host_stdio','test_idle_settlement','test_independent_review_safety','test_knowledge_relevance','test_local_codex','test_observation_reads','test_openrouter_request','test_openrouter_stream','test_owner_effort','test_project_knowledge','test_provider_check_result','test_rust_setup','test_task_evidence','test_task_guide','test_task_lessons','test_tool_validation_diagnostics','test_full_access','test_computer_access','test_transport_limits','test_chat_links','test_thread_fanout','test_task_questions','test_worker_question_review','test_worker_question_attention','test_first_run','test_project_ideas','test_project_orchestrator','test_home_routing','test_home_requests','test_pickup','test_thread_names','test_thread_filing','test_beta_plan','test_project_skills','test_project_memory','test_backend_context_efficiency','test_words','test_independent_review','test_provider_plans','test_project_settings','test_outside_sessions','test_run_history','test_handoff_snapshot','test_codex_policy','test_parallel_runs','test_thread_folders',
            # sweep/native (PLAN-native A5, A9): web search on both providers, auto memory off.
            'test_native_first',
            # Knowledge (design sweep, 19 Sep): the repository's instruction files and Knowledge's undo.
            'test_repo_guidance',
            # sweep/analytics (beta.9): usage events, the allowlist, the switch, the queue and the admin report.
            'test_usage_events',
            # beta.11: release signatures checked before an update is saved or unpacked, and content-free error reports.
            'test_release_signature', 'test_error_reports',
            # b11/devid: Developer ID signing, notarisation and the same-team update check.
            'test_release_signing',
            # beta.15, group 7: which Mac icons an account may use, and the Dock tile plug-in's bundle entry.
            'test_app_icons',
            # beta.18: Claude Code's status lines before the task don't stop a run; turn output still does, named, relaunched once.
            'test_claude_run_start',
            # beta.18: lost runs settled from the provider's record, how a run ended (17.1) and which notes a request recalls.
            'test_stopping_settle', 'test_run_ending', 'test_memory_recall',
            # beta.18 (b18/accounts): adding an account by signing in first, with nothing typed.
            'test_account_signin'],work/'backend',timeout=1800)
        run('frontend-types',[node,'node_modules/typescript/bin/tsc','--noEmit'],work/'frontend')
        run('frontend-px-text',[node,'scripts/check-px-text.mjs'],work/'frontend')
        # One voice on every screen. Report mode until the UI strings from before the wedge are cleaned up; drop the flag to gate.
        run('frontend-copy',[node,'scripts/check-copy.mjs'],work/'frontend')
        run('frontend-features',[node,'--import','./test-loader.mjs','--experimental-strip-types','--test','--test-concurrency=1','src/local/BetaFeedback.test.mjs','src/local/sendingRow.test.mjs','src/shared/ui/markdown/localFileLinks.test.mjs','src/shared/ui/markdown/replyActions.test.mjs','src/shared/ui/markdown/replyChips.test.mjs','src/local/previewPane.test.mjs','src/local/resultNotes.test.mjs','src/local/firstRun.test.mjs','src/local/jobLive.test.mjs','src/local/surfacesAdd.test.mjs','src/local/dervoActivity.test.mjs','src/local/taskGuide.test.mjs','src/local/responsePresentation.test.mjs','src/shared/ui/receivedText.test.mjs','src/shared/ui/markdown/streaming.test.mjs','src/shared/ui/markdown/nodeCache.test.mjs','src/local/betaUpdates.test.mjs','src/local/projectSetup.test.mjs','src/local/releasePreview.test.mjs','src/local/workspaceDraftPersistence.test.mjs','src/local/workspaceDraftGate.test.mjs','src/local/independentReview.test.mjs','src/local/ownerEffort.test.mjs','src/local/dervoAccount.test.mjs','src/local/conversationCoding.test.mjs','src/local/activeRunNotice.test.mjs','src/local/codingQueue.test.mjs','src/local/addAccount.test.mjs','src/local/moss.test.mjs','src/local/ConversationStop.test.mjs','src/local/messageAvatars.test.mjs','src/local/timelineRows.test.mjs','src/local/conversationLifecycle.test.mjs','src/local/checkOverride.test.mjs','src/local/conversationScreen.test.mjs','src/local/knowledgeScreen.test.mjs','src/local/workspaceSearch.test.mjs','src/local/humanAcceptance.test.mjs','src/local/collaboration.test.mjs','src/local/dervoBrand.test.mjs','src/local/codingTasks.test.mjs','src/local/taskFanout.test.mjs','src/local/workerQuestions.test.mjs','src/local/openRouterSettings.test.mjs','src/local/imageInput.test.mjs','src/local/localImageComposer.test.mjs','src/local/bugsList.test.mjs','src/local/workspaceBriefing.test.mjs','src/local/dervoSidebar.test.mjs','src/local/usagePopover.test.mjs','src/local/connectedAccounts.test.mjs','src/local/testersPage.test.mjs','src/local/waitlistInvites.test.mjs','src/local/commandPalette.test.mjs','src/local/composerTools.test.mjs','src/local/guideActions.test.mjs','src/local/catchupActions.test.mjs','src/local/checkProfiles.test.mjs','src/local/settingsShell.test.mjs','src/local/dervoControls.test.mjs','src/local/notifications.test.mjs','src/local/cleanups.test.mjs','src/local/progressBrief.test.mjs','src/local/projectCatchup.test.mjs','src/local/modelPicker.test.mjs','src/local/projectSettings.test.mjs','src/local/fullAccess.test.mjs','src/local/projectRoom.test.mjs','src/local/home.test.mjs','src/local/presence.test.mjs','src/local/projectTerminal.test.mjs','src/local/terminalShortcutIsolation.test.mjs','src/local/betaPlan.test.mjs','src/local/conversationMentions.test.mjs','src/local/taskRetry.test.mjs','src/local/useTaskGuideVisibility.test.mjs','src/local/projectSkills.test.mjs','src/local/conversationMentionPills.test.mjs','src/local/escapeToStop.test.mjs','src/local/archiveShortcut.test.mjs','src/local/sidebarResize.test.mjs','src/local/accountForeground.test.mjs','src/local/appearanceSettings.test.mjs','src/local/chatSlice.test.mjs','src/local/claudeCodingTask.test.mjs','src/local/codexLoginUI.test.mjs','src/local/connectionLogin.test.mjs','src/local/connectionsUI.test.mjs','src/local/conversationFeedPatch.test.mjs','src/local/conversationForeground.test.mjs','src/local/conversationMemory.test.mjs','src/local/conversationPolling.test.mjs','src/local/fileRecovery.test.mjs','src/local/harness.test.mjs','src/local/inspectorNavigation.test.mjs','src/local/projectKnowledge.test.mjs','src/local/projectKnowledgeUI.test.mjs','src/local/projectScope.test.mjs','src/local/providerLabel.test.mjs','src/local/sessionMemory.test.mjs','src/local/startupGate.test.mjs','src/local/taskReading.test.mjs','src/local/workspaceWelcome.test.mjs','src/local/outsideSessions.test.mjs','src/local/history.test.mjs','src/local/runRecord.test.mjs','src/local/traceFile.test.mjs','src/local/handoffConfirm.test.mjs','src/local/errorNotes.test.mjs','src/local/threadFolder.test.mjs',
            # Design sweep, first run and brand moments (sweep/firstrun): the project-created moment and the overview that
            # waits for beta access.
            'src/local/projectMoment.test.mjs',
            # Design sweep, surfaces (19 Sep): picker A, the account menu, thread menus with Undo, add-project and the
            # task sheet. Manage projects, the organisation panel and the old project forms were removed with their tests.
            'src/local/surfaces.test.mjs','src/local/surfacesMenus.test.mjs','src/local/surfacesAdd.test.mjs',
            # sweep/analytics (beta.9): the usage switch, the notice, screen views and the Testers page's Usage section.
            'src/local/usageSharing.test.mjs',
            # beta.15, groups 7 and 9: the app icon picker and the update prompt.
            'src/local/appIconPicker.test.mjs','src/local/updatePrompt.test.mjs',
            # beta.18: busy answers matched by code in English and Portuguese, and the memory lines under a reply.
            'src/i18n/hostMessages.test.mjs','src/local/rememberedLine.test.mjs'],work/'frontend')
        run('frontend-build',[node,'frontend/node_modules/vite/bin/vite.js','build','frontend','--outDir','../dist','--emptyOutDir'],work)
        run('native-tests',[cargo,'test','--offline'],work)
        # Rebuild the complete bundle from the current manifest, including icons and metadata.
        bundle_output=storage.target/'release/bundle'
        if bundle_output.is_symlink(): raise RuntimeError('Linked cached bundle directory is not allowed.')
        if bundle_output.exists(): shutil.rmtree(bundle_output)
        executable=storage.target/'release/dervo-local'
        if executable.is_symlink(): raise RuntimeError('Linked cached executable is not allowed.')
        run('native-bundle',[node,'frontend/node_modules/@tauri-apps/cli/tauri.js','build','--bundles','app','--','--offline'],work)
        template=storage.target/'release/bundle/macos/Dervo Local.app'
        bundle=output/'Dervo Local.app'
        subprocess.run(['/bin/cp','-cR',str(template),str(bundle)],check=True,pass_fds=storage.child_fds()+release_fds)
        # The app's own Python 3.13 (runtime/python.json): fetched once into a shared download cache, checked by SHA-256,
        # trimmed and precompiled. Before the bundle audit and signing, which cover it like the rest of the app.
        run('python-runtime',[sys.executable,'-E','-s','-B','scripts/python_runtime.py','stage','--app',str(bundle)],work,timeout=900)
        try:
            state['python_runtime']=json.loads((output/'python-runtime.log').read_text())
        except ValueError:
            state['python_runtime']={'log':str(output/'python-runtime.log')}
        # Voice's pinned libraries (runtime/voice.json) into Resources/voice/site-packages, checked by importing them with
        # the app's own Python, and the voice audio helper built from voice/mac. Signed below with everything else.
        run('voice-runtime',[sys.executable,'-E','-s','-B','scripts/voice_runtime.py','stage','--app',str(bundle)],work,timeout=900)
        try:
            state['voice_runtime']=json.loads((output/'voice-runtime.log').read_text())
        except ValueError:
            state['voice_runtime']={'log':str(output/'voice-runtime.log')}
        resources=bundle/'Contents/Resources'
        state['excluded_runtime_tests']=release_profiles.exclude_test_modules(resources)
        metadata={'workspaceHandoff':1,'releaseId':output.name,'builtAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'releaseProfile':state['release_profile'],'nativeVersion':state['native_version']}
        state['artifact_audit']=release_profiles.verify_bundle(resources,profile)
        release_version.verify(bundle, state['native_version'])
        if account_config.require(resources) != account_project:
            raise RuntimeError('The bundled account project differs from the requested public configuration.')
        (resources/'runtime/dervo-preview.json').write_text(json.dumps(metadata))
        # Developer ID, notarised and stapled when Bryce's certificate and the dervo-notary profile are in the login
        # Keychain; ad hoc otherwise (release_signing.py). Nothing in the bundle changes after this point.
        import release_signing
        state['signing']=release_signing.sign(bundle,run=run,output=output,work=work,config=config,
                                              inspect=lambda command,_env=None:release_signing.inspect(command,env,storage.child_fds()+release_fds))
        if state['signing']['mode'] == 'developer-id':
            run('native-keychain-check', [str(bundle/'Contents/MacOS/dervo-local'), '--keychain-self-check'], work, timeout=60)
            state['native_keychain'] = {'state': 'passed', 'scope': 'disposable fixture only'}
        save()
        state.update(state='built',step='Ready to preview',bundle=str(bundle),built_at=metadata['builtAt'],executable_sha256=hashlib.sha256((bundle/'Contents/MacOS/dervo-local').read_bytes()).hexdigest(),
                     verification='Feature checks and compilation passed. '+state['signing']['detail']+' Native interaction and real provider use still need testing. Drafts and saved conversations persist in the preview workspace. Appearance preferences reset on close. No installation performed.')
        import shlex
        launcher=output/'Open preview.command'
        launcher.write_text('#!/bin/zsh\nexport DERVO_DATA_DIR='+shlex.quote(str(output/'preview-data'))+'\nunset DERVO_PYTHON\nexec '+shlex.quote(str(bundle/'Contents/MacOS/dervo-local'))+'\n')
        launcher.chmod(0o755)
        # Last, so a build that fails at any step before this never reaches the source plan.
        if stamped is not None:
            state['beta_plan']=record_build_in_source(source,work,plan_before)
        save()
    except Exception as exc:
        state.update(state='failed',error=str(exc));save();raise
    return state

def watch_parent(fd):
    # Only the host owns the writing end. A crash closes it without needing saved PIDs.
    import signal
    import threading
    def watch():
        try:
            while os.read(fd, 1): pass
        finally:
            platform_os.signal_own_group(signal.SIGKILL)
    threading.Thread(target=watch, daemon=True).start()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True);parser.add_argument('--config',required=True)
    parser.add_argument('--parent-fd',type=int)
    parser.add_argument('--profile',choices=['private-beta','personal-experimental'],default='private-beta')
    args=parser.parse_args()
    if args.parent_fd is not None: watch_parent(args.parent_fd)
    build(args.source,args.output,json.loads(Path(args.config).read_text()),profile=args.profile)
