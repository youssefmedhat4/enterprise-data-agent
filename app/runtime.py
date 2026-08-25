from __future__ import annotations

import asyncio
import os
import selectors
import sys

import uvicorn


def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "8000")),
        loop="none",
    )
    server = uvicorn.Server(config)
    try:
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=_windows_selector_loop) as runner:
                runner.run(server.serve())
            return
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        return


def _windows_selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


if __name__ == "__main__":
    main()
