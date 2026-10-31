import json
from pathlib import Path
import tempfile
import unittest

from session_cache import cache_scope, persist_state, restore_state


def seed(value):
    return json.dumps({'cookies': [{'domain': 'login.xjtu.edu.cn', 'name': 'example_session', 'value': value}], 'origins': []})


class SessionCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'session.enc'
        self.seed = seed('example_initial_cookie')
        self.state = json.loads(seed('example_updated_cookie'))

    def test_roundtrip_only_persists_authenticated_ciphertext(self):
        persist_state(self.seed, self.path, self.state)
        data = self.path.read_bytes()
        self.assertNotIn(b'example_updated_cookie', data)
        self.assertNotIn(b'login.xjtu.edu.cn', data)
        self.assertEqual(restore_state(self.seed, self.path), self.state)
        self.assertTrue(self.path.with_suffix('.updated').is_file())

    def test_tampered_cache_cannot_replace_the_seed(self):
        persist_state(self.seed, self.path, self.state)
        data = bytearray(self.path.read_bytes())
        data[-1] ^= 1
        self.path.write_bytes(data)
        self.assertEqual(restore_state(self.seed, self.path), json.loads(self.seed))

    def test_rotated_secret_cannot_decrypt_an_old_cache(self):
        persist_state(self.seed, self.path, self.state)
        new_seed = seed('different_initial_cookie')
        self.assertNotEqual(cache_scope(self.seed), cache_scope(new_seed))
        self.assertEqual(restore_state(new_seed, self.path), json.loads(new_seed))

    def test_missing_seed_does_not_export_a_session(self):
        persist_state('', self.path, self.state)
        self.assertFalse(self.path.exists())

    def test_cached_state_is_limited_to_school_domains(self):
        self.state['cookies'].append({'domain': 'other.example', 'name': 'private', 'value': 'private'})
        persist_state(self.seed, self.path, self.state)
        restored = restore_state(self.seed, self.path)
        self.assertEqual(len(restored['cookies']), 1)


if __name__ == '__main__':
    unittest.main()
