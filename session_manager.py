"""
Session management for Wegest browser sessions
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
    logger
)
from playwright.async_api import async_playwright
from datetime import datetime, timedelta
from typing import Optional
import asyncio


async def get_or_create_wegest_session(conversation_id: str) -> 'WegestSession':
    async with wegest_sessions_lock:
        session = wegest_sessions.get(conversation_id)
        if session:
            return session

        active_count = len(wegest_sessions)
        if active_count >= MAX_CONCURRENT_SESSIONS:
            raise Exception(f"Maximum concurrent live sessions reached ({MAX_CONCURRENT_SESSIONS})")

        from config import WegestSession
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

        # Session is valid only if login is NOT visible
        # and the app shell/menu or agenda button exists
        return (
            not state.get("loginVisible", False)
            and (state.get("hasAgendaButton", False) or state.get("hasMenu", False))
        )

    except Exception:
        return False


async def get_assigned_pool_session(conversation_id: str) -> Optional['WegestPoolSession']:
    async with pool_lock:
        pool_id = conversation_to_pool_session.get(conversation_id)
        if not pool_id:
            return None
        from config import WegestPoolSession
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


async def ensure_wegest_browser(conversation_id: str):
    from config import WegestSession
    session = await get_or_create_wegest_session(conversation_id)

    if session.page and not session.page.is_closed():
        return session

    await reset_wegest_session(conversation_id)
    session = await get_or_create_wegest_session(conversation_id)

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
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
    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(5000)

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
            timeout=60000
        )
    except Exception:
        pass

    await page.wait_for_timeout(30000)

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
    await page.wait_for_timeout(2000)

    return session


async def ensure_wegest_agenda_open(conversation_id: str):
    session = await ensure_wegest_logged_in(conversation_id)
    page = session.page

    agenda_visible = await page.evaluate("""() => {
        const a = document.getElementById('pannello_agenda');
        return a ? getComputedStyle(a).display !== 'none' : false;
    }""")

    if agenda_visible:
        session.agenda_open = True
        session.last_used_at = datetime.utcnow()
        logger.info(f"📅 Agenda already open for {conversation_id}")
        from catalog import update_operator_catalog_from_page
        await update_operator_catalog_from_page(page)
        return session

    agenda_button_exists = await page.evaluate("""() => {
        return !!document.querySelector("[pannello='pannello_agenda']");
    }""")

    if not agenda_button_exists:
        logger.warning(f"Agenda button not found for {conversation_id}, resetting session...")
        await reset_wegest_session(conversation_id)
        session = await ensure_wegest_logged_in(conversation_id)
        page = session.page

    logger.info(f"📅 Opening agenda in existing session for {conversation_id}...")
    await page.click("[pannello='pannello_agenda']")
    await page.wait_for_timeout(5000)
    await dismiss_system_modals(page, "after-agenda")
    await page.wait_for_timeout(2000)

    session.agenda_open = True
    session.last_used_at = datetime.utcnow()
    from catalog import update_operator_catalog_from_page
    await update_operator_catalog_from_page(page)

    return session


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
    from config import WegestPoolSession
    session = WegestPoolSession(id=pool_id)

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    session.playwright = p
    session.browser = browser
    session.context = context
    session.page = page
    session.last_used_at = datetime.utcnow()

    logger.info(f"🔥 Warming pool session {pool_id}...")

    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(5000)

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
            timeout=60000
        )
    except Exception:
        pass

    await page.wait_for_timeout(30000)

    login_visible = await page.evaluate("""() => {
        const el = document.getElementById('pannello_login');
        return el ? getComputedStyle(el).display !== 'none' : false;
    }""")
    if login_visible:
        raise Exception(f"Pool session {pool_id} login failed")

    session.logged_in = True
    logger.info(f"✅ Pool session {pool_id} logged in")

    await dismiss_system_modals(page, "post-login")
    await page.wait_for_timeout(2000)

    await page.click("[pannello='pannello_agenda']")
    await page.wait_for_timeout(5000)
    await dismiss_system_modals(page, "after-agenda")
    await page.wait_for_timeout(2000)

    session.agenda_open = True
    logger.info(f"📅 Pool session {pool_id} agenda ready")

    async with pool_lock:
        wegest_pool[pool_id] = session


async def get_live_session_for_conversation(conversation_id: str):
    session = await get_assigned_pool_session(conversation_id)
    if session:
        return session

    # Fallback: assign one now if available
    return await assign_idle_pool_session_to_conversation(conversation_id)


async def warm_pool_on_startup():
    await asyncio.sleep(5)

    for i in range(POOL_SIZE):
        pool_id = f"pool_{i+1}"
        try:
            await create_and_warm_pool_session(pool_id)
        except Exception as e:
            logger.warning(f"Failed to warm {pool_id}: {e}")


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
        await page.wait_for_timeout(2500)
    await page.evaluate("""
        () => document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay').forEach(el => {
            if (getComputedStyle(el).display !== 'none') el.style.display = 'none';
        })
    """)


async def snap(page, name: str, force: bool = False):
    if not config.DEBUG_SCREENSHOTS and not force:
        return
    try:
        data = await page.screenshot(type="png", full_page=True)
        config.screenshots[name] = base64.b64encode(data).decode()
        config.logger.info(f"📸 {name}")
    except Exception as e:
        config.logger.warning(f"Screenshot failed ({name}): {e}")


async def ensure_pool_healthy():
    """
    Background task that ensures the pool always has warm sessions ready.
    Runs continuously and replenishes any missing/died sessions.
    """
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute.

            # Collect sessions that need resetting (without holding lock during reset)
            to_reset = []
            to_create = []

            async with pool_lock:
                # Check which pool slots are missing or unhealthy
                for i in range(POOL_SIZE):
                    pool_id = f"pool_{i+1}"
                    session = wegest_pool.get(pool_id)

                    if not session:
                        to_create.append(pool_id)
                        continue

                    # Quick check: is page alive?
                    if not session.page or session.page.is_closed():
                        to_reset.append(pool_id)
                        continue

                    # Session exists and page is open, verify it's still healthy
                    # (but don't do heavy checks here to avoid holding lock)
                    # We'll do detailed check outside the lock

            # Now reset unhealthy sessions (without holding pool_lock)
            for pool_id in to_reset:
                logger.warning(f"Pool {pool_id} needs reset, recycling...")
                await reset_pool_session(pool_id)
                to_create.append(pool_id)

            # Create new sessions for any missing/reset slots
            for pool_id in to_create:
                try:
                    logger.info(f"🔥 Replenishing pool session: {pool_id}")
                    await create_and_warm_pool_session(pool_id)
                except Exception as e:
                    logger.error(f"Failed to replenish {pool_id}: {e}")

            # Final check: verify all sessions are healthy
            async with pool_lock:
                active_count = sum(
                    1 for s in wegest_pool.values()
                    if s.page and not s.page.is_closed()
                )

            if active_count < POOL_SIZE:
                logger.warning(f"⚠️ Pool still under capacity: {active_count}/{POOL_SIZE}")

        except Exception as e:
            logger.error(f"Pool health monitor error: {e}")
            await asyncio.sleep(60)
