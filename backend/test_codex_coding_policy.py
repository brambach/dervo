"""Coding policy is separate from ordinary chat and rejects fixture overrides."""
import copy
import unittest
from adapters import CodexAdapter
from codex_coding import CodexCodingAdapter, TrustedTaskBinding
from codex_policy import POLICY as CHAT_POLICY, for_folder
from test_codex_coding import BINDING, INIT


def expected_policy():
    policy=copy.deepcopy(CHAT_POLICY)
    policy['features']['code_mode_host']={'enabled':True,'disable_in_process_fallback':True}
    policy['agents']={'enabled':False}
    return policy

class CodingPolicyTests(unittest.TestCase):
    def test_full_access_is_an_exact_separate_policy_and_keeps_integrations_off(self):
        from codex_coding_policy import policy_for, verify_config
        from dervo_connections import Rejected
        full = policy_for(True)
        self.assertEqual((full['sandbox_mode'], full['approval_policy']), ('danger-full-access', 'never'))
        for feature in ('shell_tool', 'unified_exec'):
            self.assertIs(full['features'][feature], True)
        for feature in ('apps', 'connectors', 'plugins', 'hooks', 'computer_use', 'multi_agent'):
            self.assertIs(full['features'][feature], False)
        base = {'config': copy.deepcopy(full), 'layers': [{'name': {'type': 'sessionFlags'}, 'config': full}]}
        verify_config(base, full_access=True)
        with self.assertRaises(Rejected):
            verify_config(base)
        restricted = policy_for()
        with self.assertRaises(Rejected):
            verify_config({'config': restricted, 'layers': [{'name': {'type': 'sessionFlags'}, 'config': restricted}]}, full_access=True)
        for key, value in [('mcp_servers', {'unexpected': {'command': 'never'}}), ('approval_policy', 'on-request')]:
            changed = copy.deepcopy(base)
            changed['config'][key] = value
            with self.subTest(key=key), self.assertRaises(Rejected):
                verify_config(changed, full_access=True)

    def test_coding_launch_uses_only_coding_policy_differences(self):
        import tomllib
        import codex_coding
        from codex_policy import argv as chat_argv
        args=codex_coding.argv()
        self.assertEqual(args[0],chat_argv()[0])
        suffix=['app-server','--listen','stdio://','--strict-config']
        self.assertEqual(args[-len(suffix):],suffix)
        parsed={}
        for i in range(1,len(args)-4,2):
            self.assertEqual(args[i],'-c')
            parsed.update(tomllib.loads(args[i+1]))
        self.assertEqual(parsed,expected_policy())
        self.assertIs(CHAT_POLICY['features']['code_mode_host'],False)
        self.assertNotIn('agents',CHAT_POLICY)

    def test_effective_coding_controls_are_exact(self):
        from codex_coding_policy import verify_config
        from dervo_connections import Rejected
        for key,value in [('code_mode_host',{'enabled':1,'disable_in_process_fallback':True}),
                          ('agents',{'enabled':False,'default_subagent_model':'unexpected'})]:
            with self.subTest(key=key):
                policy=expected_policy()
                result={'config':copy.deepcopy(policy),'layers':[{'name':{'type':'sessionFlags'},'config':policy}]}
                if key=='agents':result['config']['agents']=value
                else:result['config']['features'][key]=value
                with self.assertRaises(Rejected):verify_config(result)

    def test_policy_modes_and_strict_provenance_cannot_be_crossed(self):
        from codex_coding_policy import verify_config
        from codex_policy import verify_config as verify_chat
        from dervo_connections import Rejected
        policy=expected_policy()
        base={'config':copy.deepcopy(policy),'layers':[{'name':{'type':'sessionFlags'},'config':policy}]}
        with self.assertRaises(Rejected):verify_chat(base)
        chat={'config':copy.deepcopy(CHAT_POLICY),'layers':[{'name':{'type':'sessionFlags'},'config':copy.deepcopy(CHAT_POLICY)}]}
        with self.assertRaises(Rejected):verify_config(chat)
        cases=[]
        def change(where,key,value):
            case=copy.deepcopy(base)
            target=case
            for part in where:target=target[part]
            target[key]=value
            cases.append(case)
        for key,value in [('model_provider','synthetic'),('forced_login_method',None),
                          ('model_catalog_json','/tmp/catalog.json'),
                          ('model_providers',{'synthetic':{'requires_openai_auth':False}}),
                          ('sandbox_mode','danger-full-access'),('mcp_servers',{'bad':{}}),
                          ('hooks',{'bad':{}}),('notify',['touch','bad'])]:
            change(['config'],key,value)
            change(['layers',0,'config'],key,value)
        for key,value in [('enabled',False),('disable_in_process_fallback',False),('extra',True)]:
            change(['config','features','code_mode_host'],key,value)
        change(['config','agents'],'enabled',True)
        change(['config','features'],'shell_tool',True)
        change(['config','shell_environment_policy'],'inherit','all')
        for kind in ('user','system','project'):
            c=copy.deepcopy(base)
            c['layers'].append({'name':{'type':kind},'config':{'hooks':{'bad':{}}}})
            cases.append(c)
        cases.extend([{'config':base['config'],'layers':[]},
                      {'config':base['config'],'layers':base['layers']*2}])
        for index,case in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(Rejected):verify_config(case)
        original=copy.deepcopy(base)
        verify_config(base)
        self.assertEqual(base,original,'Verifier must not alter protocol evidence')

    def test_only_disabled_project_layers_are_ignored_with_effective_policy_checked(self):
        from codex_coding_policy import verify_config
        from codex_policy import verify_config as verify_chat
        from dervo_connections import Rejected
        for policy, verifier in [(expected_policy(), verify_config), (copy.deepcopy(CHAT_POLICY), verify_chat)]:
            base={'config':copy.deepcopy(policy),'layers':[
                {'name':{'type':'sessionFlags'},'config':copy.deepcopy(policy)},
                {'name':{'type':'project'},'config':{'mcp_servers':{'fixture':{'command':'never-run'}}},
                 'disabledReason':'Project is not trusted'}]}
            original=copy.deepcopy(base)
            verifier(base)
            self.assertEqual(base,original)
            for reason in (None,'','   ',True,{}):
                case=copy.deepcopy(base);case['layers'][1]['disabledReason']=reason
                with self.subTest(reason=reason),self.assertRaises(Rejected): verifier(case)
            for kind in ('user','system','unknown'):
                case=copy.deepcopy(base);case['layers'][1]['name']['type']=kind
                with self.subTest(kind=kind),self.assertRaises(Rejected): verifier(case)
            for key,value in [('mcp_servers',{'fixture':{'command':'never-run'}}),('sandbox_mode','danger-full-access'),('hooks',{'fixture':{}})]:
                case=copy.deepcopy(base);case['config'][key]=value
                with self.subTest(key=key),self.assertRaises(Rejected): verifier(case)
            case=copy.deepcopy(base);case['config']['features']['shell_tool']=True
            with self.assertRaises(Rejected): verifier(case)

    def test_chat_adapter_still_uses_chat_policy_and_no_caller_verifier(self):
        from dervo_connections import Rejected
        sent=[]
        chat=CodexAdapter(BINDING,sent.append,lambda *_:None)
        chat.connect()
        chat.receive({'id':sent[-1]['id'],'result':INIT})
        launched=for_folder(CHAT_POLICY,BINDING['cwd'])
        result={'config':copy.deepcopy(launched),'layers':[{'name':{'type':'sessionFlags'},'config':copy.deepcopy(launched)}]}
        chat.receive({'id':sent[-1]['id'],'result':result})
        self.assertEqual(sent[-1]['method'],'account/read')
        task=TrustedTaskBinding('policy-test',BINDING,lambda *_:True)
        with self.assertRaises(TypeError):
            CodexCodingAdapter(BINDING,sent.append,lambda *_:None,task=task,verify_config=lambda _:None)

    def test_coding_adapter_accepts_exact_coding_policy(self):
        sent=[]
        task=TrustedTaskBinding('policy-test',BINDING,lambda *_:True)
        adapter=CodexCodingAdapter(BINDING,sent.append,lambda *_:None,task=task)
        adapter.connect()
        adapter.receive({'id':sent[-1]['id'],'result':INIT})
        policy=for_folder(expected_policy(),BINDING['cwd'])
        result={'config':policy,'layers':[{'name':{'type':'sessionFlags'},'config':policy}]}
        try:
            adapter.receive({'id':sent[-1]['id'],'result':result})
        except Exception as exc:
            self.fail('Exact coding policy must pass its own preflight: '+str(exc))
        self.assertEqual(adapter.state,'authenticating')
        self.assertEqual(sent[-1]['method'],'account/read')

if __name__=='__main__':unittest.main()
