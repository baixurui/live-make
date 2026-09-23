import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

from services.business_api import media
from services.business_api.app import BusinessApi
from services.business_api.db import Database
from services.business_api.server import RequestHandler
from services.business_api.studio import Studio


class HttpStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = Database(cls.root / 'test.sqlite3')
        cls.admin = cls.database.create_account('admin', 'test-password-123', True)
        cls.lock = threading.RLock()
        cls.api = BusinessApi(cls.database)
        cls.studio = Studio(cls.api, cls.root / 'media', cls.lock)
        class Handler(RequestHandler):
            pass
        Handler.api, Handler.studio, Handler.lock = cls.api, cls.studio, cls.lock
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.fixture = cls.root / 'fixture.mp4'
        media.render(cls.fixture, '测试视频 · Live Make', 7)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.studio.close()
        cls.database.close()
        cls.temporary.cleanup()

    def setUp(self):
        self.client = requests.Session()
        self.client.headers['X-Studio-Request'] = '1'
        response = self.client.post(self.base + '/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.client.close()

    def create(self, risk='LOW'):
        response = self.client.post(self.base + '/api/v1/tasks', json={'title': 'HTTP fixture', 'risk_level': risk})
        self.assertEqual(response.status_code, 201)
        return response.json()['id']

    def detail(self, task_id):
        return self.client.get(self.base + '/api/v1/studio/tasks/' + task_id).json()

    def wait_job(self, task_id, job_id):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            detail = self.detail(task_id)
            job = next(item for item in detail['jobs'] if item['id'] == job_id)
            if job['status'] not in {'QUEUED', 'RUNNING'}:
                return job, detail
            time.sleep(.15)
        self.fail('job did not finish')

    def upload(self, task_id):
        with self.fixture.open('rb') as source:
            response = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/upload', data=source, headers={'X-Filename': 'fixture.mp4'})
        self.assertEqual(response.status_code, 202, response.text)
        job, detail = self.wait_job(task_id, response.json()['id'])
        self.assertEqual(job['status'], 'SUCCEEDED', job)
        return detail['media'][0]

    def test_static_auth_csrf_and_json_validation(self):
        response = requests.get(self.base + '/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('视频工作台', response.text)
        self.assertIn('HttpOnly', self.client.cookies.get('livemake_session') and self.client.post(self.base + '/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password-123'}).headers['Set-Cookie'])
        self.assertEqual(requests.get(self.base + '/api/v1/studio/capabilities').status_code, 403)
        self.assertEqual(self.client.post(self.base + '/api/v1/tasks', json={'title': 'forged'}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post(self.base + '/api/v1/tasks', json=[]).status_code, 400)
        self.assertEqual(self.client.get(self.base + '/.env').status_code, 404)
        self.assertEqual(self.client.post(self.base + '/api/v1/auth/logout').status_code, 204)
        self.assertEqual(self.client.get(self.base + '/api/v1/auth/me').status_code, 403)

    def test_upload_preview_ranges_and_generation(self):
        task_id = self.create()
        uploaded = self.upload(task_id)
        self.assertEqual(uploaded['metadata']['width'], 720)
        video_url = self.base + uploaded['url']
        self.assertEqual(requests.get(video_url).status_code, 403)
        partial = self.client.get(video_url, headers={'Range': 'bytes=0-31'})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(len(partial.content), 32)
        self.assertIn(b'ftyp', partial.content)
        self.assertEqual(self.client.get(video_url, headers={'Range': 'bytes=999999999-'}).status_code, 416)
        suffix = self.client.get(video_url, headers={'Range': 'bytes=-16'})
        self.assertEqual(len(suffix.content), 16)
        generated = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/generate', json={'text': '中文叠加字幕测试', 'duration': 8, 'background_id': uploaded['id']})
        self.assertEqual(generated.status_code, 200, generated.text)
        job, detail = self.wait_job(task_id, generated.json()['id'])
        self.assertEqual(job['status'], 'SUCCEEDED', job)
        self.assertEqual(len(detail['media']), 2)
        self.assertEqual(detail['media'][0]['metadata']['version'], 2)
        self.assertAlmostEqual(detail['media'][0]['metadata']['duration'], 8, delta=.15)
        stale = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/review', json={'media_id': uploaded['id'], 'caption': 'caption'})
        self.assertEqual(stale.status_code, 400)
        current = detail['media'][0]
        review = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/review', json={'media_id': current['id'], 'caption': 'caption'})
        self.assertEqual(review.json(), {'reviewers': 1, 'required': 1})
        unavailable = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/publish', json={'media_id': current['id'], 'caption': 'caption', 'confirmed': True})
        self.assertEqual(unavailable.status_code, 400)
        self.assertEqual(self.detail(task_id)['publications'], [])

    def test_corrupt_upload_fails_without_registering_media(self):
        task_id = self.create()
        response = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/upload', data=b'not a video', headers={'X-Filename': 'bad.mp4'})
        job, detail = self.wait_job(task_id, response.json()['id'])
        self.assertEqual(job['status'], 'FAILED')
        self.assertEqual(detail['media'], [])

    def test_renamed_manifest_is_not_treated_as_video(self):
        task_id = self.create()
        manifest = f"ffconcat version 1.0\nfile '{self.fixture.as_posix()}'\n".encode()
        response = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/upload', data=manifest, headers={'X-Filename': 'disguised.mp4'})
        job, detail = self.wait_job(task_id, response.json()['id'])
        self.assertEqual(job['status'], 'FAILED')
        self.assertEqual(detail['media'], [])

    def test_publish_preserves_review_gates_and_deduplication(self):
        task_id = self.create('HIGH')
        uploaded = self.upload(task_id)
        body = {'media_id': uploaded['id'], 'caption': 'approved caption', 'confirmed': True, 'visibility': 'private'}
        self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/review', json=body)
        self.assertEqual(self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/publish', json=body).status_code, 400)
        with self.lock:
            reviewer = self.database.create_account('reviewer', 'test-password-456')
            self.studio.review(reviewer, task_id, body)
            self.studio.provider.key = 'configured'
            self.studio.provider.secret = 'configured'
            self.studio.provider.redirect = 'https://example.com/callback'
            self.studio.connection.execute('INSERT OR REPLACE INTO studio_accounts VALUES (?,?,?)', (self.admin['id'], json.dumps({'open_id': 'test-account', 'access_token': 'test-token'}), time.time() + 3600))
            self.studio.connection.commit()
            self.studio.provider.publish = lambda *args: {'item_id': 'mock-platform-receipt'}
        try:
            changed = dict(body, caption='changed caption')
            self.assertEqual(self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/publish', json=changed).status_code, 400)
            accepted = self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/publish', json=body)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(accepted.json()['status'], 'SUBMITTING')
            self.assertEqual(self.client.post(self.base + '/api/v1/studio/tasks/' + task_id + '/publish', json=body).status_code, 400)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                record = self.detail(task_id)['publications'][0]
                if record['status'] != 'SUBMITTING':
                    break
                time.sleep(.1)
            self.assertEqual(record['status'], 'SUBMITTED')
            self.assertEqual(self.client.get(self.base + '/api/v1/tasks/' + task_id).json()['status'], 'DISCOVERED')
        finally:
            from services.business_api.douyin import Douyin
            with self.lock:
                self.studio.provider = Douyin()


if __name__ == '__main__':
    unittest.main()
