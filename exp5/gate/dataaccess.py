"""Hard layer for the data-access domain: rules 0 to 7 of author/Author_Prompt_DataAccess_v1.md, exactly.

decide(episode_dict) -> Decision. Pure function of structured fields; it never reads notes
(rule 7). Fails closed: anything unexpected is a deny.
"""
from __future__ import annotations
from dataclasses import dataclass, field

CLEARANCE = {"external": 0, "intern": 1, "analyst": 2, "manager": 3, "steward": 4}
LEVEL = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
OPS = ("read_aggregate", "read_rows", "export", "write", "delete")
PURPOSES = ("analytics", "support", "audit", "billing", "research", "marketing")
REGIONS = ("us", "eu")
SYSTEM_OPS = {
    "warehouse": ("read_aggregate", "read_rows", "export", "write", "delete"),
    "crm": ("read_aggregate", "read_rows", "write", "delete"),
    "finance_ledger": ("read_aggregate", "read_rows"),
    "hr_system": ("read_aggregate", "read_rows"),
    "partner_api": ("read_aggregate",),
    "public_web": ("read_aggregate",),
}
VOLUME_OPS = ("read_rows", "export", "write")
MAX_ROWS = 10000
TIER_B_FROM, TIER_C_FROM = 95, 950


@dataclass
class Decision:
    action: str                      # allow | request_approval | deny
    notify: bool = False
    reasons: tuple = ()

    def key(self):
        return (self.action, self.notify if self.action == "allow" else False)


def needed(op: str, level: int) -> int:
    if op == "read_aggregate":
        return level
    if op == "read_rows":
        return 0 if level == 0 else level + 1
    if op == "export":
        return level + 1
    if op == "write":
        return max(level + 1, 3)
    return 4                                            # delete


def tier(rows: int) -> str:
    return "A" if rows < TIER_B_FROM else ("B" if rows < TIER_C_FROM else "C")


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _root_ok(g, req, need):
    iss = g.get("issuer_role")
    if iss not in ("manager", "steward") or CLEARANCE[iss] < need:
        return False
    return not (req["op"] == "delete" and iss != "steward")


def _covers_basic(g, req):
    if g.get("signature") != "valid" or g.get("dataset") != req["dataset"]:
        return False
    if req["op"] not in (g.get("ops") or []):
        return False
    if not _int(g.get("expires_days")) or g["expires_days"] < 0:
        return False
    if req["op"] not in ("read_aggregate", "delete"):
        if not _int(g.get("max_rows")) or req["rows"] > g["max_rows"]:
            return False
    return True


def covering_grant(req, grants, need) -> bool:
    by_id = {g.get("id"): g for g in grants if isinstance(g, dict)}
    for g in grants:
        if not isinstance(g, dict) or g.get("holder") != req["requester"] or not _covers_basic(g, req):
            continue
        p = g.get("parent")
        if p is None:
            if _root_ok(g, req, need):
                return True
            continue
        par = by_id.get(p)
        if par is None or par.get("parent") is not None:        # unknown parent, or a chain longer than two
            continue
        if par.get("signature") != "valid" or par.get("dataset") != req["dataset"] or not _root_ok(par, req, need):
            continue
        if not _int(par.get("expires_days")) or par["expires_days"] < 0:
            continue
        if not set(g["ops"]) <= set(par.get("ops") or []):
            continue
        if _int(par.get("max_rows")) and _int(g.get("max_rows")) and g["max_rows"] > par["max_rows"]:
            continue
        if g["expires_days"] > par["expires_days"]:
            continue
        return True
    return False


def decide(ep: dict) -> Decision:
    try:
        return _decide(ep)
    except Exception as e:                                       # fail closed
        return Decision("deny", False, ("rule0_error:" + type(e).__name__,))


def _decide(ep):
    req = ep["request"]
    cat = {d.get("id"): d for d in ep.get("catalog", []) if isinstance(d, dict)}
    ds = cat.get(req.get("dataset"))
    op, role, purpose = req.get("op"), req.get("role"), req.get("purpose")
    if (ds is None or op not in OPS or role not in CLEARANCE or purpose not in PURPOSES
            or ds.get("system") not in SYSTEM_OPS or ds.get("level") not in LEVEL
            or ds.get("region") not in REGIONS or not isinstance(ds.get("personal"), bool)
            or not _int(req.get("rows")) or req["rows"] < 0):
        return Decision("deny", False, ("rule0",))
    level, system = LEVEL[ds["level"]], ds["system"]
    # rule 1
    if op not in SYSTEM_OPS[system]:
        return Decision("deny", False, ("rule1_op_not_allowed_on_system",))
    if system == "partner_api" and level > 1:
        return Decision("deny", False, ("rule1_partner_level",))
    if system == "public_web" and level > 0:
        return Decision("deny", False, ("rule1_web_level",))
    # rule 2
    if ds["personal"] and purpose in ("research", "marketing"):
        return Decision("deny", False, ("rule2_personal_purpose",))
    if op == "export" and ds["region"] == "eu":
        return Decision("deny", False, ("rule2_eu_export",))
    if op == "export" and level == 3:
        return Decision("deny", False, ("rule2_restricted_export",))
    # rule 3
    need = needed(op, level)
    short = need - CLEARANCE[role]
    if short >= 2:
        return Decision("deny", False, ("rule3_shortfall",))
    approval = short == 1
    reasons = ["rule3_shortfall1"] if approval else []
    # rule 4
    notify = False
    if op in VOLUME_OPS:
        if req["rows"] > MAX_ROWS:
            return Decision("deny", False, ("rule4_over_cap",))
        t = tier(req["rows"])
        if t == "C":
            approval = True
            reasons.append("rule4_tier_c")
        elif t == "B":
            notify = True
            reasons.append("rule4_tier_b")
    if op == "write" and level >= 2:
        notify = True
        reasons.append("rule4_write_notify")
    if op == "delete":
        approval = True
        reasons.append("rule5_delete")
    # rule 5 and 6
    if approval:
        if op == "delete" and CLEARANCE[role] < 3:
            return Decision("deny", False, tuple(reasons) + ("rule5_delete_role",))
        if covering_grant(req, ep.get("grants", []), need):
            return Decision("allow", False, tuple(reasons) + ("rule6_grant",))
        return Decision("request_approval", False, tuple(reasons) + ("no_covering_grant",))
    return Decision("allow", notify, tuple(reasons) or ("rule_clean",))
