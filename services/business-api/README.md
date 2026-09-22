# Business API

The implementation lives in the importable package `services/business_api`.

Run locally from the repository root:

```powershell
$env:BUSINESS_API_ADMIN_USERNAME = "admin"
$env:BUSINESS_API_ADMIN_PASSWORD = "replace-with-a-long-password"
python -m services.business_api.server
```

The bootstrap administrator is created only when the database has no accounts and both bootstrap variables are set. Passwords are stored as PBKDF2-SHA256 hashes and are never returned by the API.

The service exposes the routes in `contracts/openapi.yaml`. Use `Authorization: Bearer <access_token>` for user routes. Workflow transitions require `X-Internal-Service: workflow` and are not available to regular users.

The publishing context endpoint `GET /api/v1/internal/tasks/{task_id}/publishing-context`
requires the dedicated Bearer credential set in `BUSINESS_API_PUBLISHING_TOKEN`.
Configure the same value as `BUSINESS_API_TOKEN` in the publishing service. Missing
configuration denies access; user sessions and `X-Internal-Service` do not grant access.
The endpoint rechecks current versions, enabled reviewers, version-bound approval
votes, workflow QC and risk in one database read snapshot. Media versions are positive
integers. Missing/unregistered/unscheduled workflow context is not approved implicitly.
