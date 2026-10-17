"""Native selection and creation boundary, using disposable folders and accounts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from host import Host
from project_setup import stage,save
from dervo_connections import Rejected

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        self.host=Host(self.root/'data');self.addCleanup(lambda:self.host.close())
        self.profile=self.host.dispatch('add_profile',{'provider':'codex','identity':'fixture@example.test','category':'personal'})['id']
        self.selected=stage(self.host,self.source)
        self.args={'selectionId':self.selected['selectionId'],'profileId':self.profile,'model':'fixture-model','name':'Test project'}
    def test_folder_choice_does_not_create_or_grant_then_save_is_idempotent(self):
        self.assertEqual(self.host.dispatch('projects',{}),[])
        result=save(self.host,self.args);self.assertEqual(save(self.host,self.args),result)
        self.assertEqual(len(self.host.dispatch('projects',{})),1)
        self.assertEqual(self.host.dispatch('projects',{})[0]['name'],'Test project')
        self.assertEqual(self.host.store.db.execute('select count(*) from threads').fetchone()[0],1)
        with self.assertRaises(Rejected):save(self.host,{**self.args,'model':'different'})
    def test_renderer_paths_and_replaced_selection_are_refused(self):
        with self.assertRaises(Rejected):save(self.host,{**self.args,'path':str(self.source)})
        stage(self.host,self.source)
        with self.assertRaises(Rejected):save(self.host,self.args)
        self.assertEqual(self.host.dispatch('projects',{}),[])
    def test_changed_folder_and_expiry_refuse_without_creation(self):
        self.source.rename(self.root/'moved');self.source.mkdir()
        with self.assertRaises(Rejected):save(self.host,self.args)
        self.selected=stage(self.host,self.source);self.args['selectionId']=self.selected['selectionId']
        with patch('project_setup.time.monotonic',return_value=self.host.picked_folder['created']+1801):
            with self.assertRaises(Rejected):save(self.host,self.args)
        self.assertEqual(self.host.dispatch('projects',{}),[])
    def test_home_folder_disk_and_dervo_data_are_refused_when_picked(self):
        """The home folder, a whole disk or a folder holding Dervo's Store can't be staged, so onboarding never makes a
        project its overview can't read (RC review, 9 Oct)."""
        home=self.root/'home';(home/'code'/'app').mkdir(parents=True)
        with patch('onboarding_host.HOME',home):
            for folder,words in [(home,'home folder can’t be a project'),(self.root,'holds your whole home folder'),(Path('/'),'holds your whole home folder')]:
                with self.assertRaises(Rejected) as caught:stage(self.host,folder)
                self.assertIn(words,str(caught.exception))
            # The Store's own folder, and one inside it, hold Dervo's data.
            for folder in [self.root/'data',self.host.root]:
                with self.assertRaises(Rejected) as caught:stage(self.host,folder)
                self.assertIn('Dervo’s own data',str(caught.exception))
            self.assertEqual(stage(self.host,home/'code'/'app')['name'],'app','a project inside the home folder is fine')
        self.assertEqual(self.host.dispatch('projects',{}),[])
    def test_invalid_name_account_and_model_never_create_partial_project(self):
        for changes in [{'name':''},{'name':'a'*81},{'name':'two\nlines'},{'profileId':'unknown'},{'model':' fixture-model '}]:
            with self.assertRaises(Rejected):save(self.host,{**self.args,**changes})
        self.assertEqual(self.host.dispatch('projects',{}),[])
        self.assertEqual(self.host.store.db.execute('select count(*) from project_names').fetchone()[0],0)
    def test_create_rolls_back_if_name_cannot_be_saved(self):
        self.host.store.db.execute("CREATE TRIGGER reject_name BEFORE INSERT ON project_names BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Exception):save(self.host,self.args)
        self.assertEqual(self.host.dispatch('projects',{}),[])
        self.assertIsNone(self.host.picked_folder['saved'])
if __name__=='__main__':unittest.main()
