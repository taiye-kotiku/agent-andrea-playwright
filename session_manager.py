"""
Session management for Wegest browser sessions - extracted from main.py.bak
"""

from config import (
    wegest_sessions,
    wegest_sessions_lock,
    MAX_CONCURRENT_SESSIONS,
    SESSION_IDLE_TTL_SECONDS,
    wegest_pool,
    conversation_to_pool_session,
    pool_lock,
    POOL_SIZE,
    WEGEST_USER,
    WEGEST_PASSWORD,
    LOGIN_URL,
    logger,
    DEBUG_SCREENSHOTS,
    screenshots
)
from playwright.async_api import async_playwright
from datetime import datetime, timedelta
from typing import Optional
import asyncio
import base64
import os

# Tracks pool IDs currently being warmed to prevent concurrent double-warm
_pool_warming_in_progress: set[str] = set()


async def snap(page, name: str, force: bool = False):
    from config import DEBUG_SCREENSHOTS, screenshots, html_dumps, logger
    if not DEBUG_SCREENSHOTS and not force:
        return
    try:
        data = await page.screenshot(type="png", full_page=True)
        screenshots[name] = base64.b64encode(data).decode()
        logger.info(f"📸 {name}")
    except Exception as e:
        logger.warning(f"Screenshot failed ({name}): {e}")


async def dump_html(page, name: str, force: bool = False):
    """Save HTML dump for debugging - always saves on error even without DEBUG flag."""
    from config import html_dumps, logger
    try:
        html = await page.content()
        # Truncate to 50KB to avoid storage bloat
        html_dumps[name] = html[:50000]
        logger.info(f"📄 HTML dump: {name}")
    except Exception as e:
        logger.warning(f"HTML dump failed ({name}): {e}")


# Alias for WegestSession and WegestPoolSession from config
from config import WegestSession as WegestSession
from config import WegestPoolSession as WegestPoolSession


async def get_or_create_wegest_session(conversation_id: str) -> 'WegestSession':
    async with wegest_sessions_lock:
        session = wegest_sessions.get(conversation_id)
        if session:
            return session

        active_count = len(wegest_sessions)
        if active_count >= MAX_CONCURRENT_SESSIONS:
            raise Exception(f"Maximum concurrent live sessions reached ({MAX_CONCURRENT_SESSIONS})")

        session = WegestSession()
        wegest_sessions[conversation_id] = session
        logger.info(f"🆕 Created Wegest session for {conversation_id}")
        return session


async def reset_wegest_session(conversation_id: str):
    async with wegest_sessions_lock:
        session = wegest_sessions.get(conversation_id)
        if not session:
            return

    try:
        if session.page:
            await session.page.close()
    except Exception:
        pass
    try:
        if session.context:
            await session.context.close()
    except Exception:
        pass
    try:
        if session.browser:
            await session.browser.close()
    except Exception:
        pass
    try:
        if session.playwright:
            await session.playwright.stop()
    except Exception:
        pass

    async with wegest_sessions_lock:
        wegest_sessions.pop(conversation_id, None)

    logger.info(f"♻️ Wegest session reset for {conversation_id}")


async def is_wegest_session_alive(conversation_id: str) -> bool:
    try:
        session = await get_or_create_wegest_session(conversation_id)
        if not session.page:
            return False
        if session.page.is_closed():
            return False

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

        return (
            not state.get("loginVisible", False)
            and (state.get("hasAgendaButton", False) or state.get("hasMenu", False))
        )
    except Exception:
        return False


async def ensure_wegest_browser(conversation_id: str):
    session = await get_or_create_wegest_session(conversation_id)

    if session.page and not session.page.is_closed():
        return session

    await reset_wegest_session(conversation_id)
    session = await get_or_create_wegest_session(conversation_id)

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-extensions",
            "--single-process",
            "--disable-web-security",
            "--js-flags=--max-old-space-size=128"
        ]
    )
    context = await browser.new_context(
        viewport={"width": 1024, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    session.playwright = p
    session.browser = browser
    session.context = context
    session.page = page
    session.logged_in = False
    session.agenda_open = False
    session.last_used_at = datetime.utcnow()

    logger.info(f"🌐 Wegest browser session created for {conversation_id}")
    return session


async def ensure_wegest_logged_in(conversation_id: str):
    session = await ensure_wegest_browser(conversation_id)

    if await is_wegest_session_alive(conversation_id):
        session.logged_in = True
        session.last_used_at = datetime.utcnow()
        logger.info(f"✅ Reusing existing logged-in session for {conversation_id}")
        return session

    page = session.page

    logger.info(f"🔐 Logging into Wegest session for {conversation_id}...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_selector("input[name='username']", timeout=10000)

    await page.fill("input[name='username']", WEGEST_USER)
    await page.fill("input[name='password']", WEGEST_PASSWORD)
    await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")

    await page.click("div.button")

    try:
        await page.wait_for_function(
            """() => {
                const lp = document.getElementById('pannello_login');
                return lp && getComputedStyle(lp).display === 'none';
            }""",
            timeout=30000
        )
    except Exception:
        pass

    await page.wait_for_timeout(10000)

    login_visible = await page.evaluate("""() => {
        const el = document.getElementById('pannello_login');
        return el ? getComputedStyle(el).display !== 'none' : false;
    }""")

    if login_visible:
        raise Exception("Login failed — panel still visible")

    session.logged_in = True
    session.agenda_open = False
    session.last_used_at = datetime.utcnow()

    logger.info(f"🎉 Wegest session login successful for {conversation_id}")

    await dismiss_system_modals(page, "post-login")

    return session


async def dismiss_system_modals(page, label=""):
    logger.info(f"🔍 Modal sweep: {label}")
    for attempt in range(5):
        modal_visible = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return false;
                const style = window.getComputedStyle(modal);
                return style.display !== 'none' && style.visibility !== 'hidden';
            }
        """)
        if not modal_visible:
            break
        logger.info(f"  ⚠️ System modal (attempt {attempt + 1})")
        clicked = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return null;
                const testo1 = modal.querySelector('.testo1');
                const txt = testo1 ? testo1.textContent.toLowerCase() : '';
                if (txt.includes('cassa') || txt.includes('passaggio')) {
                    const b = modal.querySelector('.button.avviso');
                    if (b) { b.click(); return 'annulla-cassa'; }
                }
                const c = modal.querySelector('.button.conferma');
                if (c && getComputedStyle(c).display !== 'none') { c.click(); return 'conferma'; }
                const x = modal.querySelector('.button.chiudi');
                if (x && getComputedStyle(x).display !== 'none') { x.click(); return 'chiudi'; }
                const a = modal.querySelector('.button.avviso');
                if (a && getComputedStyle(a).display !== 'none') { a.click(); return 'avviso'; }
                modal.style.display = 'none';
                return 'force-hidden';
            }
        """)
        logger.info(f"  → {clicked}")
        await page.wait_for_timeout(1000)
    await page.evaluate("""
        () => {
            document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay, #modale_sfondo, .agenda_modale_sfondo').forEach(el => {
                if (getComputedStyle(el).display !== 'none') el.style.display = 'none';
            });
        }
    """)


async def adaptive_modal_scan(page, label="", auto_dismiss=True):
    """Scan ALL visible modals, log their content, and optionally dismiss them.

    Returns a dict with:
      - modals: list of {selector, text, buttons, type, action_taken}
      - blocking: bool — whether any undismissed modal remains
    """
    result = {"modals": [], "blocking": False}

    modals_info = await page.evaluate("""
        () => {
            const found = [];

            // 1. Standard system dialog
            const sysModal = document.getElementById('modale_dialog');
            if (sysModal && getComputedStyle(sysModal).display !== 'none' && getComputedStyle(sysModal).visibility !== 'hidden') {
                const testo = sysModal.querySelector('.testo1');
                const buttons = [];
                sysModal.querySelectorAll('.button').forEach(b => {
                    const classes = Array.from(b.classList).join('.');
                    const txt = b.textContent.trim();
                    const visible = getComputedStyle(b).display !== 'none';
                    buttons.push({classes, txt, visible});
                });
                found.push({
                    selector: '#modale_dialog',
                    type: 'system_dialog',
                    text: testo ? testo.textContent.trim() : '',
                    buttons: buttons,
                    hasConferma: !!sysModal.querySelector('.button.conferma'),
                    hasChiudi: !!sysModal.querySelector('.button.chiudi'),
                    hasAvviso: !!sysModal.querySelector('.button.avviso'),
                    hasOk: !!sysModal.querySelector('.button.ok'),
                    hasAnnulla: !!sysModal.querySelector('.button.annulla')
                });
            }

            // 2. Phone modal
            const phoneModal = document.querySelector('.modale.card.inserisci_cellulare');
            if (phoneModal && getComputedStyle(phoneModal).display !== 'none') {
                found.push({
                    selector: '.modale.card.inserisci_cellulare',
                    type: 'phone_input',
                    text: phoneModal.textContent.trim().substring(0, 200),
                    buttons: []
                });
            }

            // 3. Customer search modal
            const customerModal = document.querySelector('.cerca_cliente.modale');
            if (customerModal && getComputedStyle(customerModal).display !== 'none') {
                found.push({
                    selector: '.cerca_cliente.modale',
                    type: 'customer_search',
                    text: customerModal.textContent.trim().substring(0, 200),
                    buttons: []
                });
            }

            // 4. Customer form modal (new customer)
            const customerForm = document.querySelector('.form_cliente');
            if (customerForm && getComputedStyle(customerForm).display !== 'none') {
                found.push({
                    selector: '.form_cliente',
                    type: 'customer_form',
                    text: customerForm.textContent.trim().substring(0, 200),
                    buttons: []
                });
            }

            // 5. Modal backdrop (#modale_sfondo) — blocks clicks
            const sfondo = document.getElementById('modale_sfondo');
            if (sfondo && getComputedStyle(sfondo).display !== 'none') {
                found.push({
                    selector: '#modale_sfondo',
                    type: 'modal_backdrop',
                    text: 'Background overlay blocking clicks',
                    buttons: []
                });
            }

            // 6. Generic overlays — only real overlay containers, NOT Wegest admin UI cards
            document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay, .sfondo').forEach(el => {
                if (getComputedStyle(el).display !== 'none') {
                    const sel = el.id ? `#${el.id}` : '.' + Array.from(el.classList).join('.');
                    if (!found.some(f => f.selector === sel)) {
                        found.push({
                            selector: sel,
                            type: 'generic_overlay',
                            text: el.textContent.trim().substring(0, 200),
                            buttons: []
                        });
                    }
                }
            });

            return found;
        }
    """)

    if not modals_info:
        return result

    for modal in modals_info:
        modal_entry = {"selector": modal["selector"], "type": modal["type"],
                       "text": modal["text"][:300], "buttons": modal.get("buttons", []),
                       "action_taken": None}
        logger.warning(f"  🚧 MODAL [{modal['type']}] at {modal['selector']}: \"{modal['text'][:150]}\"")
        if modal.get("buttons"):
            btn_desc = ", ".join([f"{b['txt']}({b['classes']})" for b in modal["buttons"] if b.get("visible")])
            if btn_desc:
                logger.warning(f"    Buttons: {btn_desc}")

        if auto_dismiss:
            action = await _dismiss_specific_modal(page, modal)
            modal_entry["action_taken"] = action
            if action:
                logger.info(f"  ✅ Dismissed {modal['type']} → {action}")
            else:
                logger.warning(f"  ⚠️ Could NOT dismiss {modal['type']} — may be blocking")
                result["blocking"] = True

        result["modals"].append(modal_entry)
        await page.wait_for_timeout(500)

    # Cleanup any remaining overlays including modale_sfondo
    await page.evaluate("""
        () => {
            document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay, .sfondo, #modale_sfondo').forEach(el => {
                if (getComputedStyle(el).display !== 'none') el.style.display = 'none';
            });
        }
    """)

    return result


async def _dismiss_specific_modal(page, modal_info):
    """Attempt to dismiss a specific modal based on its type and content."""
    modal_type = modal_info["type"]
    modal_text = modal_info.get("text", "").lower()
    selector = modal_info["selector"]

    # System dialog — try buttons in priority order
    if modal_type == "system_dialog":
        clicked = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return null;
                const txt = (modal.querySelector('.testo1')?.textContent || '').toLowerCase();

                // Cash/till modals → annulla
                if (txt.includes('cassa') || txt.includes('passaggio')) {
                    const b = modal.querySelector('.button.avviso');
                    if (b && getComputedStyle(b).display !== 'none') { b.click(); return 'annulla-cassa'; }
                }

                // Error modals → OK/avviso/conferma/chiudi
                if (txt.includes('errore') || txt.includes('error') || txt.includes('attenzione') || txt.includes('warning')) {
                    const ok = modal.querySelector('.button.ok');
                    if (ok && getComputedStyle(ok).display !== 'none') { ok.click(); return 'ok'; }
                    const avv = modal.querySelector('.button.avviso');
                    if (avv && getComputedStyle(avv).display !== 'none') { avv.click(); return 'avviso'; }
                }

                // Try standard buttons
                const c = modal.querySelector('.button.conferma');
                if (c && getComputedStyle(c).display !== 'none') { c.click(); return 'conferma'; }
                const x = modal.querySelector('.button.chiudi');
                if (x && getComputedStyle(x).display !== 'none') { x.click(); return 'chiudi'; }
                const a = modal.querySelector('.button.avviso');
                if (a && getComputedStyle(a).display !== 'none') { a.click(); return 'avviso'; }
                const o = modal.querySelector('.button.ok');
                if (o && getComputedStyle(o).display !== 'none') { o.click(); return 'ok'; }
                const an = modal.querySelector('.button.annulla');
                if (an && getComputedStyle(an).display !== 'none') { an.click(); return 'annulla'; }

                // Force hide as last resort
                modal.style.display = 'none';
                return 'force-hidden';
            }
        """)
        return clicked

    # Phone modal — fill and confirm
    if modal_type == "phone_input":
        return await page.evaluate("""
            () => {
                const m = document.querySelector('.modale.card.inserisci_cellulare');
                if (!m) return null;
                const inp = m.querySelector('input[name="cellulare"]');
                const btn = m.querySelector('.button.rimira.primary.conferma') || m.querySelector('.button.conferma');
                if (btn && getComputedStyle(btn).display !== 'none') { btn.click(); return 'confirmed'; }
                if (inp && inp.value) { m.style.display = 'none'; return 'force-hidden (has value)'; }
                m.style.display = 'none';
                return 'force-hidden';
            }
        """)

    # Customer search — just log (handled by booking flow)
    if modal_type == "customer_search":
        return None  # Expected modal, not an error

    # Customer form — just log (handled by booking flow)
    if modal_type == "customer_form":
        return None  # Expected modal

    # Modal backdrop (#modale_sfondo) — force hide, it's just a background
    if modal_type == "modal_backdrop":
        await page.evaluate("""
            () => {
                const el = document.getElementById('modale_sfondo');
                if (el) el.style.display = 'none';
            }
        """)
        return "force-hidden-backdrop"

    # Generic overlay — force hide
    if modal_type == "generic_overlay":
        await page.evaluate(f"""
            () => {{
                const el = document.querySelector('{selector}');
                if (el) el.style.display = 'none';
            }}
        """)
        return "force-hidden-generic"

    return None


async def get_assigned_pool_session(conversation_id: str) -> Optional['WegestPoolSession']:
    async with pool_lock:
        pool_id = conversation_to_pool_session.get(conversation_id)
        if not pool_id:
            return None
        return wegest_pool.get(pool_id)


async def assign_idle_pool_session_to_conversation(conversation_id: str) -> 'WegestPoolSession':
    async with pool_lock:
        # If already assigned, return existing
        existing_pool_id = conversation_to_pool_session.get(conversation_id)
        if existing_pool_id and existing_pool_id in wegest_pool:
            return wegest_pool[existing_pool_id]

        # Find idle session
        for pool_id, session in wegest_pool.items():
            if not session.in_use:
                session.in_use = True
                session.assigned_conversation_id = conversation_id
                session.last_used_at = datetime.utcnow()
                conversation_to_pool_session[conversation_id] = pool_id
                logger.info(f"🔗 Assigned {pool_id} to conversation {conversation_id}")
                return session

    raise Exception("No warm session available in pool")


async def reset_pool_session(pool_id: str):
    async with pool_lock:
        session = wegest_pool.get(pool_id)
        if not session:
            return

    try:
        if session.page:
            await session.page.close()
    except Exception:
        pass
    try:
        if session.context:
            await session.context.close()
    except Exception:
        pass
    try:
        if session.browser:
            await session.browser.close()
    except Exception:
        pass
    try:
        if session.playwright:
            await session.playwright.stop()
    except Exception:
        pass

    async with pool_lock:
        wegest_pool.pop(pool_id, None)

    logger.info(f"♻️ Pool session reset: {pool_id}")


async def create_and_warm_pool_session(pool_id: str):
    WEGEST_USER = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

    session = WegestPoolSession(id=pool_id)

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-extensions",
            "--single-process",
            "--disable-web-security",
            "--js-flags=--max-old-space-size=128"
        ]
    )
    context = await browser.new_context(
        viewport={"width": 1024, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    session.playwright = p
    session.browser = browser
    session.context = context
    session.page = page
    session.last_used_at = datetime.utcnow()

    logger.info(f"🔥 Warming pool session {pool_id}...")

    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_selector("input[name='username']", timeout=10000)

    await page.fill("input[name='username']", WEGEST_USER)
    await page.fill("input[name='password']", WEGEST_PASSWORD)
    await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")

    await page.click("div.button")

    try:
        await page.wait_for_function(
            """() => {
                const lp = document.getElementById('pannello_login');
                return lp && getComputedStyle(lp).display === 'none';
            }""",
            timeout=30000
        )
    except Exception:
        pass

    await page.wait_for_timeout(10000)

    login_visible = await page.evaluate("""() => {
        const el = document.getElementById('pannello_login');
        return el ? getComputedStyle(el).display !== 'none' : false;
    }""")
    if login_visible:
        raise Exception(f"Pool session {pool_id} login failed")

    session.logged_in = True
    logger.info(f"✅ Pool session {pool_id} logged in")

    await dismiss_system_modals(page, "post-login")

    await page.click("[pannello='pannello_agenda']")
    await page.wait_for_timeout(2000)
    await dismiss_system_modals(page, "after-agenda")
    await page.wait_for_timeout(1000)

    session.agenda_open = True
    logger.info(f"📅 Pool session {pool_id} agenda ready")

    async with pool_lock:
        wegest_pool[pool_id] = session


async def warm_pool_on_startup():
    await asyncio.sleep(5)

    for i in range(POOL_SIZE):
        pool_id = f"pool_{i+1}"
        try:
            await create_and_warm_pool_session(pool_id)
        except Exception as e:
            logger.warning(f"Failed to warm {pool_id}: {e}")


async def ensure_pool_healthy():
    while True:
        try:
            async with pool_lock:
                warm_count = 0
                for pool_id, session in wegest_pool.items():
                    if session.page and not session.page.is_closed():
                        warm_count += 1

            if warm_count < POOL_SIZE:
                logger.warning(f"⚠️ Pool under capacity: {warm_count}/{POOL_SIZE}")
                for i in range(1, POOL_SIZE + 1):
                    pool_id = f"pool_{i}"
                    if pool_id not in wegest_pool:
                        try:
                            await create_and_warm_pool_session(pool_id)
                        except Exception as e:
                            logger.error(f"❌ Failed to replenish {pool_id}: {e}")
            else:
                pass
        except Exception as e:
            logger.error(f"❌ Pool health check failed: {e}")

        await asyncio.sleep(60)


async def get_live_session_for_conversation(conversation_id: str):
    session = await get_assigned_pool_session(conversation_id)
    if session:
        return session

    # Fallback: assign one now if available
    return await assign_idle_pool_session_to_conversation(conversation_id)


async def cleanup_idle_wegest_sessions():
    now = datetime.utcnow()
    to_remove = []

    async with wegest_sessions_lock:
        items = list(wegest_sessions.items())

    for conversation_id, session in items:
        if not session.last_used_at:
            continue
        age = (now - session.last_used_at).total_seconds()
        if age > SESSION_IDLE_TTL_SECONDS:
            to_remove.append(conversation_id)

    for conversation_id in to_remove:
        await reset_wegest_session(conversation_id)

    if to_remove:
        logger.info(f"🧹 Cleaned {len(to_remove)} idle Wegest sessions")


async def cleanup_idle_pool_sessions():
    now = datetime.utcnow()
    to_reset = []

    async with pool_lock:
        for pool_id, session in list(wegest_pool.items()):
            if session.last_used_at and (now - session.last_used_at).total_seconds() > SESSION_IDLE_TTL_SECONDS:
                if session.assigned_conversation_id and pool_id not in [s for s in conversation_to_pool_session.values()]:
                    to_reset.append(pool_id)

    for pool_id in to_reset:
        await reset_pool_session(pool_id)

    if to_reset:
        logger.info(f"♻️ Cleaned {len(to_reset)} idle pool sessions")
