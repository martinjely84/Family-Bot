"""
Drop-in helper for YOUR trading bot.
------------------------------------
The desktop agent reads a JSON status file to answer /status and /pnl.
Import this helper into your trading bot and call write_status(...) whenever
your state changes (e.g. once per loop / per trade).

Example inside your bot:

    from desktop_agent.status_writer_example import write_status

    write_status(
        status="running",
        pnl=123.45,
        pnl_today=12.30,
        balance=10250.00,
        open_trades=2,
        positions="BTC long 0.5, ETH short 2",
    )

The agent will display whatever fields you pass. Common fields
(status, pnl, pnl_today, balance, positions, open_trades, last_update)
are shown first; any extra keys are shown afterward.
"""

import os
import json
from datetime import datetime

# Must match STATUS_FILE used by the agent.
STATUS_FILE = os.environ.get("STATUS_FILE", "status.json")


def write_status(**fields):
    """Atomically write the current bot state to the status file."""
    fields.setdefault("last_update", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=2, default=str)
    os.replace(tmp, STATUS_FILE)  # atomic on the same filesystem


if __name__ == "__main__":
    # Quick self-test: writes a sample status file you can inspect.
    write_status(
        status="running",
        pnl=123.45,
        pnl_today=12.30,
        balance=10250.00,
        open_trades=2,
        positions="BTC long 0.5",
    )
    print(f"Wrote sample status to {STATUS_FILE}")
