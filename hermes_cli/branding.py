"""Startup copy shared by the Mithril launcher and the upstream CLI.

Keep this module stdlib-only: dependency recovery runs before activation.
"""

import os


def cli_name() -> str:
    return "mithril" if os.environ.get("MITHRIL_AGENT") == "1" else "hermes"


def product_name() -> str:
    return "Mithril" if os.environ.get("MITHRIL_AGENT") == "1" else "Hermes"
