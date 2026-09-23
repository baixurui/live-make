import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from services.business_api.bailian import Bailian, BailianError
from services.business_api.config import load_env


class BailianTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'test-secret'}, clear=True)
        self.environment.start()
        self.provider = Bailian()

    def tearDown(self):
        self.environment.stop()

    @patch('services.business_api.bailian.requests.request')
    def test_text_uses_qwen_compatible_json(self, request):
        result = {'script': '文案', 'image_prompt': '竖屏画面', 'video_prompt': '镜头推进', 'caption': '标题'}
        request.return_value.status_code = 200
        request.return_value.json.return_value = {'choices': [{'message': {'content': json.dumps(result)}}]}
        self.assertEqual(self.provider.text('主题'), result)
        body = request.call_args.kwargs['json']
        self.assertEqual(body['model'], 'qwen3.6-plus')
        self.assertFalse(body['enable_thinking'])
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertTrue(request.call_args.args[1].endswith('/compatible-mode/v1/chat/completions'))

    @patch('services.business_api.bailian.requests.request')
    def test_image_has_one_portrait_output(self, request):
        request.return_value.status_code = 200
        request.return_value.json.return_value = {'output': {'task_id': 'remote-image'}}
        self.assertEqual(self.provider.image('画面'), 'remote-image')
        body = request.call_args.kwargs['json']
        self.assertEqual(body['model'], 'wanx-v1')
        self.assertEqual(body['parameters']['size'], '720*1280')
        self.assertEqual(body['parameters']['n'], 1)
        self.assertEqual(request.call_args.kwargs['headers']['X-DashScope-Async'], 'enable')

    @patch('services.business_api.bailian.requests.request')
    def test_video_uses_new_media_protocol(self, request):
        request.return_value.status_code = 200
        request.return_value.json.return_value = {'output': {'task_id': 'remote-video'}}
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'frame.png'
            image.write_bytes(b'fixture')
            self.provider.video('运动', image, 8)
        body = request.call_args.kwargs['json']
        self.assertEqual(body['model'], 'wan2.7-i2v')
        self.assertEqual(body['input']['media'][0]['type'], 'first_frame')
        self.assertTrue(body['input']['media'][0]['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('img_url', body['input'])
        self.assertEqual(body['parameters']['duration'], 8)
        self.assertEqual(body['parameters']['resolution'], '720P')

    @patch('services.business_api.bailian.requests.request')
    def test_timeout_is_unknown_and_not_retried(self, request):
        request.side_effect = requests.Timeout('test-secret')
        with self.assertRaises(BailianError) as error:
            self.provider.image('prompt')
        self.assertTrue(error.exception.uncertain)
        self.assertNotIn('test-secret', str(error.exception))
        self.assertEqual(request.call_count, 1)

    @patch('services.business_api.bailian.requests.request')
    def test_missing_key_does_not_call_network(self, request):
        self.provider.key = ''
        with self.assertRaises(BailianError):
            self.provider.text('prompt')
        request.assert_not_called()

    def test_download_rejects_arbitrary_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            for url in ['http://example.com/a', 'https://127.0.0.1/a', 'https://evil.example/a', 'https://aliyuncs.com.evil.example/a']:
                with self.assertRaises(BailianError):
                    self.provider.download(url, Path(directory) / 'out', 100)

    def test_malformed_submission_response_remains_uncertain(self):
        for payload in [{}, {'output': None}, {'output': []}]:
            with self.assertRaises(BailianError) as failure:
                self.provider.remote_id(payload)
            self.assertTrue(failure.exception.uncertain)

    def test_env_loading_preserves_process_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('DASHSCOPE_API_KEY=other\nBAILIAN_TEXT_MODEL="qwen3.6-plus"\n', encoding='utf-8')
            load_env(path)
            self.assertEqual(os.environ['DASHSCOPE_API_KEY'], 'test-secret')
            self.assertEqual(os.environ['BAILIAN_TEXT_MODEL'], 'qwen3.6-plus')
