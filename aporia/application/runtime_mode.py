"""
Aporia runtime mode resolution.
"""

from __future__ import annotations
import re
from typing import Sequence
from aporia.config import Environment


class AporiaRuntimeMode:
    """
    Evaluates global, pilot, canary and tenant-specific runtime modes.
    """

    @classmethod
    def for_tenant(cls, environment: Environment, tenant_id: int) -> str:
        global_mode = environment.get_string("APORIA_MODE", "guarded_reversible").strip().lower()
        if global_mode in ("shadow", "advisory", "guarded_reversible"):
            return global_mode

        if tenant_id < 1 or tenant_id not in cls.pilot_tenant_ids(environment):
            return "disabled"

        if not cls.approval_is_valid(environment):
            return "disabled"

        pilot_mode = environment.get_string("APORIA_REAL_PILOT_MODE", "disabled").strip().lower()
        if pilot_mode == "shadow":
            return "shadow"
        if pilot_mode == "canary":
            return "advisory"
        if pilot_mode == "guarded_reversible":
            return "guarded_reversible"
        return "disabled"

    @classmethod
    def forTenant(cls, environment: Environment, tenant_id: int) -> str:
        return cls.for_tenant(environment, tenant_id)

    @classmethod
    def has_active_pilot(cls, environment: Environment) -> bool:
        pilot_mode = environment.get_string("APORIA_REAL_PILOT_MODE", "disabled").strip().lower()
        return (
            len(cls.pilot_tenant_ids(environment)) > 0
            and cls.approval_is_valid(environment)
            and pilot_mode in ("shadow", "canary", "guarded_reversible")
        )

    @classmethod
    def hasActivePilot(cls, environment: Environment) -> bool:
        return cls.has_active_pilot(environment)

    @classmethod
    def pilot_tenant_ids(cls, environment: Environment) -> list[int]:
        ids: list[int] = []
        raw = environment.get_string("APORIA_REAL_PILOT_TENANT_IDS", "")
        for item in raw.split(","):
            val = item.strip()
            if val and val.isdigit() and int(val) > 0:
                ids.append(int(val))
        res = sorted(list(set(ids)))
        return res

    @classmethod
    def pilotTenantIds(cls, environment: Environment) -> list[int]:
        return cls.pilot_tenant_ids(environment)

    @classmethod
    def approval_is_valid(cls, environment: Environment) -> bool:
        app_env = environment.app_environment()
        commitment = environment.get_string("APORIA_REAL_PILOT_APPROVAL_COMMITMENT", "").strip().lower()
        return app_env == "staging" and bool(re.match(r"^[0-9a-f]{64}$", commitment))

    @classmethod
    def approvalIsValid(cls, environment: Environment) -> bool:
        return cls.approval_is_valid(environment)
