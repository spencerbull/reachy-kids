"""JSON API behind the settings page."""

from typing import Any

from fastapi import FastAPI, HTTPException

from reachy_kids.runner import ConversationRunner
from reachy_kids.providers import PROVIDERS


def register_routes(app: FastAPI, runner: ConversationRunner) -> None:
    """Add the settings/state API to the app's settings server."""

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        return runner.snapshot()

    @app.get("/api/providers")
    def get_providers() -> list[dict[str, Any]]:
        return [
            {"name": p.name, "label": p.label, "voices": list(p.voices), "kids_voice": p.kids_voice}
            for p in PROVIDERS.values()
        ]

    @app.post("/api/settings")
    def post_settings(changes: dict[str, Any]) -> dict[str, Any]:
        try:
            runner.update_settings(changes)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return runner.snapshot()

    @app.post("/api/restart")
    def post_restart() -> dict[str, Any]:
        runner.restart()
        return runner.snapshot()
