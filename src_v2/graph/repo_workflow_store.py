# src_v2/graph/repo_workflow_store.py
"""
Durable repo workflow coordination for human approval and repo readiness.

This store is intentionally separate from the LangGraph checkpoint database.
LangGraph owns graph execution state. This store owns external workflow events:
repo selection requested, selected repo received, resume claimed/completed,
and future repo-readiness status.

Design goals:
- Do not lose fast simulator or Slack button clicks.
- Validate selected repos against the options presented to the user.
- Make duplicate Slack retries idempotent.
- Avoid timing sleeps/workarounds around LangGraph interrupts.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import aiosqlite


DEFAULT_DB_PATH = os.getenv("REPO_WORKFLOW_DB_PATH", "/data/repo_workflows.sqlite")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS repo_workflows (
            thread_id TEXT PRIMARY KEY,
            channel_id TEXT,
            thread_ts TEXT,
            status TEXT NOT NULL,
            repo_options_json TEXT NOT NULL DEFAULT '[]',
            selected_repo TEXT,
            selection_source TEXT,
            selection_received_at TEXT,
            resume_started_at TEXT,
            resume_completed_at TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await db.commit()


@asynccontextmanager
async def _workflow_db(db_path: str = DEFAULT_DB_PATH) -> AsyncIterator[aiosqlite.Connection]:
    """
    Open one aiosqlite connection and close it exactly once.

    Avoid using `async with await aiosqlite.connect(...)` because awaiting the
    connection starts the worker thread, and entering it again can try to start
    that same thread a second time.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row

    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await _ensure_schema(db)
        yield db
    finally:
        await db.close()


def _row_to_dict(row: Optional[aiosqlite.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None

    data = dict(row)

    try:
        data["repo_options"] = json.loads(data.get("repo_options_json") or "[]")
    except json.JSONDecodeError:
        data["repo_options"] = []

    return data


async def get_workflow(thread_id: str) -> Optional[dict[str, Any]]:
    async with _workflow_db() as db:
        cursor = await db.execute(
            "SELECT * FROM repo_workflows WHERE thread_id = ?",
            (thread_id,),
        )
        row = await cursor.fetchone()
        return _row_to_dict(row)


async def record_selection_requested(
    *,
    thread_id: str,
    channel_id: Optional[str],
    thread_ts: Optional[str],
    repo_options: list[str],
) -> dict[str, Any]:
    """
    Record that the app presented repo choices to the user.

    If a selection has already been received for this thread, do not erase it.
    This protects against duplicate /slack/events processing or retries.
    """
    now = _utc_now()
    repo_options_json = json.dumps(repo_options)

    async with _workflow_db() as db:
        cursor = await db.execute(
            "SELECT * FROM repo_workflows WHERE thread_id = ?",
            (thread_id,),
        )
        existing = _row_to_dict(await cursor.fetchone())

        if existing and existing.get("selected_repo"):
            return {
                "ok": True,
                "status": existing["status"],
                "thread_id": thread_id,
                "selected_repo": existing.get("selected_repo"),
                "message": "Selection already exists; request not overwritten.",
            }

        await db.execute(
            """
            INSERT INTO repo_workflows (
                thread_id,
                channel_id,
                thread_ts,
                status,
                repo_options_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                thread_ts = excluded.thread_ts,
                status = 'repo_selection_requested',
                repo_options_json = excluded.repo_options_json,
                error = NULL,
                updated_at = excluded.updated_at
            """,
            (
                thread_id,
                channel_id,
                thread_ts,
                "repo_selection_requested",
                repo_options_json,
                now,
                now,
            ),
        )
        await db.commit()

    return {
        "ok": True,
        "status": "repo_selection_requested",
        "thread_id": thread_id,
        "repo_options": repo_options,
    }


async def record_selection_received(
    *,
    thread_id: str,
    selected_repo: str,
    source: str = "slack_interaction",
) -> dict[str, Any]:
    """
    Record a human/simulator repo selection idempotently.

    This validates the selected repo against the original options and protects
    against duplicate clicks, Slack retries, and conflicting selections.
    """
    now = _utc_now()

    async with _workflow_db() as db:
        cursor = await db.execute(
            "SELECT * FROM repo_workflows WHERE thread_id = ?",
            (thread_id,),
        )
        workflow = _row_to_dict(await cursor.fetchone())

        if not workflow:
            return {
                "ok": False,
                "status": "missing_workflow",
                "thread_id": thread_id,
                "selected_repo": selected_repo,
                "message": "No repo selection request exists for this thread.",
            }

        repo_options = workflow.get("repo_options") or []

        if selected_repo not in repo_options:
            error = (
                f"Invalid repo selection {selected_repo!r}. "
                f"Expected one of: {repo_options!r}"
            )
            await db.execute(
                """
                UPDATE repo_workflows
                SET status = 'repo_selection_invalid',
                    error = ?,
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (error, now, thread_id),
            )
            await db.commit()

            return {
                "ok": False,
                "status": "repo_selection_invalid",
                "thread_id": thread_id,
                "selected_repo": selected_repo,
                "message": error,
            }

        existing_selection = workflow.get("selected_repo")
        existing_status = workflow.get("status")

        if existing_selection and existing_selection != selected_repo:
            return {
                "ok": False,
                "status": "repo_selection_conflict",
                "thread_id": thread_id,
                "selected_repo": selected_repo,
                "existing_selection": existing_selection,
                "message": "A different repo was already selected for this thread.",
            }

        if existing_selection == selected_repo:
            return {
                "ok": True,
                "status": existing_status,
                "thread_id": thread_id,
                "selected_repo": selected_repo,
                "message": "Duplicate selection received; no state change needed.",
            }

        await db.execute(
            """
            UPDATE repo_workflows
            SET status = 'repo_selection_received',
                selected_repo = ?,
                selection_source = ?,
                selection_received_at = ?,
                error = NULL,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (selected_repo, source, now, now, thread_id),
        )
        await db.commit()

    return {
        "ok": True,
        "status": "repo_selection_received",
        "thread_id": thread_id,
        "selected_repo": selected_repo,
    }


async def get_pending_selection(thread_id: str) -> Optional[str]:
    """
    Return a selected repo that has been recorded but not consumed/resumed yet.
    """
    workflow = await get_workflow(thread_id)

    if not workflow:
        return None

    if workflow.get("status") != "repo_selection_received":
        return None

    return workflow.get("selected_repo")


async def mark_selection_consumed(
    *,
    thread_id: str,
    selected_repo: str,
    consumed_by: str,
) -> None:
    now = _utc_now()

    async with _workflow_db() as db:
        await db.execute(
            """
            UPDATE repo_workflows
            SET status = 'repo_selection_consumed',
                selected_repo = COALESCE(selected_repo, ?),
                resume_completed_at = ?,
                error = NULL,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (selected_repo, now, now, thread_id),
        )
        await db.commit()


async def claim_resume(thread_id: str) -> bool:
    """
    Atomically claim the right to resume a waiting graph.

    Returns True for exactly one caller. Duplicate Slack retries will return False.
    """
    now = _utc_now()

    async with _workflow_db() as db:
        cursor = await db.execute(
            """
            UPDATE repo_workflows
            SET status = 'repo_resume_in_progress',
                resume_started_at = ?,
                error = NULL,
                updated_at = ?
            WHERE thread_id = ?
              AND status = 'repo_selection_received'
              AND selected_repo IS NOT NULL
            """,
            (now, now, thread_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def mark_resume_completed(thread_id: str) -> None:
    now = _utc_now()

    async with _workflow_db() as db:
        await db.execute(
            """
            UPDATE repo_workflows
            SET status = 'repo_resume_completed',
                resume_completed_at = ?,
                error = NULL,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (now, now, thread_id),
        )
        await db.commit()


async def mark_resume_failed(thread_id: str, error: str) -> None:
    now = _utc_now()

    async with _workflow_db() as db:
        await db.execute(
            """
            UPDATE repo_workflows
            SET status = 'repo_resume_failed',
                error = ?,
                updated_at = ?
            WHERE thread_id = ?
            """,
            (error, now, thread_id),
        )
        await db.commit()
