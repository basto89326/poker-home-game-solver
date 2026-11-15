"""Start the app: ``python -m src.api``, then open the printed address."""

import logging

from ..config import load_config
from ..engine.equity import load_preflop_equity
from .app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()
    load_preflop_equity()  # build the cached table up front rather than on the first solve
    print(f"Home Game Solver running at http://{config.host}:{config.port}")
    create_app(config).run(host=config.host, port=config.port, debug=False)


if __name__ == "__main__":
    main()
