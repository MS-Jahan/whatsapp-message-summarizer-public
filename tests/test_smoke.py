import importlib


def test_worker_module_importable_and_has_main():
    mod = importlib.import_module("app.worker")
    assert callable(mod.main)
