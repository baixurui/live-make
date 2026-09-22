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
