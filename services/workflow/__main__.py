from __future__ import annotations

import argparse
import json
import os
import logging
import time
from urllib.error import URLError

from services.business_api.db import Database
from .engine import Workflow
from .publishing_adapter import PublishingAdapter


def main():
    parser = argparse.ArgumentParser(description="Local workflow operator; use the same SQLite file as Business API.")
    parser.add_argument("--database", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--interval", type=float, default=1)
    commands.add_parser("tick")
    commands.add_parser("pending")
    commands.add_parser("sync-publishing")
    register = commands.add_parser("register")
    register.add_argument("task_id")
    register.add_argument("account_id")
    register.add_argument("--script-version", type=int, default=1)
    register.add_argument("--media-version", type=int, default=1)
    register.add_argument("--voice-version", default="voice-v1")
    receive = commands.add_parser("receive")
    receive.add_argument("file")
    for command in ("inspect", "review", "pause", "cancel", "resume"):
        commands.add_parser(command).add_argument("task_id")
    reschedule = commands.add_parser("reschedule")
    reschedule.add_argument("task_id")
    reschedule.add_argument("date")
    restart = commands.add_parser("restart")
    restart.add_argument("task_id")
    restart.add_argument("--script-version", type=int)
    restart.add_argument("--media-version", type=int)
    commands.add_parser("acknowledge").add_argument("event_id")
    commands.add_parser("retry-search").add_argument("topic_id")
    scan = commands.add_parser("scan-result")
    scan.add_argument("topic_id")
    scan.add_argument("date")
    scan.add_argument("request_key")
    scan.add_argument("result", choices=("success", "failure"))
    args = parser.parse_args()
    database = Database(args.database)
    workflow = Workflow(database)
    publishing_url = os.environ.get("PUBLISHING_SERVICE_URL")
    adapter = PublishingAdapter(workflow, publishing_url, os.environ.get("WORKFLOW_PUBLISHING_TOKEN", "")) if publishing_url else None
    try:
        if args.command == "run":
            if args.interval <= 0:
                parser.error("interval must be positive")
            while True:
                workflow.tick()
                if adapter:
                    try:
                        adapter.sync()
                    except (URLError, TimeoutError, OSError):
                        logging.warning("publishing transport unavailable; retrying unchanged messages next tick")
                time.sleep(args.interval)
        elif args.command == "sync-publishing":
            if adapter is None:
                parser.error("PUBLISHING_SERVICE_URL and WORKFLOW_PUBLISHING_TOKEN are required")
            adapter.sync()
        elif args.command == "register":
            workflow.register(args.task_id, args.account_id, args.script_version, args.media_version, args.voice_version)
        elif args.command == "receive":
            with open(args.file, encoding="utf-8") as source:
                workflow.receive(json.load(source))
        elif args.command == "reschedule":
            print(workflow.reschedule(args.task_id, args.date))
        elif args.command == "restart":
            workflow.restart(args.task_id, args.script_version, args.media_version)
        elif args.command == "acknowledge":
            workflow.acknowledge(args.event_id)
        elif args.command == "retry-search":
            workflow.retry_search(args.topic_id)
        elif args.command == "scan-result":
            print(workflow.scan_result(args.topic_id, args.date, args.request_key, args.result == "success"))
        elif args.command in {"pending", "tick"}:
            result = getattr(workflow, args.command)()
            if result is not None:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            result = getattr(workflow, args.command)(args.task_id)
            if result is not None:
                print(json.dumps(result, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        pass
    finally:
        database.close()


if __name__ == "__main__":
    main()
