"""
Session management endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Any

from app.core.auth import verify_api_secret
from app.models import PrepareLiveSessionRequest
from app.services.wegest import assign_idle_pool_session_to_conversation, logger
from app.services.call_state import update_call_state
from datetime import datetime

router = APIRouter(prefix="/api", tags=["session"])


@router.post("/prepare-live-session")
async def prepare_live_session(
    request: Request,
    payload: PrepareLiveSessionRequest,
    _: str = Depends(verify_api_secret)
):
    """Prepare a live Wegest session for a conversation."""
    if not payload.conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id required")

    try:
        session = await assign_idle_pool_session_to_conversation(payload.conversation_id)

        async with session.lock:
            # Verify session is still alive
            try:
                if not session.page or session.page.is_closed():
                    raise Exception("Pool session page is closed")

                state = await session.page.evaluate("""() => {
                    const loginPanel = document.getElementById('pannello_login');
                    const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
                    const menu = document.getElementById('menu');

                    return {
                        loginVisible: loginPanel ? getComputedStyle(loginPanel).display !== 'none' : false,
                        hasAgendaButton: !!agendaBtn,
                        hasMenu: !!menu
                    };
                }""")

                if state.get("loginVisible", False) or not (state.get("hasAgendaButton", False) or state.get("hasMenu", False)):
                    raise Exception("Pool session is no longer ready")

                session.last_used_at = datetime.utcnow()

                await update_call_state(payload.conversation_id, {
                    "session_prepared": True
                })

                return {
                    "success": True,
                    "conversation_id": payload.conversation_id,
                    "session_ready": True,
                    "message": "Live Wegest session is ready"
                }

            except Exception as session_err:
                logger.warning(f"Assigned pool session failed health check: {session_err}")
                raise session_err

    except Exception as e:
        logger.error(f"❌ Session warm-up failed for {payload.conversation_id}: {e}")
        return {
            "success": False,
            "conversation_id": payload.conversation_id,
            "session_ready": False,
            "message": f"No live session available right now: {e}"
        }
