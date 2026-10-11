#!/usr/bin/env python3
"""Serve the LOCAL stub gateway for k6/manual runs (127.0.0.1 only).

TEST-ONLY fixture JWKS (same HS256 fixture key as the unit suite) so the
load script can authenticate two deterministic principals. Never used for
live traffic; production config rejects fixture tokens (see FixtureGuard).
Usage: PYTHONPATH=app/src:app/tests ./.venv/bin/python scripts/serve_stub.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "tests"))

import uvicorn
from gateway.admission import AtomicAdmission, LocalRows
from gateway.web import create_app
from helpers import make_gateway


def main() -> None:
    gw = make_gateway()
    gw.admission = AtomicAdmission(LocalRows(), gw.config)
    gw.ledger = gw.admission
    uvicorn.run(create_app(gw), host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
