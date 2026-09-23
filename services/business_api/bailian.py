from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


class BailianError(ValueError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class Bailian:
    def __init__(self):
        self.key = os.environ.get('DASHSCOPE_API_KEY', '').strip()
        self.base = os.environ.get('BAILIAN_BASE_URL', 'https://dashscope.aliyuncs.com').rstrip('/')
        parsed = urlparse(self.base)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.path or parsed.query or parsed.fragment or not (parsed.hostname == 'dashscope.aliyuncs.com' or (parsed.hostname or '').endswith('.cn-beijing.maas.aliyuncs.com')):
            raise ValueError('BAILIAN_BASE_URL 必须是北京地域官方 HTTPS 域名，不含路径。')
        self.text_model = os.environ.get('BAILIAN_TEXT_MODEL', 'qwen3.6-plus')
        self.image_model = os.environ.get('BAILIAN_IMAGE_MODEL', 'wanx-v1')
        self.video_model = os.environ.get('BAILIAN_VIDEO_MODEL', 'wan2.7-i2v')

    def capabilities(self):
        return {'configured': bool(self.key), 'region': 'cn-beijing', 'text_model': self.text_model,
                'image_model': self.image_model, 'video_model': self.video_model,
                'duration': 8, 'resolution': '720P', 'image_size': '720*1280'}

    def request(self, method, path, body=None, asynchronous=False):
        if not self.key:
            raise BailianError('请在项目 .env 填写 DASHSCOPE_API_KEY，然后重启服务。')
        headers = {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}
        if asynchronous:
            headers['X-DashScope-Async'] = 'enable'
        try:
            response = requests.request(method, self.base + path, headers=headers, json=body,
                                        timeout=(10, 120), allow_redirects=False)
        except requests.RequestException as error:
            raise BailianError('百炼网络超时或连接失败。提交结果可能未知，请勿重复创建付费任务。', uncertain=method == 'POST') from error
        if response.status_code != 200:
            try:
                code = str(response.json().get('code', ''))
            except (ValueError, AttributeError):
                code = ''
            code = code if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', code) else 'RequestRejected'
            raise BailianError(f'百炼请求失败（HTTP {response.status_code} / {code}）。请核对北京地域 Key、模型权限、余额和模型名称。', uncertain=method == 'POST' and response.status_code >= 500)
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError()
        except ValueError as error:
            raise BailianError('百炼响应格式无效，请到控制台核对任务。', uncertain=method == 'POST') from error
        return payload

    def text(self, topic):
        payload = self.request('POST', '/compatible-mode/v1/chat/completions', {
            'model': self.text_model, 'enable_thinking': False, 'max_tokens': 1800,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': '你是短视频策划。为一条8秒9:16单镜头短视频生成中文方案。只返回JSON对象，包含script（口播文案，最多100字）、image_prompt（首帧画面描述，最多800字）、video_prompt（基于首帧的动作和运镜，最多1000字）、caption（发布标题，最多100字）。不得承诺图片模型生成清晰文字，不编造事实。画面提示词写清主体、构图、风格和竖屏，视频不要切换到完全不同场景。'},
                {'role': 'user', 'content': topic},
            ],
        })
        try:
            result = json.loads(payload['choices'][0]['message']['content'])
            for name, limit in {'script': 100, 'image_prompt': 800, 'video_prompt': 1000, 'caption': 100}.items():
                if not isinstance(result[name], str) or not 1 <= len(result[name].strip()) <= limit:
                    raise ValueError()
            return {name: result[name].strip() for name in ('script', 'image_prompt', 'video_prompt', 'caption')}
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise BailianError('模型未返回符合长度要求的 JSON 文案，未继续生成图片；可修改主题后重新生成。') from error

    def image(self, prompt):
        payload = self.request('POST', '/api/v1/services/aigc/text2image/image-synthesis', {
            'model': self.image_model, 'input': {'prompt': prompt},
            'parameters': {'size': '720*1280', 'n': 1, 'style': '<auto>'},
        }, asynchronous=True)
        return self.remote_id(payload)

    def video(self, prompt, image: Path, duration):
        if image.stat().st_size > 20 * 1024 * 1024:
            raise BailianError('首帧图片超过 20 MiB。')
        encoded = 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode('ascii')
        payload = self.request('POST', '/api/v1/services/aigc/video-generation/video-synthesis', {
            'model': self.video_model,
            'input': {'prompt': prompt, 'media': [{'type': 'first_frame', 'url': encoded}]},
            'parameters': {'resolution': '720P', 'duration': duration, 'prompt_extend': True, 'watermark': True},
        }, asynchronous=True)
        return self.remote_id(payload)

    @staticmethod
    def remote_id(payload):
        output = payload.get('output')
        remote_id = output.get('task_id') if isinstance(output, dict) else None
        if not isinstance(remote_id, str) or not re.fullmatch(r'[\w-]{1,160}', remote_id):
            raise BailianError('响应未返回有效 task_id，请到百炼控制台核对，不能自动重试。', uncertain=True)
        return remote_id

    def query(self, remote_id):
        if not re.fullmatch(r'[\w-]{1,160}', remote_id):
            raise BailianError('任务 ID 无效。')
        payload = self.request('GET', '/api/v1/tasks/' + remote_id)
        output = payload.get('output')
        if not isinstance(output, dict) or 'task_status' not in output:
            raise BailianError('百炼未返回任务状态，请稍后恢复查询。')
        return output

    def download(self, url, destination: Path, limit):
        parsed = urlparse(url)
        hostname = parsed.hostname or ''
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in {None, 443} or not any(hostname.endswith(suffix) for suffix in ('.aliyuncs.com', '.aliyun.com', '.alicdn.com')):
            raise BailianError('结果下载地址不是允许的阿里云 HTTPS 域名。')
        started = time.monotonic()
        try:
            with requests.get(url, stream=True, timeout=(10, 30), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise BailianError('结果文件不可下载或已过期，请恢复查询；不会重复生成。')
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise BailianError('结果文件超出本地大小限制。')
                size = 0
                with destination.open('wb') as output:
                    for chunk in response.iter_content(128 * 1024):
                        size += len(chunk)
                        if size > limit or time.monotonic() - started > 180:
                            raise BailianError('下载超时或结果文件过大，请恢复查询。')
                        output.write(chunk)
                if not size:
                    raise BailianError('结果文件为空。')
        except (requests.RequestException, ValueError) as error:
            destination.unlink(missing_ok=True)
            if isinstance(error, BailianError):
                raise
            raise BailianError('结果下载失败，请恢复查询；不会重复提交生成。') from error
