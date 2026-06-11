from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lifecycle_db_path(paths: Any) -> Path:
    return Path(paths.monitor_dir) / "sessions.db"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _new_project_id() -> str:
    return f"proj-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _new_topic_id() -> str:
    return f"topic-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _new_snapshot_id() -> str:
    return f"snap-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _connect(paths: Any) -> sqlite3.Connection:
    db_path = lifecycle_db_path(paths)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_lifecycle_store(conn)
    return conn


def ensure_lifecycle_store(conn_or_paths: sqlite3.Connection | Any) -> None:
    owns_connection = not isinstance(conn_or_paths, sqlite3.Connection)
    conn = _connect(conn_or_paths) if owns_connection else conn_or_paths
    try:
        conn.executescript(
            """
            create table if not exists topics (
              topic_id text primary key,
              topic_title text not null,
              task_fingerprint text not null default '',
              status text not null default 'active',
              created_at text not null,
              updated_at text not null,
              tags_json text not null default '[]'
            );

            create table if not exists projects (
              project_id text primary key,
              title text not null,
              status text not null,
              created_at text not null,
              updated_at text not null,
              archived_at text not null default '',
              topic_id text not null default '',
              attempt_index integer not null default 0,
              attempt_title text not null default '',
              task_fingerprint text not null default '',
              debug_flag integer not null default 0,
              title_source text not null default '',
              tags_json text not null default '[]',
              parent_project_id text not null default '',
              current_session_id text not null default '',
              current_program_id text not null default '',
              program_status text not null default 'not_started',
              active_run_id text not null default '',
              source_snapshot_id text not null default '',
              pending_action_json text not null default 'null',
              run_ids_json text not null default '[]',
              artifact_refs_json text not null default '[]',
              manifest_path text not null default ''
            );

            create table if not exists sessions (
              session_id text primary key,
              project_id text not null,
              path text not null default '',
              status text not null,
              created_at text not null,
              updated_at text not null,
              archived_at text not null default ''
            );

            create table if not exists program_refs (
              program_ref_id text primary key,
              project_id text not null,
              program_id text not null,
              version text not null default '',
              status text not null default '',
              path text not null default '',
              created_at text not null,
              updated_at text not null
            );

            create table if not exists runs (
              run_id text primary key,
              project_id text not null,
              session_id text not null default '',
              program_id text not null default '',
              status text not null default '',
              artifact_refs_json text not null default '[]',
              created_at text not null,
              updated_at text not null
            );

            create table if not exists snapshots (
              snapshot_id text primary key,
              project_id text not null,
              session_id text not null default '',
              program_id text not null default '',
              run_id text not null default '',
              title text not null default '',
              pending_action_json text not null default 'null',
              artifact_refs_json text not null default '[]',
              created_at text not null
            );

            create table if not exists lifecycle_events (
              event_id text primary key,
              event_type text not null,
              project_id text not null default '',
              session_id text not null default '',
              snapshot_id text not null default '',
              payload_json text not null default '{}',
              created_at text not null
            );

            create table if not exists current_state (
              key text primary key,
              value text not null
            );

            create index if not exists idx_topics_fingerprint on topics(task_fingerprint);
            """
        )
        _ensure_column(conn, "projects", "topic_id", "text not null default ''")
        _ensure_column(conn, "projects", "attempt_index", "integer not null default 0")
        _ensure_column(conn, "projects", "attempt_title", "text not null default ''")
        _ensure_column(conn, "projects", "task_fingerprint", "text not null default ''")
        _ensure_column(conn, "projects", "debug_flag", "integer not null default 0")
        _ensure_column(conn, "projects", "title_source", "text not null default ''")
        _ensure_column(conn, "projects", "tags_json", "text not null default '[]'")
        conn.execute("create index if not exists idx_projects_topic_id on projects(topic_id)")
        conn.execute("create index if not exists idx_projects_task_fingerprint on projects(task_fingerprint)")
        conn.commit()
    finally:
        if owns_connection:
            conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    existing = {str(row[1]) for row in conn.execute(f"pragma table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"alter table {table} add column {column} {declaration}")


def _record_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    project_id: str = "",
    session_id: str = "",
    snapshot_id: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        insert into lifecycle_events
          (event_id, event_type, project_id, session_id, snapshot_id, payload_json, created_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"evt-{uuid.uuid4().hex[:12]}",
            event_type,
            project_id,
            session_id,
            snapshot_id,
            _json_dumps(payload or {}),
            utc_now(),
        ),
    )


def _set_current_project(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        "insert or replace into current_state (key, value) values ('current_project_id', ?)",
        (project_id,),
    )


def _row_to_project(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    project = dict(row)
    project["pending_action"] = _json_loads(project.pop("pending_action_json", "null"), None)
    project["run_ids"] = _json_loads(project.pop("run_ids_json", "[]"), [])
    project["artifact_refs"] = _json_loads(project.pop("artifact_refs_json", "[]"), [])
    project["tags"] = _json_loads(project.pop("tags_json", "[]"), [])
    try:
        project["attempt_index"] = int(project.get("attempt_index") or 0)
    except (TypeError, ValueError):
        project["attempt_index"] = 0
    project["debug_flag"] = str(project.get("debug_flag") or "").lower() in {"1", "true", "yes"}
    if not project.get("attempt_title"):
        project["attempt_title"] = project.get("title", "")
    project["experiment_id"] = project["project_id"]
    project["intake_session_id"] = project.get("current_session_id", "")
    project["program_id"] = project.get("current_program_id", "")
    return project


def _row_to_topic(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    topic = dict(row)
    topic["tags"] = _json_loads(topic.pop("tags_json", "[]"), [])
    return topic


def _upsert_session(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    session_id: str,
    path: str = "",
    status: str = "active",
    now: str,
) -> None:
    if not session_id:
        return
    conn.execute(
        """
        insert into sessions (session_id, project_id, path, status, created_at, updated_at, archived_at)
        values (?, ?, ?, ?, ?, ?, '')
        on conflict(session_id) do update set
          project_id=excluded.project_id,
          path=excluded.path,
          status=excluded.status,
          updated_at=excluded.updated_at
        """,
        (session_id, project_id, path, status, now, now),
    )


def _upsert_program_ref(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    program_id: str,
    status: str,
    version: str = "",
    path: str = "",
    now: str,
) -> None:
    if not program_id:
        return
    program_ref_id = f"{project_id}:{program_id}:{version or 'current'}"
    conn.execute(
        """
        insert into program_refs
          (program_ref_id, project_id, program_id, version, status, path, created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(program_ref_id) do update set
          status=excluded.status,
          path=excluded.path,
          updated_at=excluded.updated_at
        """,
        (program_ref_id, project_id, program_id, version, status, path, now, now),
    )


def create_project(
    paths: Any,
    *,
    title: str,
    intake_session_id: str,
    project_id: str | None = None,
    status: str = "active",
    program_id: str = "",
    program_status: str = "not_started",
    pending_action: dict[str, Any] | None = None,
    run_ids: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    parent_project_id: str = "",
    source_snapshot_id: str = "",
    manifest_path: str = "",
    topic_id: str = "",
    attempt_index: int = 0,
    attempt_title: str = "",
    task_fingerprint: str = "",
    debug_flag: bool = False,
    title_source: str = "",
    tags: list[str] | None = None,
    set_current: bool = True,
) -> dict[str, Any]:
    now = utc_now()
    project_id = project_id or _new_project_id()
    run_ids = list(run_ids or [])
    artifact_refs = list(artifact_refs or [])
    tags = [str(item) for item in tags or [] if str(item).strip()]
    with _connect(paths) as conn:
        conn.execute(
            """
            insert into projects (
              project_id, title, status, created_at, updated_at, archived_at,
              topic_id, attempt_index, attempt_title, task_fingerprint, debug_flag,
              title_source, tags_json, parent_project_id, current_session_id,
              current_program_id, program_status, active_run_id, source_snapshot_id,
              pending_action_json, run_ids_json, artifact_refs_json, manifest_path
            )
            values (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(project_id) do update set
              title=excluded.title,
              status=excluded.status,
              updated_at=excluded.updated_at,
              topic_id=excluded.topic_id,
              attempt_index=excluded.attempt_index,
              attempt_title=excluded.attempt_title,
              task_fingerprint=excluded.task_fingerprint,
              debug_flag=excluded.debug_flag,
              title_source=excluded.title_source,
              tags_json=excluded.tags_json,
              parent_project_id=excluded.parent_project_id,
              current_session_id=excluded.current_session_id,
              current_program_id=excluded.current_program_id,
              program_status=excluded.program_status,
              active_run_id=excluded.active_run_id,
              source_snapshot_id=excluded.source_snapshot_id,
              pending_action_json=excluded.pending_action_json,
              run_ids_json=excluded.run_ids_json,
              artifact_refs_json=excluded.artifact_refs_json,
              manifest_path=excluded.manifest_path
            """,
            (
                project_id,
                title or "未命名项目",
                status,
                now,
                now,
                topic_id,
                int(attempt_index or 0),
                attempt_title or title or "未命名项目",
                task_fingerprint,
                1 if debug_flag else 0,
                title_source,
                _json_dumps(tags),
                parent_project_id,
                intake_session_id,
                program_id,
                program_status or "not_started",
                run_ids[0] if run_ids else "",
                source_snapshot_id,
                _json_dumps(pending_action),
                _json_dumps(run_ids),
                _json_dumps(artifact_refs),
                manifest_path,
            ),
        )
        _upsert_session(conn, project_id=project_id, session_id=intake_session_id, now=now)
        _upsert_program_ref(conn, project_id=project_id, program_id=program_id, status=program_status, now=now)
        if topic_id:
            conn.execute("update topics set updated_at = ? where topic_id = ?", (now, topic_id))
        if set_current:
            _set_current_project(conn, project_id)
        _record_event(
            conn,
            event_type="project_upsert",
            project_id=project_id,
            session_id=intake_session_id,
            payload={"status": status, "source_snapshot_id": source_snapshot_id},
        )
        conn.commit()
    return get_project(paths, project_id)


def import_experiment_manifest(paths: Any, manifest: dict[str, Any], *, set_current: bool = False) -> dict[str, Any]:
    project_id = str(manifest.get("project_id") or manifest.get("experiment_id") or "").strip()
    if not project_id:
        raise ValueError("manifest 缺少 experiment_id/project_id")
    return create_project(
        paths,
        project_id=project_id,
        title=str(manifest.get("title") or "未命名项目"),
        status=str(manifest.get("status") or "active"),
        intake_session_id=str(manifest.get("intake_session_id") or manifest.get("session_id") or ""),
        program_id=str(manifest.get("program_id") or ""),
        program_status=str(manifest.get("program_status") or "not_started"),
        pending_action=manifest.get("pending_action") if isinstance(manifest.get("pending_action"), dict) else None,
        run_ids=[str(item) for item in manifest.get("run_ids", [])] if isinstance(manifest.get("run_ids"), list) else [],
        artifact_refs=[str(item) for item in manifest.get("artifact_refs", [])] if isinstance(manifest.get("artifact_refs"), list) else [],
        parent_project_id=str(manifest.get("parent_project_id") or ""),
        source_snapshot_id=str(manifest.get("source_snapshot_id") or ""),
        manifest_path=str(manifest.get("path") or ""),
        topic_id=str(manifest.get("topic_id") or ""),
        attempt_index=int(manifest.get("attempt_index") or 0),
        attempt_title=str(manifest.get("attempt_title") or manifest.get("title") or ""),
        task_fingerprint=str(manifest.get("task_fingerprint") or ""),
        debug_flag=bool(manifest.get("debug_flag")),
        title_source=str(manifest.get("title_source") or ""),
        tags=[str(item) for item in manifest.get("tags", [])] if isinstance(manifest.get("tags"), list) else [],
        set_current=set_current,
    )


def set_current_project(paths: Any, project_id: str) -> None:
    with _connect(paths) as conn:
        if conn.execute("select 1 from projects where project_id = ?", (project_id,)).fetchone() is None:
            raise ValueError(f"找不到项目：{project_id}")
        _set_current_project(conn, project_id)
        conn.commit()


def create_topic(
    paths: Any,
    *,
    topic_title: str,
    task_fingerprint: str = "",
    topic_id: str | None = None,
    status: str = "active",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    now = utc_now()
    topic_id = topic_id or _new_topic_id()
    tags = [str(item) for item in tags or [] if str(item).strip()]
    with _connect(paths) as conn:
        conn.execute(
            """
            insert into topics (
              topic_id, topic_title, task_fingerprint, status, created_at, updated_at, tags_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(topic_id) do update set
              topic_title=excluded.topic_title,
              task_fingerprint=excluded.task_fingerprint,
              status=excluded.status,
              updated_at=excluded.updated_at,
              tags_json=excluded.tags_json
            """,
            (
                topic_id,
                topic_title or "未命名任务",
                task_fingerprint,
                status or "active",
                now,
                now,
                _json_dumps(tags),
            ),
        )
        _record_event(
            conn,
            event_type="topic_upsert",
            payload={"topic_id": topic_id, "task_fingerprint": task_fingerprint, "status": status},
        )
        conn.commit()
    return get_topic(paths, topic_id)


def update_topic(
    paths: Any,
    topic_id: str,
    *,
    topic_title: str | None = None,
    task_fingerprint: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    event_type: str = "topic_update",
) -> dict[str, Any]:
    current = get_topic(paths, topic_id)
    if not current:
        raise ValueError(f"找不到 Topic：{topic_id}")
    next_tags = [str(item) for item in tags] if tags is not None else list(current.get("tags") or [])
    now = utc_now()
    with _connect(paths) as conn:
        conn.execute(
            """
            update topics set
              topic_title = ?,
              task_fingerprint = ?,
              status = ?,
              updated_at = ?,
              tags_json = ?
            where topic_id = ?
            """,
            (
                topic_title if topic_title is not None else current.get("topic_title", "未命名任务"),
                task_fingerprint if task_fingerprint is not None else current.get("task_fingerprint", ""),
                status if status is not None else current.get("status", "active"),
                now,
                _json_dumps(next_tags),
                topic_id,
            ),
        )
        _record_event(conn, event_type=event_type, payload={"topic_id": topic_id})
        conn.commit()
    return get_topic(paths, topic_id)


def get_topic(paths: Any, topic_id: str) -> dict[str, Any]:
    if not topic_id:
        return {}
    with _connect(paths) as conn:
        row = conn.execute("select * from topics where topic_id = ?", (topic_id,)).fetchone()
    return _row_to_topic(row)


def find_topic_by_fingerprint(paths: Any, task_fingerprint: str) -> dict[str, Any]:
    if not task_fingerprint:
        return {}
    with _connect(paths) as conn:
        row = conn.execute(
            """
            select * from topics
            where task_fingerprint = ?
            order by updated_at desc, topic_id desc
            limit 1
            """,
            (task_fingerprint,),
        ).fetchone()
    return _row_to_topic(row)


def list_topics(paths: Any, *, include_archived: bool = True) -> list[dict[str, Any]]:
    where = "" if include_archived else "where status != 'archived'"
    with _connect(paths) as conn:
        rows = conn.execute(
            f"select * from topics {where} order by updated_at desc, topic_id desc"
        ).fetchall()
    return [_row_to_topic(row) for row in rows]


def get_current_project(paths: Any) -> dict[str, Any]:
    with _connect(paths) as conn:
        row = conn.execute(
            """
            select p.*, t.topic_title as topic_title, t.status as topic_status
            from projects p
            left join topics t on t.topic_id = p.topic_id
            join current_state c on c.value = p.project_id
            where c.key = 'current_project_id'
            """
        ).fetchone()
    return _row_to_project(row)


def get_project(paths: Any, project_id: str) -> dict[str, Any]:
    with _connect(paths) as conn:
        row = conn.execute(
            """
            select p.*, t.topic_title as topic_title, t.status as topic_status
            from projects p
            left join topics t on t.topic_id = p.topic_id
            where p.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    return _row_to_project(row)


def list_projects(paths: Any) -> list[dict[str, Any]]:
    with _connect(paths) as conn:
        rows = conn.execute(
            """
            select p.*, t.topic_title as topic_title, t.status as topic_status
            from projects p
            left join topics t on t.topic_id = p.topic_id
            order by p.updated_at desc, p.project_id desc
            """
        ).fetchall()
    return [_row_to_project(row) for row in rows]


def update_project(
    paths: Any,
    project_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    archived_at: str | None = None,
    session_id: str | None = None,
    program_id: str | None = None,
    program_status: str | None = None,
    active_run_id: str | None = None,
    pending_action: dict[str, Any] | None = None,
    clear_pending: bool = False,
    run_ids: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    topic_id: str | None = None,
    attempt_index: int | None = None,
    attempt_title: str | None = None,
    task_fingerprint: str | None = None,
    debug_flag: bool | None = None,
    title_source: str | None = None,
    tags: list[str] | None = None,
    set_current: bool = False,
    event_type: str = "project_update",
) -> dict[str, Any]:
    current = get_project(paths, project_id)
    if not current:
        raise ValueError(f"找不到项目：{project_id}")
    next_pending = None if clear_pending else (pending_action if pending_action is not None else current.get("pending_action"))
    next_run_ids = list(run_ids) if run_ids is not None else list(current.get("run_ids") or [])
    next_artifact_refs = list(artifact_refs) if artifact_refs is not None else list(current.get("artifact_refs") or [])
    next_tags = [str(item) for item in tags] if tags is not None else list(current.get("tags") or [])
    now = utc_now()
    with _connect(paths) as conn:
        conn.execute(
            """
            update projects set
              title = ?,
              status = ?,
              updated_at = ?,
              archived_at = ?,
              topic_id = ?,
              attempt_index = ?,
              attempt_title = ?,
              task_fingerprint = ?,
              debug_flag = ?,
              title_source = ?,
              tags_json = ?,
              current_session_id = ?,
              current_program_id = ?,
              program_status = ?,
              active_run_id = ?,
              pending_action_json = ?,
              run_ids_json = ?,
              artifact_refs_json = ?
            where project_id = ?
            """,
            (
                title if title is not None else current.get("title", "未命名项目"),
                status if status is not None else current.get("status", "active"),
                now,
                archived_at if archived_at is not None else current.get("archived_at", ""),
                topic_id if topic_id is not None else current.get("topic_id", ""),
                int(attempt_index if attempt_index is not None else current.get("attempt_index", 0) or 0),
                attempt_title if attempt_title is not None else current.get("attempt_title", current.get("title", "")),
                task_fingerprint if task_fingerprint is not None else current.get("task_fingerprint", ""),
                1 if (debug_flag if debug_flag is not None else bool(current.get("debug_flag"))) else 0,
                title_source if title_source is not None else current.get("title_source", ""),
                _json_dumps(next_tags),
                session_id if session_id is not None else current.get("current_session_id", ""),
                program_id if program_id is not None else current.get("program_id", ""),
                program_status if program_status is not None else current.get("program_status", "not_started"),
                active_run_id if active_run_id is not None else current.get("active_run_id", ""),
                _json_dumps(next_pending),
                _json_dumps(next_run_ids),
                _json_dumps(next_artifact_refs),
                project_id,
            ),
        )
        if session_id:
            _upsert_session(conn, project_id=project_id, session_id=session_id, now=now)
        if program_id:
            _upsert_program_ref(
                conn,
                project_id=project_id,
                program_id=program_id,
                status=program_status or str(current.get("program_status") or ""),
                now=now,
            )
        if set_current:
            _set_current_project(conn, project_id)
        _record_event(conn, event_type=event_type, project_id=project_id)
        conn.commit()
    return get_project(paths, project_id)


def archive_project(paths: Any, project_id: str, *, reason: str = "archive") -> dict[str, Any]:
    return update_project(
        paths,
        project_id,
        status="archived",
        archived_at=utc_now(),
        event_type=reason,
    )


def active_attempt_count(paths: Any, topic_id: str) -> int:
    if not topic_id:
        return 0
    with _connect(paths) as conn:
        row = conn.execute(
            "select count(*) from projects where topic_id = ? and status = 'active'",
            (topic_id,),
        ).fetchone()
    return int(row[0] if row else 0)


def next_attempt_index(paths: Any, topic_id: str) -> int:
    if not topic_id:
        return 1
    with _connect(paths) as conn:
        row = conn.execute(
            "select coalesce(max(attempt_index), 0) from projects where topic_id = ?",
            (topic_id,),
        ).fetchone()
    return int(row[0] if row else 0) + 1


def archive_topic(paths: Any, topic_id: str, *, reason: str = "archive_topic") -> dict[str, Any]:
    topic = get_topic(paths, topic_id)
    if not topic:
        raise ValueError(f"找不到 Topic：{topic_id}")
    now = utc_now()
    with _connect(paths) as conn:
        conn.execute(
            "update topics set status = 'archived', updated_at = ? where topic_id = ?",
            (now, topic_id),
        )
        conn.execute(
            """
            update projects set status = 'archived', archived_at = ?, updated_at = ?
            where topic_id = ?
            """,
            (now, now, topic_id),
        )
        _record_event(conn, event_type=reason, payload={"topic_id": topic_id})
        conn.commit()
    return get_topic(paths, topic_id)


def resume_project(paths: Any, project_id: str) -> dict[str, Any]:
    project = update_project(paths, project_id, status="active", set_current=True, event_type="resume_project")
    topic_id = str(project.get("topic_id") or "")
    if topic_id:
        update_topic(paths, topic_id, status="active", event_type="resume_topic")
    return project


def create_snapshot(
    paths: Any,
    *,
    project_id: str,
    title: str,
    session_id: str | None = None,
    program_id: str | None = None,
    run_id: str | None = None,
    pending_action: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
) -> dict[str, Any]:
    project = get_project(paths, project_id)
    if not project:
        raise ValueError(f"找不到项目：{project_id}")
    snapshot_id = _new_snapshot_id()
    session_id = session_id if session_id is not None else str(project.get("current_session_id") or "")
    program_id = program_id if program_id is not None else str(project.get("program_id") or "")
    run_id = run_id if run_id is not None else str(project.get("active_run_id") or "")
    pending_action = pending_action if pending_action is not None else project.get("pending_action")
    artifact_refs = list(artifact_refs) if artifact_refs is not None else list(project.get("artifact_refs") or [])
    now = utc_now()
    with _connect(paths) as conn:
        conn.execute(
            """
            insert into snapshots (
              snapshot_id, project_id, session_id, program_id, run_id, title,
              pending_action_json, artifact_refs_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                project_id,
                session_id,
                program_id,
                run_id,
                title or "未命名快照",
                _json_dumps(pending_action),
                _json_dumps(artifact_refs),
                now,
            ),
        )
        _record_event(
            conn,
            event_type="snapshot",
            project_id=project_id,
            session_id=session_id,
            snapshot_id=snapshot_id,
            payload={"run_id": run_id, "program_id": program_id},
        )
        conn.commit()
    return get_snapshot(paths, snapshot_id)


def get_snapshot(paths: Any, snapshot_id: str) -> dict[str, Any]:
    with _connect(paths) as conn:
        row = conn.execute("select * from snapshots where snapshot_id = ?", (snapshot_id,)).fetchone()
    if row is None:
        return {}
    snapshot = dict(row)
    snapshot["pending_action"] = _json_loads(snapshot.pop("pending_action_json", "null"), None)
    snapshot["artifact_refs"] = _json_loads(snapshot.pop("artifact_refs_json", "[]"), [])
    return snapshot


def fork_project_from_snapshot(paths: Any, snapshot_id: str, *, new_intake_session_id: str) -> dict[str, Any]:
    snapshot = get_snapshot(paths, snapshot_id)
    if not snapshot:
        raise ValueError(f"找不到快照：{snapshot_id}")
    source = get_project(paths, str(snapshot.get("project_id") or ""))
    if not source:
        raise ValueError(f"快照缺少来源项目：{snapshot_id}")
    topic_id = str(source.get("topic_id") or "")
    forked = create_project(
        paths,
        title=f"{source.get('title') or '未命名项目'} fork",
        intake_session_id=new_intake_session_id,
        program_id=str(snapshot.get("program_id") or source.get("program_id") or ""),
        program_status=str(source.get("program_status") or "not_started"),
        pending_action=None,
        run_ids=[],
        artifact_refs=[str(item) for item in snapshot.get("artifact_refs", [])],
        parent_project_id=str(source.get("project_id") or ""),
        source_snapshot_id=snapshot_id,
        topic_id=topic_id,
        attempt_index=next_attempt_index(paths, topic_id) if topic_id else 0,
        attempt_title=f"{source.get('attempt_title') or source.get('title') or '未命名项目'} fork",
        task_fingerprint=str(source.get("task_fingerprint") or ""),
        debug_flag=bool(source.get("debug_flag")),
        title_source=str(source.get("title_source") or "fork"),
        tags=list(source.get("tags") or []),
        set_current=True,
    )
    return forked


def reset_current_run(paths: Any, project_id: str) -> dict[str, Any]:
    current = get_project(paths, project_id)
    if not current:
        raise ValueError(f"找不到项目：{project_id}")
    return update_project(
        paths,
        project_id,
        active_run_id="",
        run_ids=[],
        clear_pending=True,
        artifact_refs=list(current.get("artifact_refs") or []),
        event_type="reset_current_run",
    )


def format_projects_list(paths: Any) -> str:
    rows = list_projects(paths)
    if not rows:
        return "项目列表：当前还没有 Project。"
    current = get_current_project(paths)
    current_id = str(current.get("project_id") or "")
    lines = ["项目列表："]
    for item in rows[:20]:
        marker = "*" if str(item.get("project_id") or "") == current_id else "-"
        topic_title = str(item.get("topic_title") or ("未归组任务" if not item.get("topic_id") else item.get("topic_id") or "-"))
        attempt_title = str(item.get("attempt_title") or item.get("title") or "未命名尝试")
        debug = " · debug" if item.get("debug_flag") else ""
        lines.append(
            f"{marker} {item.get('project_id')} · {item.get('status') or '-'} · "
            f"Topic:{topic_title} · Attempt:{item.get('attempt_index') or '-'} {attempt_title}{debug} · "
            f"Session:{item.get('current_session_id') or '-'} · "
            f"Program:{item.get('program_status') or 'not_started'} · Run:{item.get('active_run_id') or '-'}"
        )
    return "\n".join(lines)
