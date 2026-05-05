import os
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("config")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

BOOKING_TASK = """
You are on the WeGest salon management website. Complete these steps:

STEP 1 - LOGIN:
Go to https://www.i-salon.eu/login/default.asp?login=
Enter username: {username}
Enter password: {password}
Enter codice: 1
Click the login button.
Wait for the page to load.

STEP 2 - AGENDA:
Click on "Agenda" in the left sidebar menu.
Wait for the agenda timetable to load.

STEP 3 - SELECT DATE:
Find the date {date} on the calendar at the top of the agenda.
Click on it to select it.

STEP 4 - SELECT TIME SLOT:
The agenda shows operators (columns) and time slots (rows).
Click the time slot at {time} for the first available operator.
A modal/popup should appear to search for a customer.

STEP 5 - SELECT CUSTOMER:
In the customer search modal, type "{customer_name}" in the search field.
Wait for results to appear.
Click on the customer in the results list to select them.

STEP 6 - SELECT SERVICES:
In the appointment form that appears, select these services: {services}.
Click on each service to add it.

STEP 7 - CONFIRM:
Click the "Aggiungi appuntamento" or confirm button to finalize the booking.

Important notes:
- Time slots show 12-hour time like "2:00 PM" not "14:00"
- Days of the week and labels may be in Italian
- If the customer is not found, click "Nuovo Cliente" to create a new one
- The customer search modal opens inside the appointment form
"""


async def run_booking(booking_data: dict) -> dict:
    """Run a full WeGest booking using browser-use AI agent."""
    from browser_use import Agent, Browser, BrowserConfig
    from browser_use.controller.service import Controller
    from langchain_openai import ChatOpenAI
    from config import WEGEST_USER, WEGEST_PASSWORD

    llm = ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key=DEEPSEEK_API_KEY,
        temperature=0.1,
    )

    browser = Browser(
        config=BrowserConfig(
            headless=True,
            extra_chromium_args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
    )

    controller = Controller()

    try:
        task = BOOKING_TASK.format(
            customer_name=booking_data.get("customer_name", ""),
            date=booking_data.get("date", ""),
            time=booking_data.get("time", ""),
            services=", ".join(booking_data.get("services", [])),
            username=WEGEST_USER,
            password=WEGEST_PASSWORD,
        )

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            controller=controller,
            use_vision=True,
            max_failures=3,
            max_actions_per_step=1,
        )

        history = await agent.run(max_steps=30)

        result = {
            "success": True,
            "steps": len(history),
            "final_result": history.final_result() if hasattr(history, 'final_result') else None,
            "errors": [],
        }

        return result

    except Exception as e:
        logger.error(f"❌ AI booking failed: {e}")
        return {"success": False, "error": str(e)}

    finally:
        try:
            await browser.close()
        except:
            pass
