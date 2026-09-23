# Web Studio Implementation Plan

**Goal:** Deliver a usable local video workbench with honest integration status.

**Architecture:** Existing Python API and SQLite, isolated studio service, one worker, same-origin static UI and authenticated media.

**Tech Stack:** Python, SQLite, Pillow, imageio-ffmpeg, requests, HTML/CSS/JavaScript.

**Spec:** docs/superpowers/specs/2026-09-23-web-studio-design.md

## Constraints

No commits or subagents. Preserve existing workflow gates. Bind localhost. Keep secrets server-side. No real publication without explicit confirmation.

## Tasks

- [x] Tests first: tests/studio/test_studio.py covers auth, file validation, jobs, reviews and publication guards.
- [x] media.py: bounded conversion, Chinese template generation and real output verification.
- [x] douyin.py: OAuth, upload/create and explicit error handling; mock provider calls in tests.
- [x] studio.py: durable media/jobs/reviews/publications and authenticated studio routes.
- [x] server.py/app.py: cookies, me/logout, request limits, origin checks, static files and byte ranges.
- [x] apps/web: Chinese login, tasks, upload/progress, preview, generation, version review and confirmed publishing.
- [x] Update contracts, requirements, environment example, launcher and README.
- [x] Run regression and browser checks; leave verified service running with clear external prerequisites.

## Verification

2026-09-23: 56 tests passed, 2 existing external-media integration tests skipped. Chrome verified login, task creation, upload, actual video decoding, background template rendering, review, download, reload persistence, settings, mobile width and logout. JavaScript/Python/PowerShell syntax checks and git diff --check passed. Live Douyin publication was not attempted because no application credentials were provided.
