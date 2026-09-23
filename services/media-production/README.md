# Media production and QC

This service owns media asset production and automated quality checks for issue
#13. It consumes `task.media_requested.v1` and emits
`task.media_qc_completed.v1`. Workflow state transitions remain owned by the
workflow service.

The implementation currently uses a deterministic local provider. A real
digital-human, TTS, or FFmpeg provider can be added behind the interfaces in
`src/media_production/providers.py` without changing the pipeline or QC rules.

The shared contracts are intentionally unchanged while the event payload is
being aligned with the other service owners. Provisional values are isolated in
`src/media_production/contract_adapter.py`.

Workflow requests use positive integer `script_version` and `media_version`
values. The workflow adapter can use `resolve_video_uri` and
`serialize_asset` from `src/media_production/asset_adapter.py` to persist media
versions and assets through the business API.

Run the tests from the repository root:

```powershell
python -m unittest discover -s tests/media_production_service -v
```
