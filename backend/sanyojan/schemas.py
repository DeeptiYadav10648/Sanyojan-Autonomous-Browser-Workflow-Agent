"""All plans are data, never code; unknown keys and actions are rejected."""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEMO_URL = "https://demo.sanyojan.test/products"
SUPPORTED_FIELDS = {"title", "price", "category", "description"}
ACTIONS = ("navigate", "inspect_title", "extract", "validate", "report")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Status(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {Status.SUCCEEDED, Status.FAILED, Status.CANCELLED}
TRANSITIONS = {
    Status.CREATED: {Status.PLANNING, Status.FAILED, Status.CANCELLED},
    Status.PLANNING: {Status.PLANNED, Status.FAILED, Status.CANCELLED},
    Status.PLANNED: {Status.RUNNING, Status.CANCELLED, Status.FAILED},
    Status.RUNNING: TERMINAL,
}


class PlanInput(StrictModel):
    instruction: str = Field(min_length=10, max_length=2000)
    target_url: str = Field(default=DEMO_URL, min_length=10, max_length=2048)
    task_type: Literal["extraction", "inspection", "report"] = "extraction"
    fields: list[str] | None = Field(default=None, min_length=1, max_length=4)

    @field_validator("instruction")
    @classmethod
    def clean_instruction(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10 or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("Provide a readable instruction of at least 10 characters.")
        return value

    @field_validator("fields")
    @classmethod
    def allowed_fields(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (len(set(value)) != len(value) or not set(value) <= SUPPORTED_FIELDS):
            raise ValueError("Choose unique fields: title, price, category, description.")
        return value


class TaskSpec(StrictModel):
    objective: str = Field(min_length=1, max_length=500)
    target_url: str = Field(max_length=2048)
    task_type: Literal["extraction", "inspection", "report"]
    fields: list[str] = Field(min_length=1, max_length=4)
    constraints: list[str] = Field(max_length=10)

    @field_validator("fields")
    @classmethod
    def allowed_fields(cls, value: list[str]) -> list[str]:
        return PlanInput.allowed_fields(value)


class PlanStep(StrictModel):
    number: int = Field(ge=1, le=5)
    action: Literal["navigate", "inspect_title", "extract", "validate", "report"]
    target: str = Field(min_length=1, max_length=2048)
    expected_result: str = Field(min_length=1, max_length=500)
    validation_rule: str = Field(min_length=1, max_length=500)
    risk: Literal["low"] = "low"
    timeout_ms: int = Field(ge=1000, le=30000)


class Plan(StrictModel):
    mode: Literal["deterministic"] = "deterministic"
    summary: str = Field(max_length=500)
    fields: list[str] = Field(min_length=1, max_length=4)
    warnings: list[str]
    steps: list[PlanStep] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def fixed_read_only_pipeline(self):
        if tuple(s.action for s in self.steps) != ACTIONS or [s.number for s in self.steps] != list(range(1, 6)):
            raise ValueError("Only the ordered, five-step read-only pipeline is permitted.")
        PlanInput.allowed_fields(self.fields)
        return self


class Event(StrictModel):
    id: int
    task_id: str
    timestamp: str
    type: Literal["state", "step", "warning", "error", "log"]
    message: str
    status: Status
    progress: int = Field(ge=0, le=5)


class Record(StrictModel):
    data: dict[str, str | float | None]
    source_url: str
    extracted_at: str
    valid: bool
    warnings: list[str]


class Result(StrictModel):
    records: list[Record]
    extracted_count: int
    valid_count: int
    warnings: list[str]
    failed_steps: list[int]
    duration_seconds: float
    reports: dict[str, str]
    page_title: str
    fields: list[str]


class Task(StrictModel):
    id: str
    instruction: str
    target_url: str
    task_type: Literal["extraction", "inspection", "report"]
    status: Status = Status.CREATED
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    started_at: str | None = None
    finished_at: str | None = None
    plan: Plan | None = None
    progress: int = Field(default=0, ge=0, le=5)
    current_action: str = "Created"
    logs: list[Event] = Field(default_factory=list)
    result: Result | None = None
    error: str | None = None


class Approval(StrictModel):
    approved: Literal[True]

    @field_validator("approved", mode="before")
    @classmethod
    def exact_boolean(cls, value):
        if value is not True:
            raise ValueError("Approval must be the JSON boolean true.")
        return value


class Health(StrictModel):
    status: Literal["ok"] = "ok"
    version: str = "0.1.0"
    planner_mode: Literal["deterministic"] = "deterministic"
    browser_available: bool
