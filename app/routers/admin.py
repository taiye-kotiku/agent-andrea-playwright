"""
Admin endpoints for cache, catalog, and service management.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Any, List

from app.core.auth import verify_api_secret
from app.models import ServiceDurationRequest
from app.services.catalog import service_catalog, operator_catalog, save_service_catalog, save_operator_catalog
from app.services.cache import invalidate_cache
from app.utils.helpers import normalize_requested_services

router = APIRouter(prefix="/api", tags=["admin"])


@router.post("/invalidate-cache")
async def invalidate_cache_endpoint(
    request: Request,
    _: str = Depends(verify_api_secret)
):
    """Invalidate availability cache for a specific date."""
    body = await request.json()
    date_str = body.get("preferred_date")
    if not date_str:
        raise HTTPException(status_code=400, detail="preferred_date required")

    await invalidate_cache(date_str)
    return {"ok": True, "invalidated": date_str}


@router.post("/get-service-duration")
async def get_service_duration(
    request: Request,
    payload: ServiceDurationRequest,
    _: str = Depends(verify_api_secret)
):
    """Get duration information for services."""
    requested_services = normalize_requested_services(payload.service, payload.services)

    if not requested_services:
        return {
            "success": False,
            "message": "No service provided",
            "services": []
        }

    catalog_services = service_catalog.get("services", {})
    results = []

    for svc in requested_services:
        svc_l = svc.lower().strip()
        matched = None

        # Exact from catalog
        if svc_l in catalog_services:
            matched = catalog_services[svc_l]

        # Fuzzy from catalog
        if matched is None:
            for known_name, info in catalog_services.items():
                if svc_l in known_name or known_name in svc_l:
                    matched = info
                    break

        # Fallback if catalog misses
        from app.services.wegest import SERVICE_DURATION_FALLBACK
        if matched is None:
            fallback_duration = SERVICE_DURATION_FALLBACK.get(svc_l)
            if fallback_duration:
                matched = {
                    "nome": svc,
                    "tempo_operatore": fallback_duration,
                    "tempo_cliente": fallback_duration
                }

        if matched:
            results.append({
                "requested_service": svc,
                "resolved_service": matched.get("nome", svc),
                "tempo_operatore": matched.get("tempo_operatore", 0),
                "tempo_cliente": matched.get("tempo_cliente", 0)
            })
        else:
            results.append({
                "requested_service": svc,
                "resolved_service": None,
                "tempo_operatore": None,
                "tempo_cliente": None
            })

    # Build spoken summaries
    if len(results) == 1:
        r = results[0]
        if r["tempo_operatore"] is not None:
            spoken_summary_it = (
                f"Il servizio {r['resolved_service']} richiede circa "
                f"{r['tempo_operatore']} minuti di lavoro operatore"
            )
            spoken_summary_en = (
                f"The service {r['resolved_service']} requires about "
                f"{r['tempo_operatore']} minutes of operator time"
            )
        else:
            spoken_summary_it = f"Non sono riuscita a trovare la durata del servizio {r['requested_service']}"
            spoken_summary_en = f"I couldn't find the duration for the service {r['requested_service']}"
    else:
        known = [r for r in results if r["tempo_operatore"] is not None]
        total_operator = sum(r["tempo_operatore"] for r in known)
        service_names = ", ".join(r["resolved_service"] or r["requested_service"] for r in results)

        spoken_summary_it = (
            f"I servizi {service_names} richiedono circa {total_operator} minuti totali di lavoro operatore"
        )
        spoken_summary_en = (
            f"The services {service_names} require about {total_operator} total minutes of operator time"
        )

    return {
        "success": True,
        "services": results,
        "spoken_summary_it": spoken_summary_it,
        "spoken_summary_en": spoken_summary_en
    }
