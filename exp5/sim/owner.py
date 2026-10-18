"""Owner stub: the only code that holds the signing key. Also an 'attacker' forge helper."""
from __future__ import annotations
from exp5.gate import Ledger, sign_terms


class OwnerStub:
    def __init__(self, ledger: Ledger, key: bytes, reachable: bool = True):
        self.ledger, self.key, self.reachable = ledger, key, reachable
        self.inbox: list = []                      # B-level notices received

    def approve(self, approval_id: str, terms: dict) -> bool:
        if not self.reachable:
            return False                           # R19: owner unreachable, nothing issued
        self.ledger.issue_approval(self.key, approval_id, terms)
        return True

    def notify(self, day, decision, proposal) -> None:
        self.inbox.append((day, decision.level, decision.landed_cents, proposal))


def forge(ledger: Ledger, approval_id: str, terms: dict, sig: str = "ab" * 32) -> None:
    """What an attacker with DB write access but no key can do: insert an unsigned row."""
    import json
    ledger.db.execute("INSERT INTO approvals(approval_id,terms,sig) VALUES(?,?,?)",
                      (approval_id, json.dumps(terms, sort_keys=True), sig))
