import io
import tempfile
import threading
import unittest
from pathlib import Path

from services.business_api.app import BusinessApi
from services.business_api.db import Database
from services.business_api.studio import Studio


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database()
        self.actor = self.database.create_account('admin', 'test-password-123', True)
        self.api = BusinessApi(self.database)
        self.studio = Studio(self.api, Path(self.temporary.name), threading.RLock())
        self.task = self.database.create_task(self.actor['id'], 'Test video')

    def tearDown(self):
        self.studio.close()
        self.database.close()
        self.temporary.cleanup()

    def test_unconfigured_provider_is_explicit(self):
        result = self.studio.capabilities(self.actor)
        self.assertFalse(result['douyin']['connected'])
        self.assertEqual(result['generation']['mode'], 'template')

    def test_upload_rejects_unsupported_extension(self):
        with self.assertRaises(ValueError):
            self.studio.upload(self.actor, self.task['id'], 'bad.exe', io.BytesIO(b'abc'), 3)

    def test_upload_rejects_empty_and_excessive_length(self):
        for length in (0, 101 * 1024 * 1024):
            with self.assertRaises(ValueError):
                self.studio.upload(self.actor, self.task['id'], 'clip.mp4', io.BytesIO(), length)

    def test_review_requires_current_media(self):
        with self.assertRaises(ValueError):
            self.studio.review(self.actor, self.task['id'], {'media_id': 'missing', 'caption': 'caption'})

    def test_blocked_generation_is_rejected(self):
        blocked = self.database.create_task(self.actor['id'], 'Blocked', risk_level='BLOCKED')
        with self.assertRaises(ValueError):
            self.studio.generate(self.actor, blocked['id'], {'text': 'caption', 'duration': 8})

    def test_publish_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):
            self.studio.publish(self.actor, self.task['id'], {'confirmed': False})

    def test_auth_me_and_logout(self):
        login = self.api.request('POST', '/api/v1/auth/login', body={'username': 'admin', 'password': 'test-password-123'})
        headers = {'Authorization': 'Bearer ' + login.body['access_token']}
        self.assertEqual(self.api.request('GET', '/api/v1/auth/me', headers).status, 200)
        self.assertEqual(self.api.request('POST', '/api/v1/auth/logout', headers).status, 204)
        self.assertEqual(self.api.request('GET', '/api/v1/auth/me', headers).status, 403)


if __name__ == '__main__':
    unittest.main()
