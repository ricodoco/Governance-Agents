"""Owner approvals: HMAC-signed, transaction-specific, expiring, single-use.

The proposer never holds the key, so it cannot mint an approval. Text in a
vendor invoice or a spoofed message is not an approval; only a signed record
in the ledger is.
"""
import hashlib
import hmac
import json


def canonical(terms: dict) -> bytes:
    return json.dumps(terms, sort_keys=True, separators=(",", ":")).encode()


def sign_terms(key: bytes, terms: dict) -> str:
    return hmac.new(key, canonical(terms), hashlib.sha256).hexdigest()


def signature_ok(key: bytes, terms: dict, sig: str) -> bool:
    return hmac.compare_digest(sign_terms(key, terms), sig)
