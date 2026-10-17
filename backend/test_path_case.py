"""scripts/check-path-case.py: paths a Mac or Windows disk would take for the same one, folders and import names too."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check-path-case.py'
spec = importlib.util.spec_from_file_location('check_path_case', SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class PathCaseTests(unittest.TestCase):
    def test_files_folders_and_import_names_that_differ_only_in_case_clash(self):
        self.assertEqual(check.clashes(['a/Readme.md', 'a/README.md']), [['a/README.md', 'a/Readme.md']])
        self.assertEqual(check.clashes(['Foo/a', 'foo/b']), [['Foo', 'foo']])
        self.assertEqual(check.clashes(['src/homeVoice.ts', 'src/HomeVoice.tsx']), [['src/HomeVoice', 'src/homeVoice']])

    def test_distinct_names_and_one_module_in_two_languages_pass(self):
        self.assertEqual(check.clashes(['src/a.ts', 'src/a.test.mjs', 'src/b.tsx', 'src/b.css', 'src/B.css.map']), [])
        self.assertEqual(check.clashes(['src/a.ts', 'src/a.tsx']), [])


if __name__ == '__main__':
    unittest.main()
