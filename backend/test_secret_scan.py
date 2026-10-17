"""secret_scan on its own: the shapes it names and the ones it leaves alone. No store, no provider.

Security review 20 Sep 2026, M4, added the lowercase hex key named as a key, DigitalOcean, SendGrid and Discord tokens
whole, an Azure connection string's key, and a plain-word password after "password is". Each has a negative control:
a commit SHA, a digest, a word that describes the password rather than being it.
"""
import unittest

import secret_scan


class FindTests(unittest.TestCase):
    def test_a_hex_key_named_as_a_key_is_found_and_a_hash_or_commit_is_not(self):
        for text in ('the mailgun key is 3f2a9c1b7d4e5f60718293a4b5c6d7e8',
                     'Use API key 7c9e6679f4d24b6a8f1e2d3c4b5a69788c for staging',
                     'twilio auth token 0123456789abcdef0123456789abcdef',
                     'deploy token 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b',
                     'secret: 3f2a9c1b7d4e5f60718293a4b5c6d7e8a1b2c3d4e5f60718293a4b5c6d7e8a1'):
            self.assertEqual(secret_scan.find(text), 'API key', text)
            self.assertIn('[redacted]', secret_scan.redact(text))
            self.assertNotIn(text.split()[-1] if 'staging' not in text else '7c9e6679f4d24b6a8f1e2d3c4b5a69788c', secret_scan.redact(text))
        # Negative controls: the same runs named as what they are, or with no key word near them.
        for text in ('reverted in commit 3f2a9c1b7d4e5f60718293a4b5c6d7e8a1b2c3d4',
                     'the sha256 is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                     'the token change landed in commit 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b',
                     'password hash is 5f4dcc3b5aa765d61d8327deb882cf99',
                     'token id 3f2a9c1b7d4e5f60718293a4b5c6d7e8',
                     'key 00000000000000000000000000000000',
                     'blob 3f2a9c1b7d4e5f60718293a4b5c6d7e8a1b2c3d4 holds the key file',
                     'the token bucket is in limits.py',
                     'the key is in .env.local'):
            self.assertIsNone(secret_scan.find(text), text)
            self.assertEqual(secret_scan.redact(text), text, text)

    def test_provider_token_shapes_are_found_whole(self):
        digitalocean = 'dop_v1_' + 'ab12' * 16
        sendgrid = 'SG.abcdefghijklmnopqrstuv.wxyz0123456789ABCDEFGHIJKLMNOPQRSTUV'
        discord = 'MTIzNDU2Nzg5MDEyMzQ1Njc4' + '.GhIjKl.' + 'mnopqrstuvwxyz0123456789ABCDEFGHIJ'  # built in parts so scanners don't flag the fixture
        azure = 'DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=' + 'Ab1' * 29 + 'A==;EndpointSuffix=core.windows.net'
        self.assertEqual(secret_scan.find(digitalocean), 'access token')
        self.assertEqual(secret_scan.find(sendgrid), 'API key')
        self.assertEqual(secret_scan.find(discord), 'access token')
        self.assertEqual(secret_scan.find(azure), 'secret setting')
        # Whole: the first part of a SendGrid or Discord token used to stay in the redacted text.
        self.assertEqual(secret_scan.redact(f'use {sendgrid} here'), 'use [redacted] here')
        self.assertEqual(secret_scan.redact(f'token {discord} end'), 'token [redacted] end')
        self.assertEqual(secret_scan.redact(azure), 'DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=[redacted];EndpointSuffix=core.windows.net')
        self.assertEqual(secret_scan.redact(digitalocean), '[redacted]')
        # Negative controls: a placeholder key, a short SG word, a DigitalOcean prefix with no token behind it.
        for text in ('AccountKey=changeme', 'AccountKey=${AZURE_KEY}', 'SG.short.short', 'dop_v1_ is the prefix', 'the SG. line'):
            self.assertIsNone(secret_scan.find(text), text)

    def test_a_plain_word_password_counts_and_a_word_about_the_password_does_not(self):
        for text in ('the db password is swordfish', 'password is hunter2', 'the admin passphrase is correcthorse', 'pwd: swordfish'):
            self.assertEqual(secret_scan.find(text), 'password', text)
            self.assertNotIn(text.split()[-1], secret_scan.redact(text), text)
        for text in ('the password is required', 'the password is stored in 1password', 'the password is wrong',
                     'the password is hashed', 'the password is expiring', 'the password is optional', 'the password is different',
                     'the password is in .env.local', 'password is the same as staging', 'the password is unknown',
                     'the password is set in the keychain', 'the password is weekly rotated'):
            self.assertIsNone(secret_scan.find(text), text)
            self.assertEqual(secret_scan.redact(text), text, text)

    def test_the_older_shapes_still_hold(self):
        # Negative control for the review's additions: nothing that saved before is refused now, and the refusals stay.
        for text in ('the token bucket is in limits.py', 'sk-button is the CSS class', 'commit 3f2a9c1b7d4e5f60718293a4b5c6d7e8a1b2c3d4 fixed it',
                     'API_KEY=${OPENAI_KEY}', 'the deploy key lives in 1password'):
            self.assertIsNone(secret_scan.find(text), text)
        for text, kind in (('ghp_' + 'a1B2' * 6, 'GitHub token'), ('AKIA' + 'A1B2C3D4E5F6G7H8', 'AWS key'),
                           ('OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234', 'API key'), ('password: hunter', 'password')):
            self.assertEqual(secret_scan.find(text), kind, text)
        self.assertEqual(secret_scan.redact_value({'a': ['Bearer abcdefghij1234567890'], 'b': 3}), {'a': ['[redacted]'], 'b': 3})
        self.assertIsNone(secret_scan.find(None))
        self.assertEqual(secret_scan.redact(''), '')


if __name__ == '__main__':
    unittest.main()
