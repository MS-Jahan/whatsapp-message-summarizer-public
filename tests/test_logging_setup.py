import logging
from app.logging_setup import configure


def test_configure_sets_level_and_one_handler():
    configure("DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    n = len(root.handlers)
    configure("INFO")
    assert len(root.handlers) == n
    assert root.level == logging.INFO
