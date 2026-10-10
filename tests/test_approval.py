from collections.abc import Iterator

import pytest

from chaosai.approval import ApprovalRequired, Approver, approval_prompt, parse_choice
from chaosai.fixture_data import fixture
from chaosai.models import Plan
from chaosai.runner import fixture_decision


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("y", "yes"),
        (" N ", "skip"),
        ("a", "all"),
        ("Q", "quit"),
        ("", None),
        ("what", None),
    ],
)
def test_parse_choice(answer: str, expected: str | None) -> None:
    assert parse_choice(answer) == expected


def ask_from(answers: Iterator[str]) -> str:
    return next(answers)


def test_invalid_retry_and_approve_all() -> None:
    prompts: list[str] = []
    answers = iter(["invalid", "a"])

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return ask_from(answers)

    approver = Approver(yes=False, is_tty=True, ask=ask)
    decision = fixture_decision(fixture("plan", Plan).root[0])
    assert approver.decide(decision) == "all"
    assert approver.decide(decision) == "yes"
    assert len(prompts) == 2
    for phrase in ("redis", "stop", "30s", "Hypothesis", "Abort", "Clamps", "[q]uit"):
        assert phrase in approval_prompt(decision)


def test_no_tty_refuses_even_if_input_can_answer() -> None:
    approver = Approver(yes=False, is_tty=False, ask=lambda _: "y")
    with pytest.raises(ApprovalRequired, match="No TTY"):
        approver.decide(fixture_decision(fixture("plan", Plan).root[0]))


def test_yes_never_prompts() -> None:
    def fail(prompt: str) -> str:
        raise AssertionError("Should not prompt")

    approver = Approver(yes=True, is_tty=False, ask=fail)
    assert approver.decide(fixture_decision(fixture("plan", Plan).root[0])) == "yes"


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt])
def test_interrupted_input_quits(exception: type[BaseException]) -> None:
    def ask(prompt: str) -> str:
        raise exception()

    approver = Approver(yes=False, is_tty=True, ask=ask)
    assert approver.decide(fixture_decision(fixture("plan", Plan).root[0])) == "quit"
