"""Targeted pytest verifying the noble-1 / noble-l0-worker consumer rename.

Static-but-real assertion: parses the actual source file and confirms
(1) the new group default literal is present, (2) the old default is gone,
(3) the consumername kwarg passes the new worker name. No Redis/numpy needed.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "hermes" / "transport" / "redis_subscriber.py"


def test_consumer_group_default_is_noble_1():
    text = SRC.read_text(encoding="utf-8")
    assert '"noble-1"' in text, "group default 'noble-1' missing"
    assert '"hermes-l0"' not in text, "stale 'hermes-l0' still present"
    assert '"noble-l0-worker"' in text, "consumer name 'noble-l0-worker' missing"


def test_consumername_kwarg_uses_new_worker():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "consumername"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == "noble-l0-worker"
                ):
                    found = True
    assert found, "xreadgroup consumername='noble-l0-worker' not found"


def test_group_default_in_nt_config_get():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    hit = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get":
            args = [a for a in node.args if isinstance(a, ast.Constant)]
            if any(a.value == "consumer_group" for a in args) and any(
                a.value == "noble-1" for a in args
            ):
                hit = True
    assert hit, "nt_config.get('consumer_group', 'noble-1') default not present"
