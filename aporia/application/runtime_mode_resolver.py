"""
Aporia runtime mode resolver.
Integrates Independent Control Plane, AporiaRuntimeMode, RealPilotEvaluation,
and GuardedPolicies to determine tenant operational mode.
"""

from __future__ import annotations
from typing import Any
from aporia.config import Environment
from aporia.infrastructure.db import Connection
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.application.runtime_mode import AporiaRuntimeMode


class AporiaRuntimeModeResolver:
    """
    Cascading resolver for tenant runtime mode.
    """

    @classmethod
    def for_tenant(cls, environment: Environment, conn: Connection | Any, tenant_id: int) -> str:
        if tenant_id < 1:
            return "disabled"

        db = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        controls = AporiaIndependentControlPlane(db, environment)

        # Immediate fail closed if any runtime-impacting switch is engaged
        if controls.any_engaged(tenant_id, [
            AporiaIndependentControlPlane.GLOBAL,
            AporiaIndependentControlPlane.TENANT,
            AporiaIndependentControlPlane.RUNTIME_INFLUENCE,
        ]):
            return "disabled"

        mode = AporiaRuntimeMode.for_tenant(environment, tenant_id)
        if mode == "disabled":
            return "disabled"

        global_mode = environment.get_string("APORIA_MODE", "guarded_reversible").strip().lower()
        if mode == "advisory" and global_mode == "disabled":
            try:
                from aporia.infrastructure.experiments import AporiaRealPilotEvaluation
                summary = AporiaRealPilotEvaluation(db).summarize(tenant_id)
                return "advisory" if summary.get("status") == "collecting" else "disabled"
            except Exception:
                return "disabled"

        if mode != "guarded_reversible":
            return mode

        try:
            stmt = db.prepare(
                "SELECT status, kill_switch FROM aporia_guarded_policies WHERE tenant_id = ? LIMIT 1"
            )
            stmt.execute([tenant_id])
            policy = stmt.fetch()
            if (
                policy
                and str(policy.get("status", "")) == "active"
                and int(policy.get("kill_switch", 0)) == 0
            ):
                return "guarded_reversible"
            return "disabled"
        except Exception:
            return "disabled"

    @classmethod
    def forTenant(cls, environment: Environment, conn: Connection | Any, tenant_id: int) -> str:
        return cls.for_tenant(environment, conn, tenant_id)
