from __future__ import annotations


class LocalMediaAdapter:
    def __init__(self, workflow, pipeline, resolve_video_uri):
        self.workflow = workflow
        self.pipeline = pipeline
        self.resolve_video_uri = resolve_video_uri

    def dispatch(self, event):
        if event["event_type"] != "task.media_requested.v1":
            raise ValueError("media request required")
        if not any(item["event_id"] == event["event_id"] for item in self.workflow.pending()):
            return False
        result = self.pipeline.handle_media_requested(event)
        data = event["data"]
        task_id = data["task_id"]
        current = self.workflow.database.get_task(task_id)["current_media_version"] or 0
        if current == data["media_version"] - 1:
            uri = self.resolve_video_uri(result, self.pipeline.asset_history)
            if not uri:
                raise ValueError("media provider must resolve an actual video URI")
            self.workflow.database.add_media_version("media-production", task_id, uri, {"asset_ids": result["data"]["asset_ids"]})
        elif current != data["media_version"]:
            raise ValueError("media version sequence does not match business API")
        self.workflow.receive(result)
        self.workflow.acknowledge(event["event_id"])
        return True
