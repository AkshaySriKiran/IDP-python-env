from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("omniparse.ops_status")


def _env(*names: str) -> str:
    for name in names:
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    return ""


def _region() -> str:
    return _env("AWS_REGION", "AWS_DEFAULT_REGION") or "eu-west-1"


def _tile(
    *,
    value: str = "—",
    meta: str = "",
    tone: str = "neutral",
    ok: bool = True,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "value": value,
        "meta": meta,
        "tone": tone,
        "ok": ok,
        "detail": detail,
    }


def _alb_dimension_from_arn(arn: str, kind: str) -> str:
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return ""
    resource = parts[5]
    if kind == "loadbalancer" and resource.startswith("loadbalancer/"):
        return resource[len("loadbalancer/") :]
    if kind == "targetgroup" and resource.startswith("targetgroup/"):
        return resource
    return resource


def _cw_avg(
    cw: Any,
    *,
    namespace: str,
    metric: str,
    dimensions: list[dict[str, str]],
    minutes: int = 15,
) -> Optional[float]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    try:
        resp = cw.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "m1",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace,
                            "MetricName": metric,
                            "Dimensions": dimensions,
                        },
                        "Period": 300,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=start,
            EndTime=end,
        )
        results = resp.get("MetricDataResults") or []
        if not results:
            return None
        values = results[0].get("Values") or []
        if not values:
            return None
        return float(values[0])
    except Exception as err:
        logger.debug("CloudWatch %s/%s failed: %s", namespace, metric, err)
        return None


def collect_ops_status() -> dict[str, Any]:
    cluster = _env("ECS_CLUSTER")
    service = _env("ECS_SERVICE")
    audit_bucket = _env("EXTRACT_AUDIT_S3_BUCKET")
    tg_arn = _env("ALB_TARGET_GROUP_ARN")
    lb_arn = _env("ALB_LOAD_BALANCER_ARN")
    region = _region()

    out: dict[str, Any] = {
        "region": region,
        "ecs": _tile(value="—", meta="Not configured", ok=False, detail="Set ECS_CLUSTER and ECS_SERVICE"),
        "cpu": _tile(value="—", meta="—", ok=False, detail="Needs CloudWatch"),
        "memory": _tile(value="—", meta="—", ok=False, detail="Needs CloudWatch"),
        "alb": _tile(value="—", meta="Not configured", ok=False, detail="Set ALB_TARGET_GROUP_ARN"),
        "audit_s3": _tile(
            value="Local" if not audit_bucket else "—",
            meta="JSONL only" if not audit_bucket else "Checking…",
            ok=True if not audit_bucket else False,
            detail="" if not audit_bucket else "Checking bucket",
        ),
    }

    try:
        import boto3
        from botocore.config import Config
    except Exception as err:
        detail = f"boto3 unavailable: {err}"
        for key in ("ecs", "cpu", "memory", "alb"):
            out[key] = _tile(value="—", meta="Unavailable", ok=False, detail=detail)
        if audit_bucket:
            out["audit_s3"] = _tile(value="S3", meta="SDK missing", ok=False, detail=detail)
        return out

    cfg = Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1})

    if audit_bucket:
        try:
            s3 = boto3.client("s3", region_name=region, config=cfg)
            s3.head_bucket(Bucket=audit_bucket)
            out["audit_s3"] = _tile(
                value="S3",
                meta=audit_bucket[:40] + ("…" if len(audit_bucket) > 40 else ""),
                tone="ok",
                ok=True,
                detail="Bucket reachable",
            )
        except Exception as err:
            out["audit_s3"] = _tile(
                value="Error",
                meta="Unreachable",
                tone="bad",
                ok=False,
                detail=str(err)[:180],
            )

    if cluster and service:
        try:
            ecs = boto3.client("ecs", region_name=region, config=cfg)
            desc = ecs.describe_services(cluster=cluster, services=[service])
            services = desc.get("services") or []
            if not services:
                out["ecs"] = _tile(
                    value="Missing",
                    meta=service,
                    tone="bad",
                    ok=False,
                    detail="Service not found",
                )
            else:
                svc = services[0]
                running = int(svc.get("runningCount") or 0)
                desired = int(svc.get("desiredCount") or 0)
                pending = int(svc.get("pendingCount") or 0)
                deployments = svc.get("deployments") or []
                primary = next((d for d in deployments if d.get("status") == "PRIMARY"), None)
                roll = (primary or {}).get("rolloutState") or (primary or {}).get("status") or "—"
                tone = "ok" if running >= desired and desired > 0 else ("warn" if running > 0 else "bad")
                out["ecs"] = _tile(
                    value=f"{running}/{desired}",
                    meta=f"pending {pending} · {roll}",
                    tone=tone,
                    ok=True,
                    detail=f"cluster={cluster}",
                )

                try:
                    cw = boto3.client("cloudwatch", region_name=region, config=cfg)
                    dims = [
                        {"Name": "ClusterName", "Value": cluster},
                        {"Name": "ServiceName", "Value": service},
                    ]
                    cpu = _cw_avg(cw, namespace="AWS/ECS", metric="CPUUtilization", dimensions=dims)
                    mem = _cw_avg(cw, namespace="AWS/ECS", metric="MemoryUtilization", dimensions=dims)
                    if cpu is None:
                        out["cpu"] = _tile(
                            value="—",
                            meta="No datapoints",
                            ok=False,
                            detail="Enable Container Insights / wait for metrics",
                        )
                    else:
                        tone = "ok" if cpu < 70 else ("warn" if cpu < 90 else "bad")
                        out["cpu"] = _tile(
                            value=f"{cpu:.0f}%",
                            meta="15m avg",
                            tone=tone,
                            ok=True,
                        )
                    if mem is None:
                        out["memory"] = _tile(
                            value="—",
                            meta="No datapoints",
                            ok=False,
                            detail="Enable Container Insights / wait for metrics",
                        )
                    else:
                        tone = "ok" if mem < 70 else ("warn" if mem < 90 else "bad")
                        out["memory"] = _tile(
                            value=f"{mem:.0f}%",
                            meta="15m avg",
                            tone=tone,
                            ok=True,
                        )
                except Exception as err:
                    out["cpu"] = _tile(value="—", meta="CW error", ok=False, detail=str(err)[:160])
                    out["memory"] = _tile(value="—", meta="CW error", ok=False, detail=str(err)[:160])
        except Exception as err:
            out["ecs"] = _tile(
                value="—",
                meta="IAM / reachability",
                tone="bad",
                ok=False,
                detail=str(err)[:180],
            )
        else:
            out["ecs"] = _tile(
                value="Local",
                meta="No ECS env",
                ok=True,
                detail="ECS_CLUSTER / ECS_SERVICE unset",
            )

    if tg_arn:
        try:
            elbv2 = boto3.client("elbv2", region_name=region, config=cfg)
            health = elbv2.describe_target_health(TargetGroupArn=tg_arn)
            descs = health.get("TargetHealthDescriptions") or []
            healthy = sum(
                1
                for d in descs
                if str((d.get("TargetHealth") or {}).get("State") or "").lower() == "healthy"
            )
            total = len(descs)
            tone = "ok" if healthy > 0 and healthy == total else ("warn" if healthy > 0 else "bad")
            out["alb"] = _tile(
                value=f"{healthy}/{total}" if total else "0",
                meta="healthy targets",
                tone=tone if total else "warn",
                ok=True,
                detail=tg_arn.split(":")[-1][:48],
            )
        except Exception as err:
            out["alb"] = _tile(
                value="—",
                meta="IAM / reachability",
                tone="bad",
                ok=False,
                detail=str(err)[:180],
            )

    return out
