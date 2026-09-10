"""AI propose: instruction -> applied edit ops via a registered model.

The server never talks to a model vendor (see :mod:`ai.adapters`). A
deployment registers :data:`~ai.runner.ModelFn` callables in
``MODEL_REGISTRY`` under a name; tests register scripted models. A proposal
runs through the same :class:`~ai.runner.AgentRunner` + tool surface as any
agent, so its edits land attributable (``agent=ai-propose:<model>``) and
reviewable via ``/ai/review`` — no second edit path.
"""

from __future__ import annotations

from typing import Any

from .runner import AgentRunner, ModelFn
from .tools import ToolContext

#: deployment-registered model callables, by name ("default" first)
MODEL_REGISTRY: dict[str, ModelFn] = {}

#: proposal-scoped budgets: proposals are smaller than free-form agent runs
DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_OPS = 60


def register_model(name: str, model: ModelFn) -> None:
    """Register (or replace) a model callable under *name*."""
    MODEL_REGISTRY[name] = model


def propose(
    ctx: ToolContext,
    doc_id: str,
    instruction: str,
    model_name: str = "default",
    max_steps: int = DEFAULT_MAX_STEPS,
    max_ops: int = DEFAULT_MAX_OPS,
) -> dict[str, Any]:
    """Run one proposal. Returns the AgentReport dict; never raises for
    model/tool failures — those surface in ``report.stopped_reason`` and the
    transcript. Unknown model names are a typed error, not a guess."""
    model = MODEL_REGISTRY.get(model_name)
    if model is None:
        return {
            "ok": False,
            "error": "model-not-registered",
            "status": 503,
            "model": model_name,
            "registered": sorted(MODEL_REGISTRY),
        }
    runner = AgentRunner(model, max_steps=max_steps, max_ops=max_ops)
    report = runner.run(ctx, doc_id, f"agent=ai-propose:{model_name}", instruction)
    return {"ok": True, "error": None, "model": model_name, "report": report.to_dict()}
