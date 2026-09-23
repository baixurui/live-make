import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from services.business_api.douyin import Douyin, ProviderError


class DouyinTests(unittest.TestCase):
    def test_official_video_paths_and_private_visibility(self):
        provider = Douyin()
        provider.call = Mock(side_effect=[{'video': {'video_id': 'encrypted-id'}}, {'item_id': 'accepted-id'}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'video.mp4'
            path.write_bytes(b'fixture')
            result = provider.publish({'open_id': 'user', 'access_token': 'secret'}, path, 'caption', True)
        self.assertEqual(result, {'item_id': 'accepted-id'})
        upload, create = provider.call.call_args_list
        self.assertEqual(upload.args[0], '/api/douyin/v1/video/upload_video/')
        self.assertEqual(create.args[0], '/api/douyin/v1/video/create_video/')
        self.assertEqual(create.kwargs['json']['private_status'], 1)
        self.assertNotIn('private', create.kwargs['json'])
        self.assertEqual(create.kwargs['headers'], {'access-token': 'secret'})

    def test_public_visibility_is_explicit(self):
        provider = Douyin()
        provider.call = Mock(side_effect=[{'video': {'video_id': 'encrypted-id'}}, {'item_id': 'accepted-id'}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'video.mp4'
            path.write_bytes(b'fixture')
            provider.publish({'open_id': 'user', 'access_token': 'secret'}, path, 'caption', False)
        self.assertEqual(provider.call.call_args.kwargs['json']['private_status'], 0)

    @patch('services.business_api.douyin.requests.post')
    def test_api_failure_is_not_success(self, post):
        post.return_value.json.return_value = {'data': {'error_code': 2100005}}
        with self.assertRaises(ProviderError):
            Douyin().call('/test')
        post.side_effect = requests.Timeout('secret must not appear')
        with self.assertRaises(ProviderError) as failure:
            Douyin().call('/test')
        self.assertNotIn('secret', str(failure.exception))

    @patch('services.business_api.douyin.requests.post')
    def test_oauth_uses_form_not_json(self, post):
        post.return_value.json.return_value = {'data': {'access_token': 'token', 'open_id': 'user'}}
        Douyin().exchange('one-time-code')
        self.assertEqual(post.call_args.kwargs['data']['grant_type'], 'authorization_code')
        self.assertNotIn('json', post.call_args.kwargs)

    def test_oauth_requires_registered_https_callback(self):
        provider = Douyin()
        provider.key, provider.secret = 'key', 'secret'
        for redirect in ['http://localhost/callback', 'https://example.com/callback?custom=1']:
            provider.redirect = redirect
            with self.assertRaises(ValueError):
                provider.authorize_url('state')


if __name__ == '__main__':
    unittest.main()
