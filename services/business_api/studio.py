from __future__ import annotations

import hashlib
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import media
from .douyin import Douyin
from .ai_studio import AiStudio


SCHEMA = '''
CREATE TABLE IF NOT EXISTS studio_media(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), name TEXT NOT NULL, metadata TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS studio_jobs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL, status TEXT NOT NULL, error TEXT, media_id TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS studio_reviews(task_id TEXT NOT NULL, media_id TEXT NOT NULL, caption_hash TEXT NOT NULL, actor_id TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(task_id,media_id,caption_hash,actor_id));
CREATE TABLE IF NOT EXISTS studio_accounts(actor_id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS studio_publications(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, media_id TEXT NOT NULL, open_id TEXT NOT NULL, publish_day TEXT NOT NULL, status TEXT NOT NULL, caption TEXT NOT NULL, visibility TEXT NOT NULL, result TEXT, created_at REAL NOT NULL, UNIQUE(open_id,publish_day));
'''


class Studio:
    max_upload = 100 * 1024 * 1024

    def __init__(self, api, root: Path, lock):
        self.api = api
        self.database = api.database
        self.connection = self.database.connection
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = lock
        self.provider = Douyin()
        self.states = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='studio')
        self.connection.executescript(SCHEMA)
        self.connection.execute("UPDATE studio_jobs SET status='FAILED',error='服务重启，任务中断，请重新提交。' WHERE status IN ('QUEUED','RUNNING')")
        self.connection.execute("UPDATE studio_publications SET status='UNKNOWN',result='服务重启，请到抖音核对结果，勿重复提交。' WHERE status='SUBMITTING'")
        self.connection.commit()

        self.ai = AiStudio(self)

    def close(self):
        self.ai.stopping.set()
        self.executor.shutdown(wait=True)

    def capabilities(self, actor):
        row = self.connection.execute('SELECT expires_at FROM studio_accounts WHERE actor_id=?', (actor['id'],)).fetchone()
        connected = bool(row and row['expires_at'] > time.time())
        return {'generation': {'mode': 'template', 'available': True}, 'bailian': self.ai.provider.capabilities(),
                'upload_limit_mb': 100, 'douyin': {'configured': self.provider.configured,
                'connected': connected, 'expires_at': row['expires_at'] if row else None}}

    def task(self, task_id):
        task = self.database.get_task(task_id)
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_tasks'").fetchone():
            if self.connection.execute('SELECT 1 FROM workflow_tasks WHERE task_id=?', (task_id,)).fetchone():
                raise ValueError('此任务由自动工作流管理，请使用原有工作流审核和排期。')
        if task['risk_level'] == 'BLOCKED' or task['status'] in {'CANCELLED', 'PUBLISHED'}:
            raise ValueError('此任务已阻止、取消或完成，不能制作或发布。')
        return task

    def detail(self, task_id):
        self.database.get_task(task_id)
        records = [dict(row) for row in self.connection.execute('SELECT * FROM studio_media WHERE task_id=? ORDER BY created_at DESC', (task_id,))]
        for record in records:
            record['metadata'] = json.loads(record['metadata'])
            record['url'] = '/api/v1/studio/media/' + record['id']
        jobs = [dict(row) for row in self.connection.execute('SELECT * FROM studio_jobs WHERE task_id=? ORDER BY created_at DESC LIMIT 20', (task_id,))]
        publications = [dict(row) for row in self.connection.execute('SELECT id,media_id,status,caption,visibility,result,created_at FROM studio_publications WHERE task_id=? ORDER BY created_at DESC', (task_id,))]
        return {'media': records, 'jobs': jobs, 'publications': publications, 'ai': self.ai.detail(task_id)}

    def file(self, media_id):
        record = self.connection.execute('SELECT * FROM studio_media WHERE id=?', (media_id,)).fetchone()
        if not record:
            raise KeyError('video not found')
        path = self.root / (record['id'] + '.mp4')
        if not path.is_file():
            raise KeyError('video file missing')
        return path

    def ensure_mutable(self, task_id):
        self.task(task_id)
        if self.connection.execute("SELECT 1 FROM studio_ai_jobs WHERE task_id=? AND status IN ('QUEUED','SUBMITTING','RUNNING','WAITING','UNKNOWN')", (task_id,)).fetchone():
            raise ValueError('此任务有未完成的百炼任务，请等待、恢复查询或先核对未知提交。')
        ai_count = self.connection.execute("SELECT COUNT(*) FROM studio_ai_jobs WHERE status IN ('QUEUED','SUBMITTING','RUNNING')").fetchone()[0]
        if self.connection.execute("SELECT 1 FROM studio_jobs WHERE task_id=? AND status IN ('QUEUED','RUNNING')", (task_id,)).fetchone():
            raise ValueError('此任务已有制作任务进行中，请等待完成。')
        if self.connection.execute("SELECT 1 FROM studio_publications WHERE task_id=? AND status IN ('SUBMITTING','SUBMITTED','UNKNOWN')", (task_id,)).fetchone():
            raise ValueError('此任务已有发布记录，不能修改素材或重复发布。')
        if ai_count + self.connection.execute("SELECT COUNT(*) FROM studio_jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0] >= 4:
            raise ValueError('制作队列已满，请稍后重试。')

    def upload(self, actor, task_id, filename, source, length):
        self.ensure_mutable(task_id)
        suffix = Path(filename).suffix.lower()
        if suffix not in {'.mp4', '.mov', '.webm'} or not 0 < length <= self.max_upload:
            raise ValueError('仅支持 1 字节至 100 MiB 的 MP4/MOV/WebM 视频。')
        job_id = secrets.token_hex(16)
        staging = self.root / (job_id + '.source' + suffix)
        try:
            with staging.open('xb') as output:
                remaining = length
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('上传不完整，请重新上传。')
                    output.write(chunk)
                    remaining -= len(chunk)
            self._enqueue(job_id, task_id, 'UPLOAD', actor, Path(filename).name[:120], staging=staging)
        except Exception:
            staging.unlink(missing_ok=True)
            raise
        return {'id': job_id, 'status': 'QUEUED'}

    def generate(self, actor, task_id, body):
        self.ensure_mutable(task_id)
        text = body.get('text', '')
        duration = body.get('duration', 8)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 100:
            raise ValueError('请输入 1–100 字文案。')
        if type(duration) is not int or not 7 <= duration <= 15:
            raise ValueError('时长必须是 7–15 秒整数。')
        background = None
        if body.get('background_id'):
            records = self.detail(task_id)['media']
            if not any(record['id'] == body['background_id'] for record in records):
                raise ValueError('背景视频不属于当前任务。')
            background = self.file(body['background_id'])
        job_id = secrets.token_hex(16)
        self._enqueue(job_id, task_id, 'GENERATE', actor, '模板视频.mp4', text=text.strip(), duration=duration, background=background)
        return {'id': job_id, 'status': 'QUEUED'}

    def _enqueue(self, job_id, task_id, kind, actor, name, **options):
        self.connection.execute('INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?)', (job_id, task_id, kind, 'QUEUED', None, None, time.time()))
        self.connection.commit()
        self.executor.submit(self._produce, job_id, task_id, actor['id'], name, options)

    def _produce(self, job_id, task_id, actor_id, name, options):
        media_id = secrets.token_hex(16)
        destination = self.root / (media_id + '.mp4')
        staging = options.get('staging')
        try:
            with self.lock:
                self.connection.execute("UPDATE studio_jobs SET status='RUNNING' WHERE id=?", (job_id,))
                self.connection.commit()
            metadata = media.normalize(staging, destination) if staging else media.render(destination, **options)
            metadata['size'] = destination.stat().st_size
            with self.lock:
                self.task(task_id)
                version = self.database.add_media_version(actor_id, task_id, '/api/v1/studio/media/' + media_id, metadata)
                metadata['version'] = version['version']
                self.connection.execute('INSERT INTO studio_media VALUES (?,?,?,?,?)', (media_id, task_id, name, json.dumps(metadata), time.time()))
                self.connection.execute("UPDATE studio_jobs SET status='SUCCEEDED',media_id=? WHERE id=?", (media_id, job_id))
                self.connection.commit()
        except Exception as error:
            destination.unlink(missing_ok=True)
            with self.lock:
                message = str(error) if isinstance(error, ValueError) else '处理失败或超时，请检查文件和运行环境后重试。'
                self.connection.execute("UPDATE studio_jobs SET status='FAILED',error=? WHERE id=?", (message, job_id))
                self.connection.commit()
        finally:
            if staging:
                staging.unlink(missing_ok=True)

    def _review_target(self, task_id, body):
        task = self.task(task_id)
        records = self.detail(task_id)['media']
        if not records or records[0]['id'] != body.get('media_id'):
            raise ValueError('请审核当前最新视频，不能使用旧版本。')
        if records[0]['metadata']['version'] != task['current_media_version']:
            raise ValueError('视频版本已变化，请重新制作或上传。')
        caption = body.get('caption', '')
        if not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 100:
            raise ValueError('发布文案须为 1–100 字。')
        return task, records[0], caption.strip(), hashlib.sha256(caption.strip().encode()).hexdigest()

    def review(self, actor, task_id, body):
        self.ensure_mutable(task_id)
        task, record, caption, digest = self._review_target(task_id, body)
        self.connection.execute('INSERT OR REPLACE INTO studio_reviews VALUES (?,?,?,?,?)', (task_id, record['id'], digest, actor['id'], time.time()))
        self.connection.commit()
        self.database.audit(actor['id'], 'studio.reviewed', 'task', task_id, {'media_id': record['id'], 'caption': caption})
        return self._review_count(task, record['id'], digest)

    def _review_count(self, task, media_id, digest):
        count = self.connection.execute('SELECT COUNT(*) FROM studio_reviews r JOIN accounts a ON a.id=r.actor_id WHERE r.task_id=? AND r.media_id=? AND r.caption_hash=? AND a.enabled=1', (task['id'], media_id, digest)).fetchone()[0]
        return {'reviewers': count, 'required': 2 if task['risk_level'] == 'HIGH' else 1}

    def authorize(self, actor):
        self.api._require_admin(actor)
        self.states = {key: value for key, value in self.states.items() if value[1] > time.time()}
        state = secrets.token_urlsafe(32)
        self.states[state] = (actor['id'], time.time() + 600)
        return {'url': self.provider.authorize_url(state)}

    def callback(self, actor, query):
        self.api._require_admin(actor)
        state = self.states.pop(query.get('state'), None)
        if not state or state[0] != actor['id'] or state[1] < time.time():
            raise ValueError('授权状态无效或过期，请重新连接。')
        if not query.get('code'):
            raise ValueError('未收到授权码，授权已取消。')
        account = self.provider.exchange(query['code'])
        if not account.get('open_id') or not account.get('access_token'):
            raise ValueError('授权响应缺少账号或访问令牌。')
        self.connection.execute('INSERT OR REPLACE INTO studio_accounts VALUES (?,?,?)', (actor['id'], json.dumps(account), time.time() + int(account.get('expires_in', 0))))
        self.connection.commit()

    def publish(self, actor, task_id, body):
        self.api._require_admin(actor)
        if body.get('confirmed') is not True:
            raise ValueError('必须明确确认后才能提交抖音。')
        self.ensure_mutable(task_id)
        task, record, caption, digest = self._review_target(task_id, body)
        votes = self._review_count(task, record['id'], digest)
        if votes['reviewers'] < votes['required']:
            raise ValueError('当前视频和文案尚未完成所需人工审核。')
        if not 7 <= record['metadata']['duration'] <= 15.1:
            raise ValueError('发布视频必须为 7–15 秒，请使用模板生成进行裁剪。')
        visibility = body.get('visibility', 'private')
        if visibility not in {'private', 'public'}:
            raise ValueError('可见性无效。')
        if not self.provider.configured:
            raise ValueError('尚未配置抖音应用，不能真实发布。')
        row = self.connection.execute('SELECT * FROM studio_accounts WHERE actor_id=?', (actor['id'],)).fetchone()
        if not row or row['expires_at'] <= time.time():
            raise ValueError('请先连接抖音账号，授权过期后需要重新连接。')
        account = json.loads(row['payload'])
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        if self.connection.execute('SELECT 1 FROM studio_publications WHERE open_id=? AND publish_day=?', (account['open_id'], day)).fetchone():
            raise ValueError('此账号今天已有提交记录（含失败或未知），请核对平台结果，勿重复发布。')
        publication_id = secrets.token_hex(16)
        self.connection.execute('INSERT INTO studio_publications VALUES (?,?,?,?,?,?,?,?,?,?)', (publication_id, task_id, record['id'], account['open_id'], day, 'SUBMITTING', caption, visibility, None, time.time()))
        self.connection.commit()
        self.executor.submit(self._publish, publication_id, account, self.file(record['id']), caption, visibility)
        return {'id': publication_id, 'status': 'SUBMITTING'}

    def _publish(self, publication_id, account, path, caption, visibility):
        try:
            result = json.dumps(self.provider.publish(account, path, caption, visibility == 'private'))
            status = 'SUBMITTED'
        except Exception:
            result = '平台结果未确认。请检查应用权限、账号授权和抖音作品列表；不会自动重试。'
            status = 'UNKNOWN'
        with self.lock:
            self.connection.execute('UPDATE studio_publications SET status=?,result=? WHERE id=?', (status, result, publication_id))
            self.connection.commit()

    def dispatch(self, method, route, actor, body, query):
        if method == 'GET' and route == '/api/v1/studio/capabilities':
            return self.capabilities(actor)
        if method == 'POST' and route == '/api/v1/studio/douyin/authorize':
            return self.authorize(actor)
        if method == 'GET' and route == '/api/v1/studio/douyin/callback':
            self.callback(actor, query)
            return {'connected': True}
        parts = route.strip('/').split('/')
        if len(parts) == 7 and parts[:4] == ['api', 'v1', 'studio', 'tasks'] and parts[5] == 'ai' and method == 'POST':
            return self.ai.dispatch(actor, parts[4], parts[6], body)
        if len(parts) == 5 and parts[:4] == ['api', 'v1', 'studio', 'tasks'] and method == 'GET':
            return self.detail(parts[4])
        if len(parts) == 6 and parts[:4] == ['api', 'v1', 'studio', 'tasks'] and method == 'POST':
            action = {'generate': self.generate, 'review': self.review, 'publish': self.publish}.get(parts[5])
            if action:
                return action(actor, parts[4], body)
        raise KeyError('route not found')
