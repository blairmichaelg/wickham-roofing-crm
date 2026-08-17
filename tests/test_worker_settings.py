import importlib

from app.workers.settings import WorkerSettings


def test_worker_settings_functions_resolvable():
    for func in WorkerSettings.functions:
        assert callable(func), f"{func} is not callable"
