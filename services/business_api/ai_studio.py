from __future__ import annotations

import json
import secrets
import threading
import time

from PIL import Image

from . import media
from .bailian import Bailian, BailianError


SCHEMA = '''
CREATE TABLE IF NOT EXISTS studio_ai_jobs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),actor_id TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,remote_id TEXT,payload TEXT NOT NULL,result TEXT,error TEXT,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS studio_ai_images(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),job_id TEXT NOT NULL,prompt TEXT NOT NULL,created_at REAL NOT NULL);
'''


class AiStudio:
    def __init__(self, studio):
        self.studio = studio
        self.connection = studio.connection
        self.provider = Bailian()
        self.stopping = threading.Event()
        self.connection.executescript(SCHEMA)
        self.connection.execute("UPDATE studio_ai_jobs SET status=CASE WHEN remote_id IS NOT NULL THEN 'WAITING' WHEN status='SUBMITTING' THEN 'UNKNOWN' ELSE 'FAILED' END,error='服务重启：已保存的远端任务可恢复查询；提交状态未知时请先核对控制台。' WHERE status IN ('QUEUED','SUBMITTING','RUNNING')")
        self.connection.commit()

    def detail(self, task_id):
        jobs = [dict(row) for row in self.connection.execute('SELECT id,stage,status,remote_id,result,error,created_at FROM studio_ai_jobs WHERE task_id=? ORDER BY created_at DESC LIMIT 30', (task_id,))]
        for job in jobs:
            job['result'] = json.loads(job['result']) if job['result'] else None
        images = [dict(row) for row in self.connection.execute('SELECT * FROM studio_ai_images WHERE task_id=? ORDER BY created_at DESC', (task_id,))]
        for image in images:
            image['url'] = '/api/v1/studio/images/' + image['id']
        return {'jobs': jobs, 'images': images}

    def file(self, image_id):
        row = self.connection.execute('SELECT id FROM studio_ai_images WHERE id=?', (image_id,)).fetchone()
        if not row:
            raise KeyError('image not found')
        path = self.studio.root / (row['id'] + '.png')
        if not path.is_file():
            raise KeyError('image file missing')
        return path

    @staticmethod
    def text_field(body, name, limit):
        value = body.get(name)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            raise ValueError(f'{name} 必须为 1–{limit} 字。')
        return value.strip()

    def start(self, actor, task_id, stage, body):
        if body.get('confirmed') is not True:
            raise ValueError('请确认本次付费模型调用。')
        if not self.provider.key:
            raise ValueError('请先在 .env 填写 DASHSCOPE_API_KEY 并重启。')
        self.studio.ensure_mutable(task_id)
        if stage == 'text':
            payload = {'topic': self.text_field(body, 'topic', 1000)}
        elif stage == 'image':
            payload = {'prompt': self.text_field(body, 'prompt', 800)}
        elif stage == 'video':
            payload = {'prompt': self.text_field(body, 'prompt', 1000), 'image_id': body.get('image_id'), 'duration': body.get('duration', 8)}
            if type(payload['duration']) is not int or not 7 <= payload['duration'] <= 15:
                raise ValueError('视频时长须为 7–15 秒整数。')
            latest = self.connection.execute('SELECT id FROM studio_ai_images WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (task_id,)).fetchone()
            if not latest or latest['id'] != payload['image_id']:
                raise ValueError('请先生成并确认本任务最新首帧图片。')
            self.file(payload['image_id'])
        else:
            raise ValueError('未知生成阶段。')
        job_id = secrets.token_hex(16)
        payload['model'] = getattr(self.provider, stage + '_model')
        self.connection.execute('INSERT INTO studio_ai_jobs VALUES (?,?,?,?,?,?,?,?,?,?)', (job_id, task_id, actor['id'], stage, 'QUEUED', None, json.dumps(payload, ensure_ascii=False), None, None, time.time()))
        self.connection.commit()
        self.studio.executor.submit(self.run, job_id)
        return {'id': job_id, 'status': 'QUEUED'}

    def resume(self, actor, task_id, body):
        self.studio.task(task_id)
        active = self.connection.execute("SELECT COUNT(*) FROM studio_ai_jobs WHERE status IN ('QUEUED','SUBMITTING','RUNNING')").fetchone()[0]
        active += self.connection.execute("SELECT COUNT(*) FROM studio_jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0]
        if active >= 4:
            raise ValueError('处理队列已满，请稍后恢复查询。')
        job = self.connection.execute("SELECT * FROM studio_ai_jobs WHERE id=? AND task_id=? AND status='WAITING' AND remote_id IS NOT NULL", (body.get('job_id'), task_id)).fetchone()
        if not job or not self.provider.key:
            raise ValueError('没有可恢复的远端任务，或尚未配置 Key。')
        self.update(job['id'], status='QUEUED', error=None)
        self.studio.executor.submit(self.run, job['id'])
        return {'id': job['id'], 'status': 'QUEUED'}

    def acknowledge(self, actor, task_id, body):
        self.studio.api._require_admin(actor)
        if body.get('confirmed') is not True:
            raise ValueError('请先在百炼控制台核对未知任务及费用，再明确确认解锁。')
        job = self.connection.execute("SELECT id FROM studio_ai_jobs WHERE id=? AND task_id=? AND status='UNKNOWN'", (body.get('job_id'), task_id)).fetchone()
        if not job:
            raise ValueError('未知任务不存在。')
        self.update(job['id'], status='ACKNOWLEDGED', error='管理员已确认核对控制台；此操作不取消远端任务、不退款。')
        self.studio.database.audit(actor['id'], 'ai.unknown_acknowledged', 'task', task_id, {'job_id': job['id']})
        return {'status': 'ACKNOWLEDGED'}

    def update(self, job_id, **values):
        with self.studio.lock:
            fields = ','.join(name + '=?' for name in values)
            self.connection.execute('UPDATE studio_ai_jobs SET ' + fields + ' WHERE id=?', (*values.values(), job_id))
            self.connection.commit()

    def run(self, job_id):
        with self.studio.lock:
            job = dict(self.connection.execute('SELECT * FROM studio_ai_jobs WHERE id=?', (job_id,)).fetchone())
        payload = json.loads(job['payload'])
        remote_id = job['remote_id']
        try:
            if self.stopping.is_set():
                self.update(job_id, status='WAITING' if remote_id else 'FAILED', error='服务正在关闭，未发起新的模型调用。')
                return
            if not remote_id:
                with self.studio.lock:
                    self.studio.task(job['task_id'])
                    active = self.connection.execute('SELECT enabled FROM accounts WHERE id=?', (job['actor_id'],)).fetchone()
                    if not active or not active['enabled']:
                        raise ValueError('发起账号已停用，未创建模型任务。')
                self.update(job_id, status='SUBMITTING')
                if job['stage'] == 'text':
                    result = self.provider.text(payload['topic'])
                    self.update(job_id, status='SUCCEEDED', result=json.dumps(result, ensure_ascii=False), error=None)
                    return
                if job['stage'] == 'image':
                    remote_id = self.provider.image(payload['prompt'])
                else:
                    remote_id = self.provider.video(payload['prompt'], self.file(payload['image_id']), payload['duration'])
                self.update(job_id, remote_id=remote_id)
            self.update(job_id, status='RUNNING')
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline and not self.stopping.is_set():
                output = self.provider.query(remote_id)
                status = output['task_status']
                if status == 'SUCCEEDED':
                    result = self.save_result(job, payload, output)
                    self.update(job_id, status='SUCCEEDED', result=json.dumps(result, ensure_ascii=False), error=None)
                    return
                if status in {'FAILED', 'CANCELED', 'CANCELLED', 'UNKNOWN'}:
                    self.update(job_id, status='FAILED', error='百炼任务失败、取消或已过期。请通过远端任务 ID 在控制台核对原因。')
                    return
                if status not in {'PENDING', 'RUNNING'}:
                    raise BailianError('未识别的远端状态，请恢复查询。')
                self.stopping.wait(5)
            self.update(job_id, status='WAITING', error='查询已暂停。点击恢复查询继续获取原任务，不会重复收费提交。')
        except Exception as error:
            status = 'WAITING' if remote_id else 'UNKNOWN' if isinstance(error, BailianError) and error.uncertain else 'FAILED'
            message = str(error) if isinstance(error, (BailianError, ValueError)) else '生成或保存失败，请检查服务环境。已知远端任务可恢复查询。'
            if self.provider.key:
                message = message.replace(self.provider.key, '[redacted]')
            self.update(job_id, status=status, error=message)

    def save_result(self, job, payload, output):
        result_id = job['id']
        root = self.studio.root
        if job['stage'] == 'image':
            results = output.get('results', [])
            if not results or not results[0].get('url'):
                raise BailianError('百炼未返回图片 URL，请恢复查询。')
            source = root / (result_id + '.download')
            destination = root / (result_id + '.png')
            try:
                self.provider.download(results[0]['url'], source, 20 * 1024 * 1024)
                with Image.open(source) as image:
                    if not 240 <= image.width <= 4096 or not 240 <= image.height <= 4096:
                        raise ValueError('生成图片尺寸无效。')
                    image.convert('RGB').save(destination, format='PNG')
                with self.studio.lock:
                    self.studio.task(job['task_id'])
                    self.connection.execute('INSERT OR IGNORE INTO studio_ai_images VALUES (?,?,?,?,?)', (result_id, job['task_id'], job['id'], payload['prompt'], time.time()))
                    self.connection.commit()
                return {'image_id': result_id, 'model': payload['model']}
            finally:
                source.unlink(missing_ok=True)
        if not output.get('video_url'):
            raise BailianError('百炼未返回视频 URL，请恢复查询。')
        source = root / (result_id + '.download.mp4')
        destination = root / (result_id + '.mp4')
        with self.studio.lock:
            if self.connection.execute('SELECT 1 FROM studio_media WHERE id=?', (result_id,)).fetchone():
                return {'media_id': result_id, 'model': payload['model']}
        try:
            self.provider.download(output['video_url'], source, 100 * 1024 * 1024)
            metadata = media.normalize(source, destination)
            metadata.update({'size': destination.stat().st_size, 'model': payload['model'], 'ai_generated': True, 'source_image_id': payload['image_id']})
            with self.studio.lock:
                self.studio.task(job['task_id'])
                version = self.studio.database.add_media_version(job['actor_id'], job['task_id'], '/api/v1/studio/media/' + result_id, metadata)
                metadata['version'] = version['version']
                self.connection.execute('INSERT INTO studio_media VALUES (?,?,?,?,?)', (result_id, job['task_id'], '百炼图生视频.mp4', json.dumps(metadata), time.time()))
                self.connection.commit()
            return {'media_id': result_id, 'model': payload['model']}
        finally:
            source.unlink(missing_ok=True)

    def dispatch(self, actor, task_id, action, body):
        if action == 'resume':
            return self.resume(actor, task_id, body)
        if action == 'acknowledge':
            return self.acknowledge(actor, task_id, body)
        return self.start(actor, task_id, action, body)
