"""Human approval state, independent of Docker and LLMs."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from chaosai.models import PolicyDecision

Choice = Literal["yes", "skip", "all", "quit"]


class ApprovalRequired(RuntimeError):
    pass


def parse_choice(answer: str) -> Choice | None:
    choices: dict[str, Choice] = {"y": "yes", "n": "skip", "a": "all", "q": "quit"}
    return choices.get(answer.strip().lower())


def approval_prompt(decision: PolicyDecision, *, simulation: bool = True) -> str:
    spec = decision.final
    clamps = (
        "; ".join(f"{c.field}: {c.requested} -> {c.final} ({c.reason})" for c in decision.clamps)
        or ("none (fixture policy only)" if simulation else "none")
    )
    return (
        f"\n{'SIMULATION' if simulation else 'LIVE FAULT'} {spec.id}: "
        f"{spec.fault} on {spec.target}, {spec.duration_s}s, "
        f"intensity={spec.intensity}\n"
        f"Hypothesis ({spec.hypothesis.journey}): {spec.hypothesis.statement}; "
        f"error <= {spec.hypothesis.max_error_rate:g}, "
        f"p95 <= {spec.hypothesis.max_p95_multiple:g}x baseline\n"
        f"Abort: error > {spec.abort.max_error_rate:g} or "
        f"p95 > {spec.abort.max_p95_multiple:g}x baseline\n"
        f"Clamps: {clamps}\n[y]es / [n]o skip / [a]ll remaining / [q]uit: "
    )


@dataclass
class Approver:
    yes: bool
    is_tty: bool
    ask: Callable[[str], str]
    all_remaining: bool = False
    simulation: bool = True

    def ensure_interactive(self) -> None:
        if not self.yes and not self.is_tty:
            raise ApprovalRequired("No TTY: refusing experiments without --yes.")

    def decide(self, decision: PolicyDecision) -> Choice:
        self.ensure_interactive()
        if self.yes or self.all_remaining:
            return "yes"
        while True:
            try:
                answer = self.ask(approval_prompt(decision, simulation=self.simulation))
            except (EOFError, KeyboardInterrupt):
                return "quit"
            choice = parse_choice(answer)
            if choice is not None:
                if choice == "all":
                    self.all_remaining = True
                return choice
