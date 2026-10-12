import logging

import uvicorn

from app.api import create_app
from app.config import load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.http_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
