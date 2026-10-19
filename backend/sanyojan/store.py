"""SQLite task snapshots and durable ordered events, updated in one transaction."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .errors import AppError
from .schemas import Event, PlanInput, Status, Task, TERMINAL, TRANSITIONS, now


class Store:
    def __init__(self, path: Path, max_tasks: int = 1000):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
              payload TEXT NOT NULL, FOREIGN KEY(task_id) REFERENCES tasks(id));
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id);
        """)
        self.max_tasks = max_tasks

    def close(self):
        self.db.close()

    def _get(self, task_id: str) -> Task:
        row = self.db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise AppError("TASK_NOT_FOUND", "Task not found.", 404)
        return Task.model_validate_json(row[0])

    def get(self, task_id: str) -> Task:
        task = self._get(task_id)
        task.logs = self.events(task_id)
        return task

    def list(self, limit: int = 50, offset: int = 0) -> list[Task]:
        # Result bodies and logs are fetched only when a history item is opened.
        rows = self.db.execute("SELECT payload FROM tasks ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        tasks = [Task.model_validate_json(row[0]) for row in rows]
        for task in tasks:
            if task.result:
                task.result.records = []
        return tasks

    def create(self, request: PlanInput) -> Task:
        count = self.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count >= self.max_tasks:
            raise AppError("HISTORY_FULL", "Task storage limit reached. Archive the local database and reports before starting a new workspace.", 429)
        task = Task(id=str(uuid4()), instruction=request.instruction, target_url=request.target_url, task_type=request.task_type)
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES (?,?)", (task.id, task.model_dump_json()))
        return self.update(task.id, "Task created; no browser action has run.", "state")

    def events(self, task_id: str, after: int = 0) -> list[Event]:
        return [Event.model_validate_json(row[0]) for row in self.db.execute(
            "SELECT payload FROM events WHERE task_id=? AND id>? ORDER BY id", (task_id, after)
        ).fetchall()]

    def update(self, task_id: str, message: str, kind: str = "log", *, status: Status | None = None, **changes) -> Task:
        # Single-process service: no await between reading, checking and writing.
        with self.db:
            task = self._get(task_id)
            if status is not None and status != task.status:
                if status not in TRANSITIONS.get(task.status, set()):
                    raise AppError("INVALID_TRANSITION", f"Cannot transition from {task.status.value} to {status.value}.", 409)
                task.status = status
                if status == Status.RUNNING:
                    task.started_at = now()
                if status in TERMINAL:
                    task.finished_at = now()
            task.updated_at = now()
            data = task.model_dump()
            data.update(changes)
            task = Task.model_validate(data)
            event = Event(id=0, task_id=task_id, timestamp=now(), type=kind, message=message[:1000], status=task.status, progress=task.progress)
            cursor = self.db.execute("INSERT INTO events(task_id,payload) VALUES (?,?)", (task_id, "{}"))
            event.id = cursor.lastrowid
            self.db.execute("UPDATE events SET payload=? WHERE id=?", (event.model_dump_json(), event.id))
            task.logs = []
            self.db.execute("UPDATE tasks SET payload=? WHERE id=?", (task.model_dump_json(), task_id))
        return self.get(task_id)

    def recover(self):
        rows = self.db.execute("SELECT id FROM tasks").fetchall()
        for (task_id,) in rows:
            if self._get(task_id).status in {Status.CREATED, Status.PLANNING, Status.RUNNING}:
                self.update(task_id, "Interrupted by service restart. Create a new plan to retry.", "error", status=Status.FAILED,
                            error="Execution interrupted by service restart.", current_action="Interrupted")
