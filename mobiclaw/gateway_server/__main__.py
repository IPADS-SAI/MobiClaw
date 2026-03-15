from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("MOBICLAW_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("MOBICLAW_GATEWAY_PORT", "8090"))
    uvicorn.run("mobiclaw.gateway_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
