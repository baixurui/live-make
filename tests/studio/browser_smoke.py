import os
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from services.business_api.media import render


def main():
    base = os.environ.get('STUDIO_TEST_URL', 'http://127.0.0.1:8080')
    password = os.environ['STUDIO_TEST_PASSWORD']
    output = Path('.runtime/browser-check')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory) / 'browser-fixture.mp4'
        render(fixture, '浏览器上传验证', 7)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            page.locator('#login-view').wait_for(state='visible')
            page.screenshot(path=str(output / 'login.png'), full_page=True)
            page.locator('input[name=username]').fill('admin')
            page.locator('input[name=password]').fill(password)
            page.locator('#login-form button').click()
            page.locator('#app-view').wait_for(state='visible')
            page.locator('#new-task').click()
            page.locator('#task-title').fill('网页验收 · 视频制作演示')
            page.locator('#new-task-form button[type=submit]').click()
            page.locator('#detail-title').filter(has_text='网页验收').wait_for()
            page.locator('#upload').set_input_files(str(fixture))
            page.wait_for_function("() => document.querySelector('#video-version').textContent.includes('v1')", timeout=90000)
            page.wait_for_function("() => document.querySelector('#preview').readyState >= 2", timeout=15000)
            assert page.locator('#preview').evaluate('(video) => video.videoWidth') == 720
            page.locator('#generate-text').fill('每一个创意，都值得成为作品。')
            page.locator('#background').select_option(index=1)
            page.locator('#generate-button').click()
            page.wait_for_function("() => document.querySelector('#video-version').textContent.includes('v2')", timeout=90000)
            page.wait_for_function("() => document.querySelector('#preview').readyState >= 2", timeout=15000)
            page.locator('#caption').fill('Live Make 本地制作验证')
            page.locator('#review').click()
            page.wait_for_function("() => document.querySelector('#review-state').textContent.includes('1 / 1')")
            assert page.locator('#publish').is_disabled()
            page.screenshot(path=str(output / 'workspace.png'), full_page=True)
            with page.expect_download() as information:
                page.locator('#download').click()
            download = information.value
            download.save_as(str(output / 'generated.mp4'))
            assert (output / 'generated.mp4').stat().st_size > 1000
            page.reload()
            page.locator('#app-view').wait_for(state='visible')
            page.locator('#task-list .open-task').first.wait_for()
            page.screenshot(path=str(output / 'tasks.png'), full_page=True)
            page.locator('#search').fill('网页验收')
            page.locator('#task-list .open-task').first.click()
            page.wait_for_function("() => document.querySelector('#video-version').textContent.includes('v2')")
            page.locator('#nav-settings').click()
            page.locator('#settings-view').wait_for(state='visible')
            assert page.locator('#connect-douyin').is_disabled()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#nav-tasks').click()
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('#logout').click()
            page.locator('#login-view').wait_for(state='visible')
            assert errors == [], errors
            browser.close()
            print('PASS: login, task creation, upload, video decode, template rendering, review, download, persistence, settings, mobile layout and logout')


if __name__ == '__main__':
    main()
