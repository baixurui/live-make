import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from services.business_api.app import BusinessApi
from services.business_api.bailian import BailianError
from services.business_api.db import Database
from services.business_api.studio import Studio


class AiStudioTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'offline-test-key'})
        self.environment.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database()
        self.actor = self.database.create_account('admin', 'test-password-123', True)
        self.lock = threading.RLock()
        self.studio = Studio(BusinessApi(self.database), Path(self.temporary.name), self.lock)
        self.task = self.database.create_task(self.actor['id'], 'AI fixture')
        self.provider = self.studio.ai.provider

    def tearDown(self):
        self.studio.close()
        self.database.close()
        self.temporary.cleanup()
        self.environment.stop()

    def start(self, stage, body):
        with self.lock:
            return self.studio.ai.start(self.actor, self.task['id'], stage, dict(body, confirmed=True))

    def wait(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.lock:
                record = dict(self.database.connection.execute('SELECT * FROM studio_ai_jobs WHERE id=?', (job_id,)).fetchone())
            if record['status'] not in {'QUEUED', 'SUBMITTING', 'RUNNING'}:
                return record
            time.sleep(.03)
        self.fail('AI job did not settle')

    def test_missing_key_and_confirmation_never_enqueue(self):
        with self.assertRaises(ValueError):
            self.studio.ai.start(self.actor, self.task['id'], 'text', {'topic': 'idea'})
        self.provider.key = ''
        with self.assertRaises(ValueError):
            self.start('text', {'topic': 'idea'})

    def test_text_then_image_saved_locally(self):
        self.provider.text = Mock(return_value={'script': '文案', 'caption': '标题', 'image_prompt': '画面', 'video_prompt': '推进'})
        job = self.start('text', {'topic': 'idea'})
        self.assertEqual(self.wait(job['id'])['status'], 'SUCCEEDED')
        self.provider.image = Mock(return_value='remote-1')
        self.provider.query = Mock(return_value={'task_status': 'SUCCEEDED', 'results': [{'url': 'https://result.aliyuncs.com/image.png'}]})
        self.provider.download = lambda url, destination, limit: Image.new('RGB', (720, 1280), 'blue').save(destination, format='PNG')
        job = self.start('image', {'prompt': 'edited picture description'})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        detail = self.studio.detail(self.task['id'])['ai']
        self.assertEqual(len(detail['images']), 1)
        self.assertTrue(self.studio.ai.file(detail['images'][0]['id']).is_file())
        self.provider.image.assert_called_once()

    def test_unknown_submission_blocks_paid_retry(self):
        self.provider.image = Mock(side_effect=BailianError('Unknown', uncertain=True))
        job = self.start('image', {'prompt': 'picture'})
        self.assertEqual(self.wait(job['id'])['status'], 'UNKNOWN')
        with self.assertRaises(ValueError):
            self.start('image', {'prompt': 'picture'})
        with self.assertRaises(ValueError):
            self.studio.ai.acknowledge(self.actor, self.task['id'], {'job_id': job['id']})
        self.studio.ai.acknowledge(self.actor, self.task['id'], {'job_id': job['id'], 'confirmed': True})
        self.assertEqual(self.provider.image.call_count, 1)

    def test_resume_queries_existing_remote_without_resubmission(self):
        self.provider.image = Mock(return_value='remote-recover')
        self.provider.query = Mock(side_effect=BailianError('poll failed'))
        job = self.start('image', {'prompt': 'picture'})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'WAITING')
        self.assertEqual(result['remote_id'], 'remote-recover')
        self.provider.query = Mock(return_value={'task_status': 'FAILED', 'code': 'ContentRejected'})
        with self.lock:
            self.studio.ai.resume(self.actor, self.task['id'], {'job_id': job['id']})
        self.assertEqual(self.wait(job['id'])['status'], 'FAILED')
        self.provider.image.assert_called_once()

    def test_video_requires_current_task_image(self):
        with self.assertRaises(ValueError):
            self.start('video', {'prompt': 'motion', 'image_id': 'foreign', 'duration': 8})

    def test_restart_preserves_remote_id_and_unknown_submission(self):
        connection = self.database.connection
        for job_id, remote_id in [('known', 'remote-kept'), ('unknown', None)]:
            connection.execute('INSERT INTO studio_ai_jobs VALUES (?,?,?,?,?,?,?,?,?,?)', (job_id, self.task['id'], self.actor['id'], 'image', 'SUBMITTING', remote_id, '{}', None, None, time.time()))
        connection.commit()
        self.studio.close()
        self.studio = Studio(BusinessApi(self.database), Path(self.temporary.name), self.lock)
        self.assertEqual(connection.execute("SELECT status FROM studio_ai_jobs WHERE id='known'").fetchone()[0], 'WAITING')
        self.assertEqual(connection.execute("SELECT remote_id FROM studio_ai_jobs WHERE id='known'").fetchone()[0], 'remote-kept')
        self.assertEqual(connection.execute("SELECT status FROM studio_ai_jobs WHERE id='unknown'").fetchone()[0], 'UNKNOWN')

    def test_shutdown_never_starts_queued_paid_request(self):
        self.provider.text = Mock()
        self.studio.ai.stopping.set()
        job = self.start('text', {'topic': 'idea'})
        self.assertEqual(self.wait(job['id'])['status'], 'FAILED')
        self.provider.text.assert_not_called()
