import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from services.business_api.app import BusinessApi
from services.business_api.db import Database
from services.business_api.studio import Studio


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database()
        self.actor = self.database.create_account('admin', 'test-password-123', True)
        self.lock = threading.RLock()
        self.studio = Studio(BusinessApi(self.database), Path(self.temporary.name), self.lock)
        self.task = self.database.create_task(self.actor['id'], 'Fixture')

    def tearDown(self):
        self.studio.close()
        self.database.close()
        self.temporary.cleanup()

    def test_managed_tasks_cannot_bypass_workflow(self):
        self.database.connection.execute('CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY)')
        self.database.connection.execute('INSERT INTO workflow_tasks VALUES (?)', (self.task['id'],))
        self.database.connection.commit()
        with self.assertRaises(ValueError):
            self.studio.generate(self.actor, self.task['id'], {'text': 'test', 'duration': 7})

    def test_members_cannot_authorize_or_publish(self):
        member = self.database.create_account('member', 'test-password-456')
        with self.assertRaises(PermissionError):
            self.studio.authorize(member)
        with self.assertRaises(PermissionError):
            self.studio.publish(member, self.task['id'], {'confirmed': True})

    def test_oauth_state_bound_to_actor_and_one_time(self):
        self.studio.provider.key = 'test'
        self.studio.provider.secret = 'test'
        self.studio.provider.redirect = 'https://example.com/api/v1/studio/douyin/callback'
        from urllib.parse import parse_qs, urlparse
        url = self.studio.authorize(self.actor)['url']
        state = parse_qs(urlparse(url).query)['state'][0]
        exchange = Mock(return_value={'access_token': 'secret-token', 'open_id': 'account-id', 'expires_in': 3600})
        self.studio.provider.exchange = exchange
        self.studio.callback(self.actor, {'state': state, 'code': 'code'})
        self.assertTrue(self.studio.capabilities(self.actor)['douyin']['connected'])
        self.assertNotIn('secret-token', json.dumps(self.studio.capabilities(self.actor)))
        with self.assertRaises(ValueError):
            self.studio.callback(self.actor, {'state': state, 'code': 'code'})
        self.assertEqual(exchange.call_count, 1)

    def test_startup_marks_interrupted_jobs_and_publications(self):
        self.studio.connection.execute('INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?)', ('job', self.task['id'], 'UPLOAD', 'RUNNING', None, None, time.time()))
        self.studio.connection.execute('INSERT INTO studio_publications VALUES (?,?,?,?,?,?,?,?,?,?)', ('publication', self.task['id'], 'media', 'account', '2026-09-23', 'SUBMITTING', 'caption', 'private', None, time.time()))
        self.studio.connection.commit()
        self.studio.close()
        self.studio = Studio(BusinessApi(self.database), Path(self.temporary.name), self.lock)
        detail = self.studio.detail(self.task['id'])
        self.assertEqual(detail['jobs'][0]['status'], 'FAILED')
        self.assertEqual(detail['publications'][0]['status'], 'UNKNOWN')
        with self.assertRaises(ValueError):
            self.studio.ensure_mutable(self.task['id'])

    def test_uncertain_platform_result_is_not_success(self):
        self.studio.connection.execute('INSERT INTO studio_publications VALUES (?,?,?,?,?,?,?,?,?,?)', ('publication', self.task['id'], 'media', 'account', '2026-09-23', 'SUBMITTING', 'caption', 'private', None, time.time()))
        self.studio.connection.commit()
        self.studio.provider.publish = Mock(side_effect=TimeoutError('secret'))
        self.studio._publish('publication', {}, Path('unused'), 'caption', 'private')
        record = self.studio.detail(self.task['id'])['publications'][0]
        self.assertEqual(record['status'], 'UNKNOWN')
        self.assertNotIn('secret', record['result'])
        self.assertEqual(self.studio.provider.publish.call_count, 1)


if __name__ == '__main__':
    unittest.main()
