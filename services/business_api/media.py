from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


def executable() -> str:
    return os.environ.get('FFMPEG_EXE') or imageio_ffmpeg.get_ffmpeg_exe()


def run(arguments: list[str]) -> str:
    result = subprocess.run(
        [executable(), '-hide_banner', '-nostdin', '-y', *arguments],
        capture_output=True, timeout=180,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    output = result.stderr.decode('utf-8', errors='replace')
    if result.returncode:
        raise ValueError('视频无法解码或渲染失败，请换用有效的 MP4/MOV/WebM 文件。')
    return output


def inspect(path: Path) -> dict:
    container = 'matroska' if path.suffix.lower() == '.webm' else 'mov'
    result = subprocess.run(
        [executable(), '-hide_banner', '-nostdin', '-protocol_whitelist', 'file,pipe', '-f', container, '-i', str(path)],
        capture_output=True, timeout=20,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    output = result.stderr.decode('utf-8', errors='replace')
    duration = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', output)
    video = re.search(r'Video: .*?\b(\d{2,5})x(\d{2,5})\b', output)
    if not duration or not video:
        raise ValueError('文件不包含有效的视频流。')
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    width, height = int(video[1]), int(video[2])
    if not 0 < seconds <= 300 or width * height > 3840 * 2160:
        raise ValueError('视频须在 5 分钟以内，分辨率不超过 4K。')
    return {'duration': seconds, 'width': width, 'height': height}


def normalize(source: Path, destination: Path) -> dict:
    inspect(source)
    container = 'matroska' if source.suffix.lower() == '.webm' else 'mov'
    run(['-protocol_whitelist', 'file,pipe', '-f', container, '-i', str(source), '-map', '0:v:0', '-map', '0:a?',
         '-vf', 'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1',
         '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-r', '24',
         '-c:a', 'aac', '-movflags', '+faststart', str(destination)])
    return inspect(destination)


def font(size: int):
    candidates = [os.environ.get('STUDIO_FONT', ''), 'C:/Windows/Fonts/msyh.ttc',
                  '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                  '/System/Library/Fonts/PingFang.ttc']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise ValueError('未找到中文字体，请配置 STUDIO_FONT 指向中文字体文件。')


def render(destination: Path, text: str, duration: int, background: Path | None = None) -> dict:
    overlay = destination.with_suffix('.png')
    canvas = Image.new('RGBA', (720, 1280), (12, 18, 34, 255) if not background else (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    if not background:
        for vertical in range(1280):
            draw.line((0, vertical, 720, vertical), fill=(12 + vertical // 90, 18 + vertical // 60, 34 + vertical // 35, 255))
        draw.rounded_rectangle((48, 100, 230, 150), radius=20, fill='#7964ff')
        draw.text((68, 108), 'LIVE MAKE', font=font(24), fill='white')
    lines = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=13) or [''])
    if len(lines) > 12:
        raise ValueError('文案换行过多，请控制在 12 行以内。')
    top = 390 if not background else min(760, 1190 - len(lines) * 48)
    if background:
        draw.rounded_rectangle((32, top - 28, 688, min(1220, top + len(lines) * 48 + 28)), radius=24, fill=(0, 0, 0, 185))
    for index, line in enumerate(lines):
        draw.text((60, top + index * 48), line, font=font(36), fill='white')
    canvas.save(overlay)
    try:
        inputs = ['-loop', '1', '-i', str(overlay)]
        filters = []
        if background:
            inputs = ['-stream_loop', '-1', '-protocol_whitelist', 'file,pipe', '-i', str(background), *inputs]
            filters = ['-filter_complex', '[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[bg];[bg][1:v]overlay=0:0[out]', '-map', '[out]', '-map', '0:a?']
        run([*inputs, *filters, '-t', str(duration), '-r', '24', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast',
             '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(destination)])
    finally:
        overlay.unlink(missing_ok=True)
    return inspect(destination)
