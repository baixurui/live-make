# Live Make Web Studio

Approved scope: login, persistent tasks, local upload/preview, template generation and official Douyin integration.

Keep Python/SQLite and serve a same-origin Chinese UI. Add isolated studio tables for files, jobs, reviews and publication records. Automatic workflow tasks remain read-only in this manual workbench.

Uploads: MP4/MOV/WebM, maximum 100 MiB, decode and normalize to MP4. Random storage IDs, authenticated reads and byte ranges. Templates: Pillow and FFmpeg, Chinese captions, optional uploaded background, 720x1280, 7-15 seconds. No simulated AI generation.

One background worker, bounded queue and subprocess timeout. Serialize database access and persist jobs; restart marks interrupted work failed. Store local data in ignored .runtime directories.

Douyin: official OAuth with one-time state and server-side tokens; upload then create. Require current-video/current-caption review (LOW one person, HIGH two people, BLOCKED prohibited), admin access and explicit confirmation. Default private visibility. One publication per account per Beijing day; uncertain submission occupies the slot and is never automatically retried. Accepted requests are SUBMITTED, not publicly published. Missing credentials disable publishing.

Security: HttpOnly sessions, same-origin writes, bounded requests and localhost binding. Production HTTPS, rate limiting, secret encryption and distributed workers are out of scope.

Validation: unit tests, real transcoding, HTTP auth/ranges, browser login/upload/preview/generation/persistence, mocked platform protocol and existing regression suite. Live Douyin verification requires operator credentials and is explicitly not claimed without them.
