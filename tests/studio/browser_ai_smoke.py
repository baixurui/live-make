import os
import shutil
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

from services.business_api import media
from services.business_api.app import BusinessApi
from services.business_api.db import Database
from services.business_api.server import RequestHandler
from services.business_api.studio import Studio


def main():
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'offline-only-secret'}):
        root = Path(directory)
        database = Database(root / 'test.sqlite3')
        database.create_account('admin', 'browser-test-password', True)
        api = BusinessApi(database)
        lock = threading.RLock()
        studio = Studio(api, root / 'files', lock)
        fixture = root / 'fixture.mp4'
        media.render(fixture, '离线协议验收 · 非模型生成', 8)
        provider = studio.ai.provider
        provider.text = Mock(return_value={'script': '雨后咖啡，温暖时光。', 'caption': '温暖的一杯', 'image_prompt': '雨后咖啡店，竖屏电影感', 'video_prompt': '镜头缓慢推进，咖啡热气上升'})
        provider.image = Mock(return_value='offline-image')
        provider.video = Mock(return_value='offline-video')
        provider.query = Mock(side_effect=lambda remote: {'task_status': 'SUCCEEDED', **({'results': [{'url': 'https://test.aliyuncs.com/frame.png'}]} if remote == 'offline-image' else {'video_url': 'https://test.aliyuncs.com/clip.mp4'})})

        def download(url, destination, limit):
            if url.endswith('.png'):
                Image.new('RGB', (720, 1280), '#9378dd').save(destination, format='PNG')
            else:
                shutil.copyfile(fixture, destination)

        provider.download = download

        class Handler(RequestHandler):
            pass

        Handler.api, Handler.lock, Handler.studio = api, lock, studio
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept())
                page.goto(base)
                page.locator('input[name=username]').fill('admin')
                page.locator('input[name=password]').fill('browser-test-password')
                page.locator('#login-form button').click()
                page.locator('#new-task').click()
                page.locator('#task-title').fill('百炼离线接口与网页验收')
                page.locator('#new-task-form button[type=submit]').click()
                page.locator('#ai-topic').fill('雨后咖啡店')
                page.locator('#ai-text').click()
                page.wait_for_function("() => document.querySelector('#ai-script').value.includes('雨后咖啡')")
                page.locator('#ai-image-prompt').fill('编辑后的竖屏咖啡画面')
                page.locator('#ai-image').click()
                page.wait_for_function("() => document.querySelector('#ai-frame').naturalWidth === 720")
                image_url = page.locator('#ai-frame').get_attribute('src')
                assert requests.get(base + image_url, timeout=5).status_code == 403
                page.locator('#ai-video-prompt').fill('编辑后的运镜提示')
                page.locator('#ai-video').click()
                page.wait_for_function("() => document.querySelector('#video-version').textContent.includes('v1')", timeout=30000)
                page.wait_for_function("() => document.querySelector('#preview').readyState >= 2")
                provider.text.assert_called_once_with('雨后咖啡店')
                provider.image.assert_called_once_with('编辑后的竖屏咖啡画面')
                assert provider.video.call_count == 1
                assert provider.video.call_args.args[0] == '编辑后的运镜提示'
                assert provider.video.call_args.args[2] == 8
                assert not page.locator('#publish').is_enabled()
                assert 'offline-only-secret' not in page.content()
                assert 'offline-only-secret' not in str(page.evaluate('() => state.capabilities'))
                Path('.runtime/browser-check').mkdir(parents=True, exist_ok=True)
                page.screenshot(path='.runtime/browser-check/bailian-offline.png', full_page=True)
                page.reload()
                page.locator('#task-list .open-task').first.click()
                page.wait_for_function("() => document.querySelector('#ai-frame').naturalWidth === 720")
                page.wait_for_function("() => document.querySelector('#ai-script').value.includes('雨后咖啡')")
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
                browser.close()
                print('PASS: offline provider text/image/video, edited prompts, authenticated image preview, playable saved MP4, persistence, mobile layout, no key exposure; no paid requests')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            studio.close()
            database.close()


if __name__ == '__main__':
    main()
