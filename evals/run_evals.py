"""Evaluate the real model's behaviour on `evals/dataset.jsonl` (S1 to S10, G1 to G5).

Four deterministic evaluators: `route_correct`, `tools_expected` (every expected tool was
called), `escalation_expected` (only when the case defines it) and `no_card_leak`. By
default it writes a markdown report to `evals/reports/`; `--langsmith` runs the same
evaluators as a LangSmith experiment. Needs the API keys and an ingested knowledge base.

Usage: `uv run python -m evals.run_evals [--langsmith]`
"""

import argparse
import asyncio
import io
import json
import re
import sys
from datetime import datetime
from typing import Any

from langsmith import Client
from langsmith.evaluation import (
    aevaluate,  # pyright: ignore[reportUnknownVariableType]  # untyped dict parameters
)

from app.chat.schemas import ChatRequest
from app.chat.service import ChatService
from app.container import build_services
from app.core.logging import configure_logging
from app.core.settings import PROJECT_ROOT, get_settings
from app.guardrails.pii import trace_anonymizer

DATASET_FILE = PROJECT_ROOT / "evals" / "dataset.jsonl"
REPORTS_DIR = PROJECT_ROOT / "evals" / "reports"
LANGSMITH_DATASET = "getnet-challenge-scenarios"
CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
CONCURRENCY = 4

type Case = dict[str, Any]
type Outputs = dict[str, Any]


# --- Evaluators: (outputs, reference) -> pass, or None when the case does not define it ---


def route_correct(outputs: Outputs, reference: Case) -> bool | None:
    return outputs["route"] == reference["expected_route"]


def tools_expected(outputs: Outputs, reference: Case) -> bool | None:
    return set(reference["expected_tools"]) <= set(outputs["tools_used"])


def escalation_expected(outputs: Outputs, reference: Case) -> bool | None:
    if reference.get("expected_escalated") is None:
        return None
    return outputs["escalated"] == reference["expected_escalated"]


def no_card_leak(outputs: Outputs, reference: Case) -> bool | None:
    return CARD_NUMBER.search(outputs["answer"]) is None


EVALUATORS = [route_correct, tools_expected, escalation_expected, no_card_leak]


def load_cases() -> list[Case]:
    lines = DATASET_FILE.read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


async def run_case(chat: ChatService, case: Case) -> Outputs:
    request = ChatRequest(message=case["message"], user_id=case["user_id"])
    response = await chat.chat(request, request_id=f"eval-{case['id']}")
    return response.model_dump(mode="json")


# --- Local report ----------------------------------------------------------------------


def score(results: list[tuple[Case, Outputs]], name: str) -> tuple[int, int]:
    evaluator = next(e for e in EVALUATORS if e.__name__ == name)
    verdicts = [evaluator(outputs, case) for case, outputs in results]
    defined = [v for v in verdicts if v is not None]
    return sum(defined), len(defined)


def mark(value: bool | None) -> str:
    return "n/a" if value is None else ("pass" if value else "FAIL")


def render_report(results: list[tuple[Case, Outputs]]) -> str:
    lines = [
        f"# Eval report, {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Evaluator | Score |",
        "|---|---|",
    ]
    for evaluator in EVALUATORS:
        passed, total = score(results, evaluator.__name__)
        lines.append(f"| `{evaluator.__name__}` | {passed}/{total} ({passed / total:.0%}) |")
    lines += [
        "",
        "| Case | Route (expected / actual) | Tools used | Escalated | "
        + " | ".join(e.__name__ for e in EVALUATORS)
        + " | Latency |",
        "|---|---|---|---|" + "---|" * len(EVALUATORS) + "---|",
    ]
    for case, outputs in results:
        verdicts = " | ".join(mark(e(outputs, case)) for e in EVALUATORS)
        lines.append(
            f"| {case['id']} | {case['expected_route']} / {outputs['route']} | "
            f"{', '.join(outputs['tools_used']) or '-'} | {outputs['escalated']} | "
            f"{verdicts} | {outputs['latency_ms']} ms |"
        )
    lines += ["", "## Answers", ""]
    for case, outputs in results:
        answer = outputs["answer"].replace("\n", " ")
        lines.append(f"- **{case['id']}** ({case['message']}): {answer}")
    return "\n".join(lines) + "\n"


async def run_local(chat: ChatService) -> bool:
    cases = load_cases()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def bounded(case: Case) -> tuple[Case, Outputs]:
        async with semaphore:
            return case, await run_case(chat, case)

    results = await asyncio.gather(*(bounded(case) for case in cases))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"eval-{datetime.now():%Y%m%d-%H%M%S}.md"
    report_file.write_text(render_report(results), encoding="utf-8")

    all_pass = True
    print(f"\n{'evaluator':<22} score")
    for evaluator in EVALUATORS:
        passed, total = score(results, evaluator.__name__)
        all_pass &= passed == total
        print(f"{evaluator.__name__:<22} {passed}/{total} ({passed / total:.0%})")
    print(f"\nreport: {report_file.relative_to(PROJECT_ROOT)}")
    return all_pass


# --- LangSmith experiment ----------------------------------------------------------------


def as_langsmith_evaluator(evaluator: Any) -> Any:
    def run(outputs: Outputs, reference_outputs: Case) -> dict[str, Any]:
        verdict = evaluator(outputs, reference_outputs)
        if verdict is None:
            return {"results": []}
        return {"key": evaluator.__name__, "score": verdict}

    run.__name__ = evaluator.__name__
    return run


def ensure_dataset(client: Client) -> None:
    if client.has_dataset(dataset_name=LANGSMITH_DATASET):
        return
    client.create_dataset(LANGSMITH_DATASET, description="Getnet challenge scenarios S1-S10, G1-G5")
    client.create_examples(
        dataset_name=LANGSMITH_DATASET,
        examples=[
            {
                "inputs": {"message": case["message"], "user_id": case["user_id"]},
                "outputs": {
                    key: value for key, value in case.items() if key.startswith("expected")
                },
                "metadata": {"case_id": case["id"]},
            }
            for case in load_cases()
        ],
    )


async def run_langsmith(chat: ChatService) -> bool:
    client = Client(anonymizer=trace_anonymizer())
    ensure_dataset(client)

    async def target(inputs: dict[str, Any]) -> Outputs:
        case = {"id": "langsmith", **inputs}
        return await run_case(chat, case)

    results = await aevaluate(
        target,
        data=LANGSMITH_DATASET,
        evaluators=[as_langsmith_evaluator(e) for e in EVALUATORS],
        experiment_prefix="getnet-agents",
        max_concurrency=CONCURRENCY,
        client=client,
    )
    print(f"\nLangSmith experiment: {results.experiment_name}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scenario evals on the real model.")
    parser.add_argument("--langsmith", action="store_true", help="run as a LangSmith experiment")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging("WARNING")
    chat = build_services(settings).chat
    runner = run_langsmith if args.langsmith else run_local
    return 0 if asyncio.run(runner(chat)) else 1


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
