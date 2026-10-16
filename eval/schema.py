"""Evaluation labels. These objects must never enter the tested model context."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Check(StrictModel):
    id: str
    requirement: str
    source: str = "user_request"
    expected: object = None


class ToolStep(StrictModel):
    tool: str
    args: dict = Field(default_factory=dict)


class Turn(StrictModel):
    user: str
    checks: list[Check] = Field(min_length=1)
    allowed_routes: list[str] = Field(default_factory=lambda: ["agent", "workflow", "direct"])
    acceptable_tool_plans: list[list[ToolStep]] = Field(default_factory=list)
    no_tools: bool = False
    expected_doc_ids: list[str] = Field(default_factory=list)


class Fault(StrictModel):
    kind: Literal["invalid_json", "unknown_tool", "wrong_type", "extra_arg", "missing_arg",
                  "non_object", "temporary_error", "persistent_error", "invalid_result",
                  "non_string", "empty_search", "llm_error", "summary_error"]
    tool: str = "get_product_detail"
    times: int = Field(default=1, ge=1)


class Case(StrictModel):
    id: str
    version: str = "agent-v1-draft"
    split: Literal["dev", "acceptance"]
    family: str
    category: Literal["social", "single_product", "context", "multi_tool", "knowledge", "fault"]
    pilot: bool = False
    tags: list[str] = Field(default_factory=list)
    turns: list[Turn] = Field(min_length=1)
    fault: Fault | None = None
    fault_expectation: str | None = None
    minimum_summaries: int = 0
    forbidden_claims: list[str] = Field(default_factory=lambda: ["不得编造未提供的业务事实或声称执行了未接入的操作"])
    annotation_status: Literal["draft_needs_human_review"] = "draft_needs_human_review"


def load_cases(path):
    from eval.common import read_jsonl
    cases = [Case.model_validate(row).model_dump() for row in read_jsonl(path)]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ID")
    families = {}
    for case in cases:
        if case["family"] in families and families[case["family"]] != case["split"]:
            raise ValueError("Scenario family crosses dev/acceptance: " + case["family"])
        families[case["family"]] = case["split"]
    return cases
