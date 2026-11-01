"""Unit tests for semantic judges (anthropic client mocked)."""

from types import SimpleNamespace
from unittest import mock

import pytest

from backend.app.services.arbitration_engine import ArbitrationArbiter
from backend.app.services.llm_judge import (
    ClaudeJudge,
    HeuristicJudge,
    JudgeResult,
    SemanticJudge,
    get_semantic_judge,
)

CODE = 'def f(x: int) -> int:\n    """Doc."""\n    return x\n'


def _client(text=None, exc=None):
    client = mock.Mock()
    if exc:
        client.messages.create.side_effect = exc
    else:
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)]
        )
    return client


def test_heuristic_matches_rubric():
    assert HeuristicJudge().judge(CODE).score == ArbitrationArbiter.compute_semantic_rubric(CODE)
    assert isinstance(HeuristicJudge(), SemanticJudge)


def test_claude_parses_json_and_sends_model():
    c = _client('{"score": 0.8, "rationale": "Clear."}')
    r = ClaudeJudge(model="m-1", client=c).judge(CODE)
    assert r == JudgeResult(0.8, "Clear.")
    assert c.messages.create.call_args.kwargs["model"] == "m-1"


def test_claude_handles_code_fences():
    r = ClaudeJudge(client=_client('```json\n{"score": 1, "rationale": "ok"}\n```')).judge(CODE)
    assert r.score == 1.0


@pytest.mark.parametrize("raw,expected", [(1.7, 1.0), (-3, 0.0)])
def test_claude_clamps(raw, expected):
    r = ClaudeJudge(client=_client('{"score": %s, "rationale": "x"}' % raw)).judge(CODE)
    assert r.score == expected


@pytest.mark.parametrize(
    "text",
    ["not json", '{"score": "high"}', '{"rationale": "no score"}', '{"score": true}', "[1]", ""],
)
def test_claude_malformed_falls_back(text):
    r = ClaudeJudge(client=_client(text)).judge(CODE)
    assert r.score == ArbitrationArbiter.compute_semantic_rubric(CODE)
    assert r.rationale is None


def test_claude_exception_and_timeout_fall_back():
    for exc in (RuntimeError("boom"), TimeoutError("slow")):
        r = ClaudeJudge(client=_client(exc=exc)).judge(CODE)
        assert r.score == HeuristicJudge().judge(CODE).score


def test_selection():
    on = SimpleNamespace(LLM_JUDGE_ENABLED=True, ANTHROPIC_API_KEY="k", LLM_JUDGE_MODEL="m")
    assert isinstance(get_semantic_judge(on), ClaudeJudge)
    assert isinstance(get_semantic_judge(SimpleNamespace(LLM_JUDGE_ENABLED=True, ANTHROPIC_API_KEY="")), HeuristicJudge)
    assert isinstance(get_semantic_judge(SimpleNamespace(LLM_JUDGE_ENABLED=False, ANTHROPIC_API_KEY="k")), HeuristicJudge)


def test_arbiter_uses_judge_and_reports_rationale():
    class Fixed:
        def judge(self, src):
            return JudgeResult(0.4, "meh")

    arb = ArbitrationArbiter(judge=Fixed(), runner=SimpleNamespace(run_tests=lambda **k: (1, 1, "ok")))
    rep = arb.evaluate("a1", CODE, "t")
    assert rep.s_semantic == 0.4
    assert "meh" in rep.stdout


def test_arbiter_default_judge_unchanged_and_override_wins():
    arb = ArbitrationArbiter(runner=SimpleNamespace(run_tests=lambda **k: (1, 1, "ok")))
    assert isinstance(arb.judge, HeuristicJudge)
    rep = arb.evaluate("a1", CODE, "t")
    assert rep.s_semantic == ArbitrationArbiter.compute_semantic_rubric(CODE)
    assert "rationale" not in rep.stdout
    assert arb.evaluate("a1", CODE, "t", semantic_score=0.1).s_semantic == 0.1
