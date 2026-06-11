from __future__ import annotations

import argparse
import json
import sys

from .client_api import build_status_snapshot
from .commands import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PATIENCE,
    build_digest_summary,
    build_follow_summary,
    end_runtime,
    execute_task,
    format_status_summary,
    heal_mission,
    judgment_summary,
    launch_campaign,
    list_topics,
    pause_runtime,
    queue_summary,
    resume_runtime,
    start_supervision_background,
    supervise_mission,
    think,
    topic_triage,
)
from .director_plan import run_director_plan
from .paths import get_control_plane_paths
from .research_loop import (
    explain_research_track,
    run_research_loop,
    status_research_loop,
    step_research_loop,
    stop_research_loop,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autobci-agent")
    subparsers = parser.add_subparsers(dest="action")

    status = subparsers.add_parser("status")
    status.add_argument("--repo-root", default=None)
    status.add_argument("--json", action="store_true")

    digest = subparsers.add_parser("digest")
    digest.add_argument("--repo-root", default=None)
    follow = subparsers.add_parser("follow")
    follow.add_argument("--repo-root", default=None)
    think_cmd = subparsers.add_parser("think")
    think_cmd.add_argument("--repo-root", default=None)
    think_cmd.add_argument("--json", action="store_true")
    topics = subparsers.add_parser("topics")
    topics.add_argument("--repo-root", default=None)
    topics.add_argument("--json", action="store_true")
    topic_triage_cmd = subparsers.add_parser("topic-triage")
    topic_triage_cmd.add_argument("--repo-root", default=None)
    topic_triage_cmd.add_argument("--topic-id", required=True)
    topic_triage_cmd.add_argument("--title", required=True)
    topic_triage_cmd.add_argument("--goal", required=True)
    topic_triage_cmd.add_argument("--success-metric", required=True)
    topic_triage_cmd.add_argument("--scope-label", required=True)
    topic_triage_cmd.add_argument("--priority", type=float, required=True)
    topic_triage_cmd.add_argument("--promotable", action="store_true")
    queue = subparsers.add_parser("queue")
    queue.add_argument("--repo-root", default=None)
    queue.add_argument("--json", action="store_true")
    judgment = subparsers.add_parser("judgment")
    judgment.add_argument("--repo-root", default=None)
    judgment.add_argument("--json", action="store_true")
    pause = subparsers.add_parser("pause")
    pause.add_argument("--repo-root", default=None)
    resume = subparsers.add_parser("resume")
    resume.add_argument("--repo-root", default=None)
    end = subparsers.add_parser("end")
    end.add_argument("--repo-root", default=None)

    launch = subparsers.add_parser("launch")
    launch.add_argument("--repo-root", default=None)
    launch.add_argument("--campaign-id", default=None)
    launch.add_argument("--track-manifest", default=None)
    launch.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    launch.add_argument("--patience", type=int, default=DEFAULT_PATIENCE)

    execute = subparsers.add_parser("execute")
    execute.add_argument("--repo-root", default=None)
    execute.add_argument("task")
    execute.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    execute.add_argument("--patience", type=int, default=DEFAULT_PATIENCE)
    execute.add_argument("--no-supervise", action="store_true")

    heal = subparsers.add_parser("heal")
    heal.add_argument("--repo-root", default=None)
    heal.add_argument("--mission-id", default=None)
    heal.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    heal.add_argument("--patience", type=int, default=DEFAULT_PATIENCE)

    supervise = subparsers.add_parser("supervise")
    supervise.add_argument("--repo-root", default=None)
    supervise.add_argument("--mission-id", default=None)
    supervise.add_argument("--hours", type=float, default=72.0)
    supervise.add_argument("--watch-interval", type=int, default=60)
    supervise.add_argument("--summary-interval", type=int, default=600)
    supervise.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    supervise.add_argument("--patience", type=int, default=DEFAULT_PATIENCE)
    supervise.add_argument("--auto-incubate", action="store_true")
    supervise.add_argument("--foreground", action="store_true")

    director_plan = subparsers.add_parser("director-plan")
    director_plan.add_argument("--repo-root", default=None)
    director_plan.add_argument("--input", dest="input_path", default=None)
    director_plan.add_argument("--min-tracks", type=int, default=10)
    director_plan.add_argument("--web", choices=("off", "on", "auto"), default="auto")
    director_plan.add_argument(
        "--web-provider",
        choices=("disabled", "fixture", "openai_web_search", "searxng"),
        default=None,
    )
    director_plan.add_argument("--json", action="store_true")

    research_loop = subparsers.add_parser("research-loop")
    research_loop_sub = research_loop.add_subparsers(dest="research_loop_action", required=True)
    for name in ("status", "step", "stop"):
        cmd = research_loop_sub.add_parser(name)
        cmd.add_argument("--repo-root", default=None)
        cmd.add_argument("--task", default="rsvp_ship_image_only_v0")
        if name == "step":
            cmd.add_argument("--only-track", default=None)
        cmd.add_argument("--json", action="store_true")
    run_loop = research_loop_sub.add_parser("run")
    run_loop.add_argument("--repo-root", default=None)
    run_loop.add_argument("--task", default="rsvp_ship_image_only_v0")
    run_loop.add_argument("--max-steps", type=int, default=1)
    run_loop.add_argument("--json", action="store_true")
    explain_loop = research_loop_sub.add_parser("explain")
    explain_loop.add_argument("--repo-root", default=None)
    explain_loop.add_argument("--task", default="rsvp_ship_image_only_v0")
    explain_loop.add_argument("--track", required=True)
    explain_loop.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    action = args.action or "status"
    paths = get_control_plane_paths(getattr(args, "repo_root", None))

    try:
        if action == "status":
            if getattr(args, "json", False):
                print(json.dumps(build_status_snapshot(paths), ensure_ascii=False, indent=2))
            else:
                print(format_status_summary(paths))
            return 0
        if action == "digest":
            print(build_digest_summary(paths))
            return 0
        if action == "follow":
            print(build_follow_summary(paths))
            return 0
        if action == "think":
            payload = think(paths)
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(str(payload.get("research_judgment_delta") or "已完成一次思考循环。").strip())
            return 0
        if action == "topics":
            payload = {"topics": list_topics(paths)}
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                topics_rows = payload["topics"]
                if not topics_rows:
                    print("当前还没有 topic inbox。")
                else:
                    for item in topics_rows:
                        print(f"{item.get('topic_id')} | {item.get('status')} | {item.get('title')}")
            return 0
        if action == "topic-triage":
            payload = topic_triage(
                paths=paths,
                topic_id=args.topic_id,
                title=args.title,
                goal=args.goal,
                success_metric=args.success_metric,
                scope_label=args.scope_label,
                priority=args.priority,
                promotable=args.promotable,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if action == "queue":
            payload = queue_summary(paths)
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                queue_rows = payload.get("recommended_queue", [])
                if not queue_rows:
                    print("当前还没有推荐队列。")
                else:
                    for item in queue_rows:
                        print(item)
            return 0
        if action == "judgment":
            payload = judgment_summary(paths)
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                rows = payload.get("latest_judgment_updates", [])
                if not rows:
                    print("当前还没有 judgment update。")
                else:
                    for item in rows:
                        print(f"{item.get('topic_id')} | {item.get('queue_update')} | {item.get('next_recommended_action')}")
            return 0
        if action == "pause":
            print(pause_runtime(paths))
            return 0
        if action == "resume":
            print(resume_runtime(paths))
            return 0
        if action == "end":
            print(end_runtime(paths))
            return 0
        if action == "launch":
            payload = launch_campaign(
                paths,
                campaign_id=args.campaign_id,
                track_manifest_path=args.track_manifest,
                max_iterations=args.max_iterations,
                patience=args.patience,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if action == "execute":
            print(
                execute_task(
                    args.task,
                    paths=paths,
                    max_iterations=args.max_iterations,
                    patience=args.patience,
                    supervise=not args.no_supervise,
                )
            )
            return 0
        if action == "heal":
            print(
                heal_mission(
                    paths,
                    mission_id=args.mission_id,
                    max_iterations=args.max_iterations,
                    patience=args.patience,
                )
            )
            return 0
        if action == "supervise":
            if args.foreground:
                print(
                    supervise_mission(
                        paths,
                        mission_id=args.mission_id,
                        duration_hours=args.hours,
                        watch_interval_seconds=args.watch_interval,
                        summary_interval_seconds=args.summary_interval,
                        max_iterations=args.max_iterations,
                        patience=args.patience,
                        auto_incubate=args.auto_incubate,
                    )
                )
            else:
                print(
                    start_supervision_background(
                        paths,
                        mission_id=args.mission_id,
                        duration_hours=args.hours,
                        watch_interval_seconds=args.watch_interval,
                        summary_interval_seconds=args.summary_interval,
                        max_iterations=args.max_iterations,
                        patience=args.patience,
                        auto_incubate=args.auto_incubate,
                    )
                )
            return 0
        if action == "director-plan":
            payload = run_director_plan(
                paths.repo_root,
                input_path=args.input_path,
                min_tracks=args.min_tracks,
                web=args.web,
                web_provider=args.web_provider,
            )
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"Director plan: {payload.get('plan_id')}")
                print(f"Tracks: {len(payload.get('tracks') or [])}")
                print(f"Web: {payload.get('web_research', {}).get('web_status')} / {payload.get('web_research', {}).get('provider')}")
            return 0
        if action == "research-loop":
            loop_action = str(args.research_loop_action)
            if loop_action == "status":
                payload = status_research_loop(paths.repo_root, task_id=args.task)
            elif loop_action == "step":
                payload = step_research_loop(paths.repo_root, task_id=args.task, only_track_id=getattr(args, "only_track", None))
            elif loop_action == "run":
                payload = run_research_loop(paths.repo_root, task_id=args.task, max_steps=args.max_steps)
            elif loop_action == "stop":
                payload = stop_research_loop(paths.repo_root, task_id=args.task)
            elif loop_action == "explain":
                payload = explain_research_track(paths.repo_root, task_id=args.task, track_id=args.track)
            else:  # pragma: no cover - argparse prevents this.
                raise ValueError(f"Unknown research-loop action: {loop_action}")
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"Research loop {loop_action}: {payload.get('status') or payload.get('phase') or '-'}")
            return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
