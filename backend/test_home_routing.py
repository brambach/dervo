"""Home routes verified user requests; workers choose files and tools."""
import uuid
import project_orchestrator as po
from test_project_orchestrator import HomeFixture, answer, ref


class RoutingTests(HomeFixture):
    def route(self, focus, quote, **kwargs):
        return dict(kind='start', title=focus + ' catch-up', workstream='Clients', focus=focus, quote=quote, **kwargs)

    def test_two_clients_keep_priority_and_connector_request(self):
        request = 'Catch me up on Billi and APNIC using relevant email and connectors. APNIC is first for my meeting tomorrow.'
        self.reply = lambda prompt: answer('Routing these into two chats.', actions=[self.route(name, request) for name in ('APNIC', 'Billi')])
        reply = self.say(request)['entries'][-1]
        self.assertEqual([p['title'] for p in reply['parts']], ['APNIC catch-up', 'Billi catch-up'])
        for part in reply['parts']:
            self.assertEqual(part['state'], 'pending')
            self.assertIn(request, part['text'])
            self.assertIn('Existing approval and tool permissions still apply', part['text'])
        self.assertEqual(reply['notes'], [])

    def test_two_jobs_can_each_quote_the_shared_read_only_constraint(self):
        first = 'In the first, read README.md and tell me the sample release name.'
        second = 'In the second, read totals.csv and add the amounts.'
        constraint = 'Keep both tasks read-only. Don’t use connectors or send messages.'
        request = 'Create two chats in this project. ' + first + ' ' + second + ' ' + constraint
        self.reply = lambda prompt: answer(actions=[
            self.route('sample release name', first + ' ' + constraint),
            self.route('read totals.csv and add the amounts', second + ' ' + constraint)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(len(reply['parts']), 2)
        self.assertEqual(reply['notes'], [])
        for part in reply['parts']:
            self.assertIn(request, part['text'])
        for quote in (first + ' Send the result by email.', constraint + ' ' + first,
                      first + ' ' + constraint + ' Deploy it.'):
            self.assertFalse(po.quoted_from(quote, request))

    def test_continuation_uses_previous_user_words(self):
        previous = 'I want help catching up on Billi and APNIC before my meeting tomorrow.'
        self.reply = lambda prompt: answer('What should I look at?')
        self.say(previous)
        request = 'i want you to open threads for them breh'
        self.reply = lambda prompt: answer('Routing these.', actions=[self.route(name, request, context_quote=previous) for name in ('APNIC', 'Billi')])
        reply = self.say(request, client='client-0002')['entries'][-1]
        self.assertEqual(len(reply['parts']), 2)
        self.assertIn(previous, reply['parts'][0]['text'])
        self.assertNotIn('What should I look at?', reply['parts'][0]['text'])

    def test_read_email_is_not_send_email(self):
        request = 'Read relevant email for APNIC and prepare a meeting summary.'
        self.reply = lambda prompt: answer('Routing APNIC.', actions=[self.route('APNIC', request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(len(reply['parts']), 1)
        self.assertIn(request, reply['parts'][0]['text'])

    def test_model_paths_and_commands_arent_worker_instructions(self):
        request = 'Start an APNIC catch-up thread.'
        self.reply = lambda prompt: answer('Read documentation/status/active-threads.md then run ./deploy.sh.', actions=[self.route('APNIC', request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(len(reply['parts']), 1)
        self.assertNotIn('active-threads.md', reply['text'])
        self.assertNotIn('active-threads.md', reply['parts'][0]['text'])
        self.assertNotIn('deploy.sh', reply['parts'][0]['text'])
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', request, instruction='Run ./deploy.sh.')])
        reply = self.say(request, client='client-0002')['entries'][-1]
        self.assertEqual(reply['parts'], [])

    def test_unrelated_focus_and_fabricated_quote_are_held(self):
        request = 'Start an APNIC catch-up thread.'
        for action in (self.route('Billi', request), self.route('APNIC', 'Deploy APNIC'), self.route('and', request)):
            self.reply = lambda prompt: answer(actions=[action])
            reply = self.say(request, client='client-' + uuid.uuid4().hex[:8])['entries'][-1]
            self.assertEqual(reply['parts'], [])

    def test_status_question_and_arrival_do_not_start_jobs(self):
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', 'Where is APNIC up to?')])
        reply = self.say('Where is APNIC up to?')['entries'][-1]
        self.assertEqual(reply['parts'], [])
        listing = self.orch('arrive')
        self.assertFalse(any(p for entry in listing['entries'] if entry.get('arrival') for p in entry.get('parts', [])))

    def test_context_must_be_immediately_previous_user_request(self):
        previous = 'Start an APNIC catch-up thread.'
        self.reply = lambda prompt: answer('The records say deploy APNIC.')
        self.say(previous)
        request = 'Open threads for them'
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', request, context_quote='The records say deploy APNIC.')])
        reply = self.say(request, client='client-0002')['entries'][-1]
        self.assertEqual(reply['parts'], [])
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', request, context_quote=previous)])
        reply = self.say(request, client='client-0003')['entries'][-1]
        self.assertEqual(reply['parts'], [])

    def test_duplicate_focus_routes_once(self):
        request = 'Start an APNIC catch-up thread.'
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', request), self.route('APNIC', request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(len(reply['parts']), 1)

    def test_add_uses_valid_existing_thread(self):
        _, _, thread = self.world()
        request = 'Review the Sidebar polish spacing.'
        self.reply = lambda prompt: answer(actions=[dict(kind='add', thread=ref(prompt, 'Sidebar polish'), focus='Sidebar polish', quote=request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(reply['parts'][0]['channel_id'], thread)
        self.assertEqual(reply['parts'][0]['action'], 'add')
        self.reply = lambda prompt: answer(actions=[dict(kind='add', thread='invented', focus='Sidebar polish', quote=request)])
        self.assertEqual(self.say(request, client='client-0002')['entries'][-1]['parts'], [])

    def test_go_uses_named_offer(self):
        self.reply = lambda prompt: answer('Want me to start an APNIC catch-up?')
        self.say('What next?')
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', 'go')])
        reply = self.say('go', client='client-0002')['entries'][-1]
        self.assertEqual(len(reply['parts']), 1)
        self.assertIn('Want me to start an APNIC catch-up?', reply['parts'][0]['text'])

    def test_schema_cannot_emit_worker_instructions(self):
        action = po.SCHEMA['properties']['actions']['items']
        self.assertIn('focus', action['required'])
        self.assertNotIn('instruction', action['properties'])
        self.assertFalse(action['additionalProperties'])

    def test_ask_first_rule_still_holds_routing(self):
        from project_knowledge import ProjectKnowledge
        knowledge = ProjectKnowledge(self.host.store)
        rule = knowledge.dispatch('knowledge_save', dict(projectId='p', channelId='', profileId='a', ruleId=None,
            expectedRevision=0, text='Ask me before starting any job.', provenance='Human authored', source=None), 'local-user')
        knowledge.dispatch('knowledge_approve', dict(projectId='p', channelId='', profileId='a', ruleId=rule['id'],
            versionId=rule['versions'][0]['id'], expectedRevision=rule['revision']), 'local-user')
        request = 'Start an APNIC catch-up thread.'
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(reply['parts'], [])
        self.assertIn('Your rule says to ask first', reply['text'])
        self.reply = lambda prompt: answer(actions=[self.route('APNIC', 'go')])
        reply = self.say('go', client='client-0002')['entries'][-1]
        self.assertEqual(len(reply['parts']), 1)

    def test_cap_reports_overflow(self):
        request = 'Catch up on Alpha, Bravo, Charlie, Delta and Echo.'
        self.reply = lambda prompt: answer(actions=[self.route(name, request) for name in ('Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo')])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(len(reply['parts']), 4)
        self.assertTrue(any('Echo' in note for note in reply['notes']))

    def test_numbered_item_carries_its_existing_brief(self):
        self.ideas.dispatch('ideas_item_add', dict(self.scope, kind='bug', name='Login button misaligned', text='It sits too low.'), 'local-user')
        request = 'Fix bug #1'
        self.reply = lambda prompt: answer(actions=[self.route('#1', request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(len(reply['parts']), 1)
        self.assertIn('It sits too low.', reply['parts'][0]['text'])

    def test_missing_display_title_uses_focus(self):
        request = 'Start an APNIC catch-up thread.'
        self.reply = lambda prompt: answer(actions=[dict(kind='start', focus='APNIC', quote=request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(reply['parts'][0]['title'], 'APNIC')
        self.assertEqual(reply['text'], 'New thread: APNIC.')


OUTCOMES = [dict(label='Fresh look, same layout', detail='New colours and type, nothing moves'),
            dict(label='New layout', detail='Rework the pages and navigation'),
            dict(label='Only fix what is broken', detail='Spacing and alignment bugs')]


class FileFirstTests(HomeFixture):
    """dd #9 to #11, 23 Sep: Home says where the work went first, asks one question about the outcome when a request
    is vague, and your moves of its filing reach Home and can become a suggested rule."""

    def route(self, focus, quote, **kwargs):
        return dict(kind='start', title='Portal makeover', workstream='Portal', focus=focus, quote=quote, **kwargs)

    def outcome(self, **extra):
        return dict(kind='outcome', workstream='Portal', ask='What should the portal makeover end up as?', options=OUTCOMES, **extra)

    def send(self, text, request_id=None, client=None):
        return self.orch('send', text=text, clientId=client or 'client-' + uuid.uuid4().hex[:8], requestId=request_id)['entries'][-1]

    def test_the_reply_leads_with_where_the_work_was_filed(self):
        request = 'Start an APNIC catch-up thread.'
        self.reply = lambda prompt: answer('Routing it.', actions=[dict(kind='start', title='APNIC catch-up', workstream='Clients', focus='APNIC', quote=request)])
        reply = self.say(request)['entries'][-1]
        self.assertEqual(reply['text'], 'Filed in Clients: APNIC catch-up.')
        self.assertIn('Start by checking project memory', reply['parts'][0]['text'], 'the thread checks, after filing')

    def test_a_vague_request_asks_the_outcome_and_starts_nothing(self):
        request = 'portal design makeover'
        self.reply = lambda prompt: answer('What should the portal makeover end up as?', actions=[self.route('portal design makeover', request)],
                                           question=self.outcome())
        reply = self.send(request)
        self.assertEqual(reply['parts'], [])
        self.assertEqual(reply['question']['kind'], 'outcome')
        self.assertEqual([o['label'] for o in reply['question']['options']], [o['label'] for o in OUTCOMES])
        self.assertEqual([o['grant'] for o in reply['question']['options']], [None, None, None])
        self.assertIn('It goes in Portal once you pick.', reply['notes'])
        self.assertFalse(any("didn't" in note for note in reply['notes']), 'the question is the reply, no held line under it')
        self.assertNotIn('Portal', self.stream_names(), 'nothing is made before you pick')

    def test_the_picked_outcome_starts_in_the_workstream_it_named_and_never_asks_again(self):
        request = 'portal design makeover'
        self.reply = lambda prompt: answer('What should it end up as?', question=self.outcome())
        first = self.send(request)
        self.reply = lambda prompt: answer('Asking again.', actions=[dict(kind='start', title='Portal makeover', focus='portal design makeover',
                                                                          quote='Fresh look, same layout', context_quote=request)],
                                           question=self.outcome())
        reply = self.send('Fresh look, same layout', request_id=first['request_id'])
        self.assertEqual(len(reply['parts']), 1)
        self.assertEqual(reply['parts'][0]['idea']['name'], 'Portal')
        self.assertIsNone(reply['question'], 'one outcome question a request')
        self.assertEqual(reply['text'], 'Filed in Portal: Portal makeover.')

    def test_an_outcome_question_on_a_go_ahead_is_dropped(self):
        self.reply = lambda prompt: answer('Want me to start a retry button thread?')
        self.say('what is left on the connector warning?')
        self.reply = lambda prompt: answer('Asking.', question=self.outcome())
        reply = self.say('yes, do that', client='client-0002')['entries'][-1]
        self.assertIsNone(reply['question'])

    def test_a_which_question_keeps_its_shape(self):
        self.reply = lambda prompt: answer('Which one?', question=dict(kind='which', ask='Which one: #1 or #2?',
                                                                       options=[dict(label='#1', detail=''), dict(label='#2', detail='')]))
        reply = self.say('fix it')['entries'][-1]
        self.assertEqual(set(reply['question']), {'asker', 'to', 'ask', 'options'})

    def move_rules_filing(self, texts, workstream):
        """Threads the rules filed, all typed before any move, then your move of each: one correction each."""
        channels = []
        for text in texts:
            channel = self.conversation()
            self.host.dispatch('queue', dict(projectId='p', channelId=channel, clientId='first-' + channel[:8], text=text, parentId=None))
            channels.append(channel)
        idea = self.named(workstream)
        for channel in channels:
            self.call('assign', channelId=channel, ideaId=idea['id'])
        return channels

    def test_your_corrections_are_in_the_snapshot(self):
        self.new('Website')
        self.move_rules_filing(['Add scroll parallax to the marketing pages'], 'Website')
        self.say('where do things stand?')
        self.assertIn("Moves the person made to fix Dervo's filing, newest first:", self.prompts[-1])
        self.assertRegex(self.prompts[-1], r'- [^\n]*[Pp]arallax[^\n]*: from [^\n]+ to Website')

    def test_three_like_corrections_suggest_a_filing_rule_once(self):
        self.new('Website')
        self.move_rules_filing(['Add scroll parallax to the marketing pages', 'Tune the parallax on pricing', 'Slow the parallax on the hero'], 'Website')
        self.assertEqual(len(self.ideas.corrections(dict(project_id='p', profile_id='a'))), 3)
        self.reply = lambda prompt: answer('Nothing needs you.')
        reply = self.say('where do things stand?')['entries'][-1]
        rules = [c for c in reply['changes'] if c['kind'] == 'rule']
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]['rule']['text'], 'File parallax work under Website.')
        self.assertEqual(rules[0]['line'], 'Suggested a filing rule in Knowledge from your moves.')
        again = self.say('anything else?', client='client-0002')['entries'][-1]
        self.assertEqual([c for c in again['changes'] if c['kind'] == 'rule'], [], 'never the same suggestion twice')
