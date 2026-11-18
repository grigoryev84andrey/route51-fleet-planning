"""Temporary launcher to retain saved scenario references across a server restart."""
import json
from pathlib import Path

import uvicorn

import server

def main() -> None:
    snapshot = Path(__file__).resolve().parent.parent / ".session_scenarios_8051.json"
    server.SESSION["scenarios"] = json.loads(snapshot.read_text(encoding="utf-8-sig"))
    uvicorn.run(server.app, host="127.0.0.1", port=8051, log_level="warning")


if __name__ == "__main__":
    main()
