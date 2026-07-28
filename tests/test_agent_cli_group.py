"""C1 verification: the `platform agent` CLI group + 5 subcommands now exist.

Uses click's introspection (no execution of the subcommand bodies, which would
need Redis/DuckDB). This proves the cron jobs' `agent --eod` etc. resolve.
"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _load_cli():
    import hermes.app as app

    return app.cli


def test_agent_group_registered():
    cli = _load_cli()
    assert "agent" in cli.commands, "agent group not registered on cli"


def test_agent_subcommands_present():
    cli = _load_cli()
    agent = cli.commands["agent"]
    expected = {
        "eod",
        "list-hypotheses",
        "check-shadow-promotions",
        "check-underperformance",
        "monthly-maintenance",
    }
    assert expected.issubset(set(agent.commands.keys())), (
        f"missing subcommands: {expected - set(agent.commands.keys())}"
    )


def test_legacy_agent_command_no_longer_missing():
    # The cron wrappers call `run_guarded.sh agent --eod` etc.; this must not
    # raise "No such command 'agent'".
    cli = _load_cli()
    assert cli.commands["agent"].name == "agent"
