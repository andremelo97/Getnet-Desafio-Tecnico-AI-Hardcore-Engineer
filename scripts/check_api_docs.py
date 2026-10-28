"""Fails if any API route is undocumented in the generated OpenAPI schema.

Every operation must have a summary, a description and a JSON response model for its
success status, so Swagger (`/docs`) is always complete.

Usage: `uv run python -m scripts.check_api_docs`
"""

import sys
from typing import Any

from app.main import create_app

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def undocumented(openapi: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path, operations in openapi.get("paths", {}).items():
        for method, operation in operations.items():
            if method not in HTTP_METHODS:
                continue
            name = f"{method.upper()} {path}"
            if not operation.get("summary"):
                problems.append(f"{name}: missing summary")
            if not operation.get("description"):
                problems.append(f"{name}: missing description")
            success = operation.get("responses", {}).get("200", {})
            if not success.get("content", {}).get("application/json", {}).get("schema"):
                problems.append(f"{name}: missing response model")
    return problems


def main() -> int:
    openapi = create_app().openapi()
    operations = sum(method in HTTP_METHODS for ops in openapi["paths"].values() for method in ops)
    problems = undocumented(openapi)
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{operations} operations checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
