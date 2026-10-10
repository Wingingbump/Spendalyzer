from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.dependencies import get_current_user
from backend.limiter import limiter
from core.db import get_last_synced_at
from core import insights as ins

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("")
@limiter.limit("5/minute")
def sync(
    request: Request,
    current_user: dict = Depends(get_current_user),
    full_sync: bool = Query(False),
):
    try:
        import services.pull as pull_service
        # main() returns the number of transactions it inserted.
        synced_count = pull_service.main(current_user["id"], full_sync=full_sync) or 0
        last_synced_at = get_last_synced_at(current_user["id"])

        # Drop the cached DataFrame BEFORE running analysis — run_analysis()
        # calls load_data(), so leaving the stale frame in place would have every
        # detector reason about the data as it stood before this sync.
        ins.invalidate_user_cache(current_user["id"])

        # Run proactive analysis pipeline after every successful sync
        try:
            from core.analysis import run_analysis
            run_analysis(current_user["id"])
        except Exception:
            pass  # analysis failure must never break sync

        return {
            "synced_count": synced_count,
            "last_synced_at": str(last_synced_at) if last_synced_at else None,
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Sync failed. Please try again.")
