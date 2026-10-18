import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import MARKET_HOURS, FakeBroker, crossover_bars

from tradebot.broker import Position
from tradebot.config import validate
from tradebot.engine import Engine
from tradebot.report import build_report
from tradebot.risk import AccountState, RiskManager

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "guard_budget.py"


# ---- enforcement in the engine --------------------------------------------

def test_budget_blocks_entries_when_fully_committed(cfg):
    state = AccountState(0, 0, 0.0, 0, committed_usd=4000)
    ok, why = RiskManager(cfg.risk).can_open(state, "crypto")
    assert not ok and "total budget" in why


def test_position_shrinks_to_fit_remaining_budget(cfg):
    rm = RiskManager(cfg.risk)
    assert rm.position_size(100.0, committed_usd=3500) == 5  # only $500 left
    assert rm.position_size(100.0, committed_usd=3000) == 10  # capped by max_position_usd


def test_position_larger_than_budget_is_invalid(cfg):
    cfg.risk.max_position_usd = 5000
    with pytest.raises(ValueError, match="max_total_budget_usd"):
        validate(cfg)


def test_engine_respects_total_budget(cfg, journal):
    cfg.risk.max_total_budget_usd = 1500
    cfg.stocks.symbols = ["AAA", "BBB"]
    broker = FakeBroker({"AAA": crossover_bars(), "BBB": crossover_bars(), "BTC": crossover_bars()})
    Engine(cfg, broker, journal, clock=lambda: MARKET_HOURS).tick()
    spent = [qty * price for _, _, side, qty, price in broker.orders if side == "BUY"]
    assert len(spent) == 2 and sum(spent) <= 1500  # $995 + a $497.50 position
    assert "Blocked signals: 1" in build_report(journal, MARKET_HOURS.date())


def test_existing_positions_count_against_budget(cfg, journal):
    broker = FakeBroker({"BTC": crossover_bars()})
    broker.pos["AAA"] = Position("AAA", "stocks", 40, 100.0)  # $4000 already committed
    Engine(cfg, broker, journal, clock=lambda: MARKET_HOURS).tick()
    assert all(side != "BUY" for _, _, side, _, _ in broker.orders)


# ---- the agent guard hook ---------------------------------------------------

@pytest.fixture
def project(tmp_path):
    """A copy of the hook and config.yaml in a scratch project."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "guard_budget.py").write_text(HOOK.read_text())
    (tmp_path / "config.yaml").write_text((ROOT / "config.yaml").read_text())
    return tmp_path


def run_hook(project, tool, **tool_input):
    proc = subprocess.run(
        [sys.executable, str(project / ".claude" / "hooks" / "guard_budget.py")],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stderr


def edit_budget(project, value):
    return run_hook(
        project, "Edit", file_path=str(project / "config.yaml"),
        old_string="max_total_budget_usd: 4000", new_string=f"max_total_budget_usd: {value}",
    )


def test_hook_blocks_raising_budget(project):
    code, err = edit_budget(project, 5000)
    assert code == 2 and "not allowed" in err


def test_hook_allows_lowering_budget(project):
    assert edit_budget(project, 3000)[0] == 0


def test_hook_allows_other_config_edits(project):
    code, _ = run_hook(
        project, "Edit", file_path=str(project / "config.yaml"),
        old_string="stop_loss_pct: 1.5", new_string="stop_loss_pct: 1.0",
    )
    assert code == 0


def test_hook_blocks_removing_budget(project):
    text = (project / "config.yaml").read_text().replace("  max_total_budget_usd: 4000\n", "")
    code, err = run_hook(project, "Write", file_path=str(project / "config.yaml"), content=text)
    assert code == 2 and "removing" in err


def test_hook_blocks_duplicate_key_trick(project):
    text = (project / "config.yaml").read_text() + "\nrisk:\n  max_total_budget_usd: 99999\n"
    code, _ = run_hook(project, "Write", file_path=str(project / "config.yaml"), content=text)
    assert code == 2


def test_hook_blocks_higher_budget_in_another_config_file(project):
    code, _ = run_hook(project, "Write", file_path=str(project / "alt.yaml"),
                       content="risk:\n  max_total_budget_usd: 10000\n")
    assert code == 2


@pytest.mark.parametrize("cmd", [
    "sed -i 's/4000/9000/' config.yaml",
    "python -c \"open('config.yaml','a').write('x')\"",
    "echo 'max_total_budget_usd: 9000' >> config.yaml",
    "tradebot run --config other.yaml",
    "rm .claude/hooks/guard_budget.py",
])
def test_hook_blocks_shell_workarounds(project, cmd):
    assert run_hook(project, "Bash", command=cmd)[0] == 2


@pytest.mark.parametrize("cmd", ["tradebot run", "tradebot report", "pytest -q 2>&1", "cat config.yaml"])
def test_hook_allows_normal_commands(project, cmd):
    assert run_hook(project, "Bash", command=cmd)[0] == 0


def test_hook_blocks_editing_the_guard(project):
    code, _ = run_hook(project, "Write", file_path=str(project / ".claude" / "settings.json"),
                       content="{}")
    assert code == 2
