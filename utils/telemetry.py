"""
Multi-Tenant Rollout Guardrails & Observability Telemetry.
Tracks metrics in memory and logs critical security/isolation assertions.
"""

from typing import Dict
from config import logger

METRICS: Dict[str, int] = {
    "auth_denials": 0,
    "cross_tenant_leak_checks": 0,
    "chat_migrations": 0,
    "migrations_applied": 0,
    "scheduler_digest_dispatches": 0,
}


def increment_metric(metric_name: str, by: int = 1):
    """Increments a telemetry metric counter."""
    if metric_name in METRICS:
        METRICS[metric_name] += by
    else:
        METRICS[metric_name] = by


def get_metrics() -> Dict[str, int]:
    """Returns a snapshot of current telemetry metrics."""
    return dict(METRICS)


def reset_metrics():
    """Resets all metrics to zero (useful for test isolation)."""
    for k in METRICS:
        METRICS[k] = 0
