import os
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("config")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

BOOKING_TASK = """
You are an AI booking assistant for WeGest, an Italian salon management web application.
You control a browser to complete bookings. You MUST use vision (screenshots) to understand the page.

## CONTEXT
- URL: https://www.i-salon.eu/login/default.asp?login=
- The UI is in Italian with some English labels
- The page is a Single Page Application (SPA) - panels switch without page reload
- Time slots use 12-hour format (e.g. "2:00 PM" not "14:00")
- Staff columns are labeled with operator names at the top
- A colored indicator shows the current time on the agenda

## YOUR TASK
Complete a booking for:
- Customer: {customer_name}
- Date: {date} (format: day/month/year, e.g. 6/5/2026)
- Time: {time} (e.g. "10:00" or "2:00 PM")
- Services: {services}

## STEP-BY-STEP INSTRUCTIONS

### STEP 1: LOGIN
1. Navigate to https://www.i-salon.eu/login/default.asp?login=
2. Find the login form with username/password fields
3. Enter username: {username}
4. Enter password: {password}
5. Enter codice: 1 (there is an input field named "codice")
6. Click the login button (it has class "button" and is a div)
7. Wait for the page to fully load after login
8. If a system modal appears (like "annulla-cassa"), dismiss it by clicking the cancel button

### STEP 2: OPEN AGENDA
1. Look for the left sidebar menu with id "menu"
2. Find and click the item labeled "Agenda" (it has attribute pannello="pannello_agenda")
3. Wait for the agenda timetable to load
4. The agenda shows a grid with operator columns and time rows
5. If any modal/popup appears, close it

### STEP 3: SELECT DATE
1. Look at the top of the agenda for the date selector/calendar
2. Find the date {date} (day {day}, month {month}, year {year})
3. Date elements have class "data" with attributes giorno, mese, anno
4. Click on the correct date element
5. Wait for the time grid to load (cells with class "cella" will appear)
6. VERIFY: After clicking, you should see the time grid for that date

### STEP 4: CLICK TIME SLOT
1. Look at the agenda grid carefully
2. Columns represent different operators/staff members
3. Rows represent time slots (each row is ~30min, grouped by hour)
4. Find the time slot for {time} - the cell will contain text like "10:00" or "10:00 AM" or "2:00 PM"
5. IMPORTANT: Click directly on the time text or the cell div
6. Cells have class "cella inizio_ora" for hour-start slots
7. After clicking, a MODAL/POPUP should appear for customer search
8. VERIFY: Look for a modal with class "cerca_cliente" or an input field "cerca_cliente"
9. If no modal appears after 3 seconds, try clicking a different time slot

### STEP 5: SEARCH & SELECT CUSTOMER
1. In the customer search modal, you will see a search input
2. The input says "Cerca per nome, cellulare o fidelity card" (Search by name, phone or loyalty card)
3. Type the customer name: {customer_name}
4. Wait 1-2 seconds for results to appear below the search field
5. Look at the results table (id="tabella_clienti")  
6. Find the row with the matching customer name
7. Click on the customer row (tr element) to select them
8. If the customer is NOT found in results:
   - Click the "Nuovo Cliente" (New Customer) button in the modal footer
   - Fill in the customer creation form
9. After selecting, the search modal should close or transition

### STEP 6: SELECT SERVICES
1. After the customer is selected, you should see an appointment form
2. The form has service categories shown as tabs at the top
3. Look for the service section labeled "Servizi"
4. For each service to add: {services_list}
5. Find the service card/button with the matching name
6. Click each service to add it to the appointment
7. Selected services should appear highlighted or in a "selected" area
8. If a service is not visible, try scrolling the service list

### STEP 7: CONFIRM BOOKING
1. Look at the bottom of the appointment form for action buttons
2. Find the button that says "Aggiungi appuntamento" (Add Appointment)
3. It has class "button rimira primary aggiungi" or similar
4. Click it to confirm the booking
5. Wait for the modal to close and confirmation to appear

## ERROR HANDLING
- If a modal appears unexpectedly, try to close it by clicking "Chiudi" or "Annulla"
- If a step fails, wait 2 seconds and retry once
- If you see a "Errore ID cliente" (Customer ID Error) modal, click OK/Conferma to dismiss
- If the customer search modal doesn't appear after clicking a time slot, try clicking another operator's column at the same time
- If you encounter a 404 or page error, go back to login and restart

## ITALIAN VOCABULARY HELP
- "Agenda" = Schedule/Calendar
- "Cerca" = Search
- "Chiudi" = Close
- "Nuovo Cliente" = New Customer
- "Aggiungi appuntamento" = Add Appointment
- "Annulla" = Cancel
- "Conferma" = Confirm
- "Elimina" = Delete
- "Servizi" = Services
"""


async def run_booking_click(page, click_data: dict) -> dict:
    """Use browser-use AI to click a time slot on an existing Lightpanda-backed CDP page."""
    try:
        cdpt = await page.context.new_cdp_session(page)
        await cdpt.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": 100,
            "y": 100,
            "button": "left",
            "clickCount": 1
        })
        await cdpt.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": 100,
            "y": 100,
            "button": "left",
            "clickCount": 1
        })
        return {"success": True, "method": "cdp"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def run_booking(booking_data: dict) -> dict:
    """Run a full WeGest booking using browser-use AI agent with OpenAI GPT-4o."""
    from browser_use import Agent, Browser, BrowserConfig
    from browser_use.controller.service import Controller
    from langchain_openai import ChatOpenAI
    from config import WEGEST_USER, WEGEST_PASSWORD

    llm = ChatOpenAI(
        model="gpt-4o",
        api_key=OPENAI_API_KEY,
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
        from datetime import datetime as dt
        date_str = booking_data.get("date", "")
        try:
            parsed = dt.strptime(date_str, "%Y-%m-%d")
            day, month, year = str(parsed.day), str(parsed.month), str(parsed.year)
        except:
            day, month, year = "", "", date_str

        task = BOOKING_TASK.format(
            customer_name=booking_data.get("customer_name", ""),
            date=f"{day}/{month}/{year}",
            day=day,
            month=month,
            year=year,
            time=booking_data.get("time", ""),
            services=", ".join(booking_data.get("services", [])),
            services_list=", ".join(booking_data.get("services", [])),
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
