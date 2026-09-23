from __future__ import annotations

import os
from urllib.parse import urlencode, urlparse

import requests


class ProviderError(Exception):
    pass


class Douyin:
    base = 'https://open.douyin.com'

    def __init__(self):
        self.key = os.environ.get('DOUYIN_CLIENT_KEY', '')
        self.secret = os.environ.get('DOUYIN_CLIENT_SECRET', '')
        self.redirect = os.environ.get('DOUYIN_REDIRECT_URI', '')

    @property
    def configured(self):
        return bool(self.key and self.secret and self.redirect)

    def authorize_url(self, state):
        if not self.configured:
            raise ValueError('尚未配置抖音应用。请配置 CLIENT_KEY、CLIENT_SECRET 和 REDIRECT_URI。')
        redirect = urlparse(self.redirect)
        if redirect.scheme != 'https' or not redirect.netloc or redirect.query or redirect.fragment:
            raise ValueError('抖音回调地址须为登记过的 HTTPS 地址，不能携带自定义查询参数。')
        return self.base + '/platform/oauth/connect/?' + urlencode({
            'client_key': self.key, 'response_type': 'code', 'scope': 'video.create.bind',
            'redirect_uri': self.redirect, 'state': state,
        })

    def call(self, path, **kwargs):
        try:
            response = requests.post(self.base + path, timeout=(10, 120), **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ProviderError('抖音请求失败或响应无法确认，请到抖音核对；不要重复提交。') from error
        if not isinstance(payload, dict):
            raise ProviderError('抖音返回了无效的响应结构。')
        data = payload.get('data', payload)
        if not isinstance(data, dict) or data.get('error_code', 0) != 0 or payload.get('error_code', 0) != 0:
            code = data.get('error_code', 'unknown') if isinstance(data, dict) else 'invalid'
            raise ProviderError(f'抖音拒绝请求，错误码 {code}。请检查应用权限和账号授权。')
        return data

    def exchange(self, code):
        return self.call('/oauth/access_token/', data={'client_key': self.key, 'client_secret': self.secret,
                         'code': code, 'grant_type': 'authorization_code'})

    def publish(self, account, path, caption, private):
        parameters = {'open_id': account['open_id']}
        headers = {'access-token': account['access_token']}
        with path.open('rb') as source:
            uploaded = self.call('/api/douyin/v1/video/upload_video/', params=parameters, headers=headers,
                                 files={'video': ('video.mp4', source, 'video/mp4')})
        video_id = uploaded.get('video', {}).get('video_id')
        if not video_id:
            raise ProviderError('抖音未返回 video_id，未执行创建视频。')
        created = self.call('/api/douyin/v1/video/create_video/', params=parameters, headers=headers,
                            json={'video_id': video_id, 'text': caption, 'private_status': 1 if private else 0})
        item_id = created.get('item_id')
        if not item_id:
            raise ProviderError('创建结果缺少 item_id，请到抖音核对后再操作。')
        return {'item_id': item_id}
