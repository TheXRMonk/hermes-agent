"""Resuming restores persisted estimates without replaying accounting deltas."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cli import HermesCLI
from hermes_state import SessionDB
from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin


def resume_cli(db, session_id):
    cli = HermesCLI.__new__(HermesCLI)
    cli._session_db = db
    cli.session_id = session_id
    cli.session_start = datetime.now()
    cli.model = "test"
    cli.agent = None
    cli._resumed = True
    cli.conversation_history = []
    cli._console_print = lambda *args, **kwargs: None
    return cli


def test_preload_displays_cached_cost_before_lazy_agent(tmp_path, monkeypatch):
    import cli as cli_module
    monkeypatch.setattr(cli_module, "CLI_CONFIG", {"display": {"show_cost": True}})
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("startup", source="cli")
        db.append_message("startup", "user", "hello")
        db.update_token_counts("startup", estimated_cost_usd=1.25,
                               cost_status="estimated", cost_source="snapshot")
        cli = resume_cli(db, "startup")
        with patch.object(cli, "_restore_session_state"):
            assert cli._preload_resumed_session()
        assert cli.agent is None
        # Trace real SQLite reads: repaint may use other cached UI state, not cost queries.
        statements = []
        with db._read_ctx() as conn:
            conn.set_trace_callback(statements.append)
            try:
                for _ in range(3):
                    assert cli._get_status_bar_snapshot()["cost_label"] == "~$1.2500"
            finally:
                conn.set_trace_callback(None)
        assert not any("sessions" in sql.lower() for sql in statements)



def test_new_clears_preloaded_cost(tmp_path, monkeypatch):
    import cli as cli_module
    monkeypatch.setattr(cli_module, "CLI_CONFIG", {"agent": {}, "display": {"show_cost": True}})
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("old", source="cli")
        db.update_token_counts("old", estimated_cost_usd=1.0, cost_status="estimated")
        cli = resume_cli(db, "old")
        cli._restore_session_cost()
        with patch("hermes_cli.cli_session_mixin._reset_model_to_config_default"):
            cli.new_session(silent=True)
        assert getattr(cli, "_restored_session_cost", None) is None
        assert "cost_label" not in cli._get_status_bar_snapshot()


def test_slash_resume_restores_after_reset(tmp_path, monkeypatch):
    import cli as cli_module
    monkeypatch.setattr(cli_module, "CLI_CONFIG", {"display": {"show_cost": True}})
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("old", source="cli")
        db.create_session("target", source="cli")
        db.update_token_counts("target", estimated_cost_usd=2.5, cost_status="estimated")
        cli = resume_cli(db, "old")
        cli.agent = SimpleNamespace(session_estimated_cost_usd=99, session_cost_status="estimated")
        def reset():
            cli.agent.session_estimated_cost_usd = 0
            cli.agent.session_cost_status = "unknown"
        cli.agent.reset_session_state = reset
        with patch("hermes_cli.main._resolve_session_by_name_or_id", return_value="target"), \
             patch.object(cli, "_restore_session_cwd"), \
             patch.object(cli, "_restore_session_yolo"), \
             patch.object(cli, "_restore_session_model"):
            cli._handle_resume_command("/resume target")
        assert cli._get_status_bar_snapshot()["cost_label"] == "~$2.5000"
        cli.agent = None
        assert cli._get_status_bar_snapshot()["cost_label"] == "~$2.5000"


def test_resume_recovers_nested_delegates_without_forks_or_double_counting(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        def row(sid, amount, parent=None, config=None, end=None):
            db.create_session(sid, source="cli", parent_session_id=parent, model_config=config)
            db.update_token_counts(sid, estimated_cost_usd=amount,
                                   cost_status="estimated", cost_source="snapshot")
            if end:
                db.end_session(sid, end)
        row("parent", 1.0, end="compression")
        row("child", 2.0, "parent", {"_delegate_from": "parent"}, "compression")
        row("child-tip", 3.0, "child", {"_delegate_from": "parent"}, "agent_close")
        row("nested", 4.0, "child-tip", {"_delegate_from": "child-tip"}, "agent_close")
        row("fork", 100.0, "child-tip",
            {"_delegate_from": "parent", "_branched_from": "child-tip"})
        row("unrelated", 200.0)
        row("unmarked-child", 300.0, "child-tip")
        row("tip", 0.5, "parent")
        cli = resume_cli(db, "tip")
        cli._restore_session_cost()
        assert cli._restored_session_cost.session_estimated_cost_usd == pytest.approx(10.5)
        cli._restore_session_cost()
        assert cli._restored_session_cost.session_estimated_cost_usd == pytest.approx(10.5)
        assert db.get_session("parent")["estimated_cost_usd"] == 1.0


@pytest.mark.parametrize("usage", [False, True])
def test_unused_compression_tip_does_not_hide_known_cost(tmp_path, usage):
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("root", source="cli")
        db.update_token_counts("root", estimated_cost_usd=1.25,
                               cost_status="estimated", cost_source="snapshot")
        db.end_session("root", "compression")
        db.create_session("tip", source="cli", parent_session_id="root")
        if usage:
            db.update_token_counts("tip", input_tokens=10)
        cli = resume_cli(db, "tip")
        cli._restore_session_cost()
        baseline = cli._restored_session_cost
        assert baseline.session_estimated_cost_usd == pytest.approx(1.25)
        assert baseline.session_cost_status == ("unknown" if usage else "estimated")
        if not usage:
            assert baseline.session_cost_source == "snapshot"


def test_agent_initialization_restores_cost_and_new_usage_is_not_doubled(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("resume-cost", source="cli", model="test")
        db.update_token_counts("resume-cost", estimated_cost_usd=1.25,
                               cost_status="estimated", cost_source="snapshot")
        cli = MagicMock()
        cli.agent = None
        cli._session_db = db
        cli.session_id = "resume-cost"
        cli._resumed = True
        cli.conversation_history = [{"role": "user", "content": "hello"}]
        cli._pending_title = None
        cli._auto_load_skills_result = None
        cli._single_query_mode = False
        cli._restore_session_cost = lambda: CLIAgentSetupMixin._restore_session_cost(cli)

        def new_agent(**kwargs):
            return SimpleNamespace(session_estimated_cost_usd=0.0,
                                   session_cost_status="unknown", session_cost_source="none")

        with patch("run_agent.AIAgent", side_effect=new_agent), \
             patch("cli._prepare_deferred_agent_startup"), \
             patch("hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build"), \
             patch("agent.credits_tracker.seed_credits_at_session_start"):
            assert CLIAgentSetupMixin._init_agent(cli, runtime_override={"provider": "test"})
            assert cli.agent.session_estimated_cost_usd == pytest.approx(1.25)
            assert cli.agent.session_cost_status == "estimated"
            assert cli.agent.session_cost_source == "snapshot"
            # The initialized-agent fast path must not add the baseline again.
            assert CLIAgentSetupMixin._init_agent(cli)
            assert cli.agent.session_estimated_cost_usd == pytest.approx(1.25)

        # The existing accounting path persists deltas, not the restored total.
        cli.agent.session_estimated_cost_usd += 0.5
        db.update_token_counts("resume-cost", estimated_cost_usd=0.5)
        assert db.get_session("resume-cost")["estimated_cost_usd"] == pytest.approx(
            cli.agent.session_estimated_cost_usd)
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["compression", "fork", "unknown", "included", "missing"])
def test_restore_respects_lineage_and_cost_provenance(tmp_path, kind):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("root", source="cli")
        db.update_token_counts("root", estimated_cost_usd=1.25,
                               cost_status="estimated", cost_source="snapshot")
        db.end_session("root", "compression")
        db.create_session("tip", source="cli", parent_session_id="root",
                          model_config={"_branched_from": "root"} if kind == "fork" else None)
        db.update_token_counts("tip", estimated_cost_usd=0.5,
                               cost_status="estimated", cost_source="snapshot")
        expected, status = (1.75 if kind == "compression" else 0.5), "estimated"
        if kind in {"unknown", "included"}:
            db.create_session(kind, source="cli")
            if kind == "included":
                db.update_token_counts(kind, estimated_cost_usd=0, cost_status="included")
            expected, status = 0, kind
        elif kind == "missing":
            expected, status = 0, "unknown"
        cli = SimpleNamespace(_session_db=db,
                              session_id="tip" if kind in {"compression", "fork"} else kind,
                              agent=SimpleNamespace(session_estimated_cost_usd=0,
                                                    session_cost_status="unknown"))
        CLIAgentSetupMixin._restore_session_cost(cli)
        CLIAgentSetupMixin._restore_session_cost(cli)
        assert cli.agent.session_estimated_cost_usd == pytest.approx(expected)
        assert cli.agent.session_cost_status == status
        assert db.get_session("root")["estimated_cost_usd"] == pytest.approx(1.25)
        assert db.get_session("tip")["estimated_cost_usd"] == pytest.approx(0.5)
    finally:
        db.close()
