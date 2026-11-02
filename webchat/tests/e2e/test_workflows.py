"""All ten workflows, start to finish, in a real browser, using only buttons so the model plays no
part. A workflow that is not proven here is not proven for customers."""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import Locator, Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeout

pytestmark = pytest.mark.e2e

FIRST = "<first choice>"
CONTACT = {
    "First name": "Probe",
    "Last name": "Tester",
    "Email": "probe@example.com",
    "Phone": "07700900999",
}
TAYLOR = {
    "Booking reference": "WORK-10001",
    "Last name": "Taylor",
    "Registration": "AB12 CDE",
    "Phone": "07700900123",
}


def log(page: Page) -> Locator:
    return page.get_by_role("log")


def begin(page: Page, button: str) -> None:
    page.get_by_role("button", name="Open chat").click()
    expect(page.get_by_role("textbox", name="Your message")).to_be_enabled()
    page.get_by_role("button", name=button, exact=True).click()


def field(page: Page, label: str) -> Locator:
    """A form control by its visible label. Only the label's own text counts: a select's label
    also contains every option, and "Email" is both a field and an option."""
    return page.locator(f'form.form label.field:has(> span:text-is("{label}"))').locator(
        "input, select, textarea"
    )


def form_text(page: Page) -> str:
    """The form's text, or nothing once it has gone. Reading it never waits."""
    return page.locator("form.form").evaluate_all(
        "elements => elements.map(item => item.innerText).join('')"
    )


def fill_form(page: Page, values: dict[str, str]) -> None:
    form = page.locator("form.form")
    expect(form).to_be_visible()
    for label, value in values.items():
        control = field(page, label)
        # None of these calls wait: the form can turn into the card at any moment on a slow machine.
        tag = control.evaluate_all("elements => elements.length ? elements[0].tagName : ''")
        if not tag:
            continue
        if tag == "SELECT":
            if value == FIRST:
                control.select_option(index=1, timeout=5000)
            else:
                control.select_option(label=value, timeout=5000)
        else:
            control.fill(value, timeout=5000)


def continue_until_card(page: Page, values: dict[str, str]) -> None:
    """Fills each form as it appears. A form can follow another, such as a time after a car."""
    confirm = page.get_by_role("button", name="Confirm")
    proceed = page.get_by_role("button", name="Continue")
    for _ in range(4):
        if confirm.count():
            return
        before = form_text(page)
        try:
            fill_form(page, values)
            proceed.click(timeout=5000)
        except PlaywrightTimeout:
            continue  # The form turned into the card while this pass was filling it.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            now = form_text(page)
            idle = proceed.evaluate_all("elements => elements.every(item => !item.disabled)")
            if confirm.count() or (idle and now and now != before):
                break
            page.wait_for_timeout(100)
        page.wait_for_timeout(400)  # Let the widget finish before reading what it shows.
    expect(confirm).to_be_visible()


def confirm_and_read(page: Page, heading: str) -> None:
    expect(page.get_by_role("heading", name=heading)).to_be_visible()
    page.get_by_role("button", name="Confirm").click()


class TestSales:
    def test_test_drive(self, page: Page) -> None:
        begin(page, "Book a test drive")
        continue_until_card(page, {"Car": FIRST, "Time": FIRST, **CONTACT, "Notes": "Please have it warm"})
        expect(page.get_by_role("heading", name="Confirm your test drive")).to_be_visible()
        expect(page.get_by_text("Please have it warm")).to_be_visible()
        confirm_and_read(page, "Confirm your test drive")
        expect(log(page)).to_contain_text("TEST-")
        expect(log(page)).to_contain_text("confirmed")

    def test_vehicle_interest(self, page: Page) -> None:
        begin(page, "Register interest")
        continue_until_card(page, {"Car": FIRST, **CONTACT, "Notes": "Please call me first"})
        expect(page.get_by_text("Please call me first")).to_be_visible()
        confirm_and_read(page, "Register your interest")
        expect(log(page)).to_contain_text("WAIT-")
        expect(log(page)).to_contain_text("doesn't hold the car")

    def test_sales_enquiry(self, page: Page) -> None:
        begin(page, "Make an enquiry")
        continue_until_card(
            page,
            {
                "Dealership": "Northstar Manchester",
                "What is it about?": "Finance",
                "Your message": "What deposit would I need?",
                **CONTACT,
            },
        )
        expect(page.get_by_text("What deposit would I need?")).to_be_visible()
        confirm_and_read(page, "Send your enquiry")
        expect(log(page)).to_contain_text("SALE-")

    def test_callback_with_timing_and_context(self, page: Page) -> None:
        begin(page, "Request a callback")
        continue_until_card(
            page,
            {
                "Dealership": "Northstar Manchester",
                "Department": "Sales",
                "What is it about?": "Finance options",
                "Best time to call": "Weekday afternoons",
                "Car": FIRST,
                **CONTACT,
            },
        )
        expect(page.get_by_text("Weekday afternoons")).to_be_visible()
        confirm_and_read(page, "Request a callback")
        expect(log(page)).to_contain_text("CALL-")

    def test_part_exchange_estimate_with_its_notice(self, page: Page) -> None:
        begin(page, "Part-exchange estimate")
        expect(log(page)).to_contain_text("privacy@northstarmotors.example")
        continue_until_card(
            page,
            {
                "Dealership": "Northstar Manchester",
                "Registration": "AB12 CDE",
                "Mileage": "54000",
                "Condition": "Good",
                **CONTACT,
            },
        )
        confirm_and_read(page, "Get your estimate")
        expect(log(page)).to_contain_text("indicative")
        expect(log(page)).to_contain_text("£")


class TestContact:
    def test_message_to_a_department(self, page: Page) -> None:
        begin(page, "Leave a message")
        continue_until_card(
            page,
            {
                "Dealership": "Northstar Bolton",
                "Department": "Parts",
                "Subject": "Roof bars",
                "Your message": "Do you stock roof bars for an X3?",
                "How should we contact you?": "Email",
                **CONTACT,
            },
        )
        expect(page.get_by_text("Do you stock roof bars for an X3?")).to_be_visible()
        confirm_and_read(page, "Send your message")
        expect(log(page)).to_contain_text("MSG-")
        expect(log(page)).to_contain_text("Nobody has replied yet")


class TestWorkshop:
    def test_new_booking(self, page: Page) -> None:
        begin(page, "Book a service")
        continue_until_card(
            page,
            {
                "Dealership": "Northstar Manchester",
                "Service": "MOT",
                "Time": FIRST,
                "Registration": "AB12 CDE",
                "Mileage": "42000",
                **CONTACT,
            },
        )
        expect(page.get_by_text("42,000 miles")).to_be_visible()
        confirm_and_read(page, "Confirm your workshop booking")
        expect(log(page)).to_contain_text("WORK-")
        expect(log(page)).to_contain_text("confirmed")

    def test_retrieving_a_booking_shows_its_details(self, page: Page) -> None:
        begin(page, "Check my booking")
        fill_form(page, TAYLOR)
        page.get_by_role("button", name="Continue").click()
        expect(log(page)).to_contain_text("Full service at Northstar Manchester")
        expect(log(page)).to_contain_text("Status: booked")

    def test_a_wrong_detail_reveals_nothing(self, page: Page) -> None:
        begin(page, "Check my booking")
        fill_form(page, {**TAYLOR, "Last name": "Wrong"})
        page.get_by_role("button", name="Continue").click()
        expect(log(page)).to_contain_text("couldn't match those details")
        expect(log(page)).not_to_contain_text("Full service")
        expect(page.locator("form.form")).to_be_visible()

    def test_amending_needs_only_one_field(self, page: Page) -> None:
        begin(page, "Change a booking")
        fill_form(page, TAYLOR)
        page.get_by_role("button", name="Continue").click()
        expect(field(page, "Mileage")).to_be_visible()
        for label in ("New time", "Mileage", "Notes"):
            assert not field(page, label).evaluate("element => element.required")
        continue_until_card(page, {"Mileage": "43000"})
        confirm_and_read(page, "Confirm your booking change")
        expect(log(page)).to_contain_text("updated")

    def test_amending_to_a_new_time(self, page: Page) -> None:
        begin(page, "Change a booking")
        fill_form(page, TAYLOR)
        page.get_by_role("button", name="Continue").click()
        continue_until_card(page, {"New time": FIRST})
        expect(page.locator("dt", has_text="New time")).to_be_visible()
        confirm_and_read(page, "Confirm your booking change")
        expect(log(page)).to_contain_text("updated")

    def test_cancelling_shows_the_warning_and_the_booking(self, page: Page) -> None:
        begin(page, "Cancel a booking")
        fill_form(page, TAYLOR)
        page.get_by_role("button", name="Continue").click()
        expect(page.get_by_role("heading", name="Cancel your booking")).to_be_visible()
        expect(page.locator("dd", has_text="Full service")).to_be_visible()
        expect(page.get_by_text("may not be available again")).to_be_visible()
        page.get_by_role("button", name="Confirm").click()
        expect(log(page)).to_contain_text("Your booking has been cancelled")

    def test_a_cancelled_booking_is_shown_as_cancelled(self, page: Page) -> None:
        begin(page, "Cancel a booking")
        fill_form(page, TAYLOR)
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("button", name="Confirm").click()
        expect(log(page)).to_contain_text("Your booking has been cancelled")
        page.reload()
        page.get_by_role("button", name="Open chat").click()
        box = page.get_by_role("textbox", name="Your message")
        expect(box).to_be_enabled()
        box.fill("modeldown")  # The e2e model is down for this word, so the menu comes back.
        box.press("Enter")
        # Still verified, so no form: the booking is read again and shown as it now is.
        page.get_by_role("button", name="Check my booking", exact=True).click()
        expect(log(page)).to_contain_text("Status: cancelled")


class TestANewQuestionPutsTheFormAside:
    """No real customer chat keeps a form pinned under every later answer (D-034)."""

    RESUME = "Continue checking your booking"

    def ask(self, page: Page, text: str) -> None:
        box = page.get_by_role("textbox", name="Your message")
        expect(box).to_be_enabled()
        box.fill(text)
        box.press("Enter")

    def test_the_form_goes_and_continue_brings_it_back(self, page: Page) -> None:
        begin(page, "Check my booking")
        expect(page.locator("form.form")).to_be_visible()
        self.ask(page, "what time does bolton open")
        expect(log(page)).to_contain_text("Northstar Bolton")
        expect(page.locator("form.form")).to_have_count(0)
        page.get_by_role("button", name=self.RESUME).click()
        fill_form(page, TAYLOR)
        page.get_by_role("button", name="Continue", exact=True).click()
        expect(log(page)).to_contain_text("Full service at Northstar Manchester")

    def test_it_stays_aside_after_a_refresh(self, page: Page) -> None:
        begin(page, "Check my booking")
        # A message sent while the button's reply is still coming is ignored, so wait for it.
        expect(page.locator("form.form")).to_be_visible()
        self.ask(page, "what time does bolton open")
        expect(page.get_by_role("button", name=self.RESUME)).to_be_visible()
        page.reload()
        page.get_by_role("button", name="Open chat").click()
        expect(page.get_by_role("button", name=self.RESUME)).to_be_visible()
        expect(page.locator("form.form")).to_have_count(0)


class TestCancellingATask:
    """Before D-035 the chat said "Finish or cancel", with no way to cancel."""

    CANCELLED = "That's cancelled, and nothing was sent to the dealership."

    def test_a_form_can_be_cancelled_and_another_task_started(self, page: Page) -> None:
        begin(page, "Check my booking")
        expect(page.locator("form.form")).to_be_visible()
        log(page).get_by_role("button", name="Cancel", exact=True).click()
        expect(log(page)).to_contain_text(self.CANCELLED)
        expect(page.locator("form.form")).to_have_count(0)
        page.get_by_role("button", name="Request a callback", exact=True).click()
        expect(field(page, "What is it about?")).to_be_visible()

    def test_a_confirmation_card_can_be_cancelled(self, page: Page) -> None:
        begin(page, "Request a callback")
        continue_until_card(
            page,
            {
                "Dealership": "Northstar Manchester",
                "Department": "Sales",
                "What is it about?": "Discuss finance options",
                **CONTACT,
            },
        )
        log(page).get_by_role("button", name="Cancel", exact=True).click()
        expect(log(page)).to_contain_text(self.CANCELLED)
        expect(page.get_by_role("button", name="Confirm")).to_have_count(0)


class TestNewChat:
    """A customer can start afresh, and a refresh does not bring the old chat back (D-036)."""

    def test_a_new_chat_is_empty_and_stays_empty(self, page: Page) -> None:
        begin(page, "Opening hours")
        expect(log(page)).to_contain_text("Northstar")
        page.get_by_role("button", name="New chat").click()
        page.get_by_role("button", name="Start new chat").click()
        expect(log(page)).not_to_contain_text("Northstar Manchester")
        expect(page.get_by_role("textbox", name="Your message")).to_be_enabled()
        page.reload()
        page.get_by_role("button", name="Open chat").click()
        expect(page.get_by_role("textbox", name="Your message")).to_be_enabled()
        expect(log(page)).not_to_contain_text("Northstar Manchester")

    def test_an_open_form_is_dropped(self, page: Page) -> None:
        begin(page, "Check my booking")
        expect(page.locator("form.form")).to_be_visible()
        page.get_by_role("button", name="New chat").click()
        page.get_by_role("button", name="Start new chat").click()
        expect(page.locator("form.form")).to_have_count(0)
        page.get_by_role("button", name="Request a callback", exact=True).click()
        expect(field(page, "What is it about?")).to_be_visible()
