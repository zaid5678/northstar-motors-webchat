"""The thin slice in a real browser: open, send, see progress, get a reply, refresh, still there."""

from __future__ import annotations

import pytest
from playwright.sync_api import Locator, Page, expect

pytestmark = pytest.mark.e2e


def launcher(page: Page) -> Locator:
    return page.get_by_role("button", name="Open chat")


def box(page: Page) -> Locator:
    return page.get_by_role("textbox", name="Your message")


def send(page: Page, text: str) -> None:
    box(page).fill(text)
    box(page).press("Enter")


def log(page: Page) -> Locator:
    return page.get_by_role("log")


class TestTheSlice:
    def test_opening_sending_and_getting_a_reply(self, page: Page) -> None:
        launcher(page).click()
        send(page, "any hybrids?")
        expect(log(page)).to_contain_text("any hybrids?")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")

    def test_progress_shows_while_waiting(self, page: Page) -> None:
        launcher(page).click()
        send(page, "any hybrids?")
        expect(page.get_by_role("status")).to_contain_text("Understanding")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")
        expect(page.get_by_role("status")).to_have_text("")

    def test_vehicle_cards_link_to_the_website(self, page: Page) -> None:
        launcher(page).click()
        send(page, "any hybrids?")
        link = log(page).get_by_role("link").first
        expect(link).to_be_visible()
        assert "http://localhost:4173/?vehicle=veh-" in (link.get_attribute("href") or "")

    def test_the_conversation_survives_a_refresh(self, page: Page) -> None:
        launcher(page).click()
        send(page, "any hybrids?")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")
        page.reload()
        launcher(page).click()
        expect(log(page)).to_contain_text("any hybrids?")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")

    def test_a_refresh_during_a_reply_still_gets_the_reply(self, page: Page) -> None:
        launcher(page).click()
        send(page, "any hybrids?")
        expect(page.get_by_role("status")).to_contain_text("Understanding")
        page.reload()
        launcher(page).click()
        expect(log(page)).to_contain_text("Here are the hybrids I found.", timeout=10000)

    def test_an_unread_reply_is_shown_on_the_closed_launcher(self, page: Page) -> None:
        launcher(page).click()
        send(page, "any hybrids?")
        page.get_by_role("button", name="Close chat").click()
        expect(page.get_by_role("button", name="Open chat, 1 unread")).to_be_visible()


class TestSafety:
    def test_model_html_is_shown_as_text(self, page: Page) -> None:
        launcher(page).click()
        send(page, "evilscript")
        expect(log(page)).to_contain_text("<img src=x")
        assert page.evaluate("window.hacked") is None

    def test_the_widget_is_isolated_in_shadow_dom(self, page: Page) -> None:
        launcher(page).click()
        assert page.evaluate("document.querySelector('northstar-chat').shadowRoot !== null")


class TestKeyboardAndAssistiveTech:
    def test_it_works_with_a_keyboard_alone(self, page: Page) -> None:
        launcher(page).focus()
        page.keyboard.press("Enter")
        expect(box(page)).to_be_focused()
        page.keyboard.type("any hybrids?")
        page.keyboard.press("Enter")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")

    def test_escape_closes_and_returns_focus(self, page: Page) -> None:
        launcher(page).click()
        expect(box(page)).to_be_focused()
        page.keyboard.press("Escape")
        expect(box(page)).to_be_hidden()
        expect(launcher(page)).to_be_focused()

    def test_new_messages_are_announced(self, page: Page) -> None:
        launcher(page).click()
        expect(log(page)).to_have_attribute("aria-live", "polite")

    def test_the_panel_is_labelled(self, page: Page) -> None:
        launcher(page).click()
        expect(page.get_by_role("dialog", name="Northstar Motors chat")).to_be_visible()


class TestLayout:
    def test_it_does_not_cover_the_page_when_closed(self, page: Page) -> None:
        size = launcher(page).bounding_box()
        assert size is not None
        assert size["width"] <= 80 and size["height"] <= 80

    def test_a_phone_gets_a_full_width_panel_with_no_sideways_scroll(self, phone: Page) -> None:
        launcher(phone).click()
        panel = phone.get_by_role("dialog", name="Northstar Motors chat").bounding_box()
        assert panel is not None
        assert panel["x"] >= 0
        assert panel["x"] + panel["width"] <= 390
        assert panel["width"] >= 350
        assert phone.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )


class TestVehicleDialog:
    """D-015. `showModal` makes everything outside the dialog inert."""

    def open_vehicle(self, page: Page) -> None:
        page.goto("http://localhost:4173/?vehicle=veh-001")
        expect(page.locator("#vehicle-dialog")).to_be_visible()

    def test_chat_works_while_the_dialog_is_open(self, page: Page) -> None:
        self.open_vehicle(page)
        launcher(page).click()
        send(page, "any hybrids?")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")

    def test_opening_the_dialog_keeps_the_conversation(self, page: Page) -> None:
        launcher(page).click()
        send(page, "any hybrids?")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")
        page.evaluate("document.querySelector('#vehicle-dialog').showModal()")
        expect(log(page)).to_contain_text("any hybrids?")
        expect(box(page)).to_be_enabled()
        send(page, "any hybrids?")
        expect(page.get_by_role("status")).to_contain_text("Understanding")

    def test_closing_the_dialog_puts_the_widget_back(self, page: Page) -> None:
        self.open_vehicle(page)
        page.locator("#close-dialog").click()
        launcher(page).click()
        expect(box(page)).to_be_visible()

    def test_it_does_not_cover_the_dialog_close_button(self, page: Page) -> None:
        self.open_vehicle(page)
        page.locator("#close-dialog").click(timeout=3000)
        expect(page.locator("#vehicle-dialog")).to_be_hidden()

    def test_escape_in_chat_closes_chat_not_the_dialog(self, page: Page) -> None:
        self.open_vehicle(page)
        launcher(page).click()
        expect(box(page)).to_be_focused()
        page.keyboard.press("Escape")
        expect(box(page)).to_be_hidden()
        expect(page.locator("#vehicle-dialog")).to_be_visible()

    def test_page_context_reaches_the_server(self, page: Page) -> None:
        self.open_vehicle(page)
        launcher(page).click()
        with page.expect_request("**/api/chat/stream") as sent:
            send(page, "any hybrids?")
        assert "veh-001" in (sent.value.post_data or "")


class TestFailureStates:
    def test_a_model_outage_shows_buttons_that_work(self, page: Page) -> None:
        launcher(page).click()
        send(page, "modeldown")
        button = page.get_by_role("button", name="Opening hours")
        expect(button).to_be_visible()
        button.click()
        expect(log(page)).to_contain_text("Northstar Bolton")

    def test_an_unreachable_server_shows_an_error_and_keeps_the_message(self, page: Page) -> None:
        launcher(page).click()
        expect(box(page)).to_be_enabled()
        page.route("**/api/chat/stream", lambda route: route.abort())
        box(page).fill("any hybrids?")
        box(page).press("Enter")
        expect(page.get_by_role("alert")).to_contain_text("couldn't send")
        expect(box(page)).to_have_value("any hybrids?")

    def test_a_missing_chat_server_leaves_the_website_working(self, page: Page) -> None:
        page.route("**/widget.js", lambda route: route.abort())
        page.reload()
        expect(page.locator("#vehicle-grid article").first).to_be_visible()


def start_callback(page: Page) -> None:
    launcher(page).click()
    page.get_by_role("button", name="Request a callback").click()
    page.get_by_label("Dealership").select_option(label="Northstar Manchester")
    page.get_by_label("Department").select_option("sales")
    page.get_by_label("What is it about?").fill("Discuss finance options")
    page.get_by_label("First name").fill("Probe")
    page.get_by_label("Last name").fill("Tester")
    page.get_by_label("Email").fill("probe@example.com")
    page.get_by_label("Phone").fill("07700900999")
    page.get_by_role("button", name="Continue").click()


class TestConfirmFlow:
    def test_a_customer_can_confirm_and_see_the_reference(self, page: Page) -> None:
        start_callback(page)
        expect(page.get_by_role("heading", name="Request a callback")).to_be_visible()
        expect(page.get_by_text("Discuss finance options")).to_be_visible()
        page.get_by_role("button", name="Confirm").click()
        expect(log(page)).to_contain_text("CALL-")

    def test_focus_moves_to_the_confirmation(self, page: Page) -> None:
        start_callback(page)
        expect(page.get_by_role("heading", name="Request a callback")).to_be_focused()

    def test_it_can_be_confirmed_by_keyboard(self, page: Page) -> None:
        start_callback(page)
        expect(page.get_by_role("heading", name="Request a callback")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.get_by_role("button", name="Confirm")).to_be_focused()
        page.keyboard.press("Enter")
        expect(log(page)).to_contain_text("CALL-")

    def test_a_refresh_restores_the_confirmation(self, page: Page) -> None:
        start_callback(page)
        expect(page.get_by_role("button", name="Confirm")).to_be_visible()
        page.reload()
        launcher(page).click()
        page.get_by_role("button", name="Confirm").click()
        expect(log(page)).to_contain_text("CALL-")

    def test_a_refresh_after_confirming_keeps_the_result(self, page: Page) -> None:
        start_callback(page)
        page.get_by_role("button", name="Confirm").click()
        expect(log(page)).to_contain_text("CALL-")
        page.reload()
        launcher(page).click()
        expect(log(page)).to_contain_text("CALL-")
        expect(page.get_by_role("button", name="Confirm")).to_have_count(0)

    def test_a_double_click_sends_one_request_result(self, page: Page) -> None:
        start_callback(page)
        page.get_by_role("button", name="Confirm").dblclick()
        expect(log(page)).to_contain_text("CALL-")
        assert log(page).get_by_text("CALL-").count() == 1


class TestStates:
    def test_unavailable_when_the_server_cannot_be_reached(self, page: Page) -> None:
        page.route("**/api/session", lambda route: route.abort())
        page.reload()
        launcher(page).click()
        expect(page.get_by_role("alert")).to_contain_text("unavailable")
        expect(page.get_by_role("button", name="Try again")).to_be_visible()

    def test_trying_again_recovers(self, page: Page) -> None:
        page.route("**/api/session", lambda route: route.abort())
        page.reload()
        launcher(page).click()
        page.unroute("**/api/session")
        page.get_by_role("button", name="Try again").click()
        expect(box(page)).to_be_enabled()

    def test_loading_is_announced(self, page: Page) -> None:
        held = []
        page.route("**/api/chat/transcript", lambda route: held.append(route))
        page.reload()
        launcher(page).click()
        expect(page.get_by_role("status")).to_contain_text("Loading")
        expect(box(page)).to_be_disabled()
        held[0].continue_()
        expect(box(page)).to_be_enabled()
        page.unroute("**/api/chat/transcript")

    def test_a_dropped_stream_resumes_and_gets_the_reply(self, page: Page) -> None:
        launcher(page).click()
        expect(box(page)).to_be_enabled()
        page.evaluate(
            """() => {
              const real = window.fetch;
              window.fetch = async (url, init) => {
                const response = await real(url, init);
                if (init?.method === 'POST' && String(url).endsWith('/api/chat/stream')) {
                  const reader = response.body.getReader();
                  const body = new ReadableStream({
                    async pull(controller) {
                      const { value } = await reader.read();
                      controller.enqueue(value);
                      await reader.cancel();
                      controller.error(new TypeError('network dropped'));
                    },
                  });
                  return new Response(body, { headers: response.headers });
                }
                return response;
              };
            }"""
        )
        send(page, "any hybrids?")
        expect(log(page)).to_contain_text("Here are the hybrids I found.", timeout=10000)
        expect(page.get_by_role("alert")).to_have_count(0)


class TestInspector:
    def test_it_is_hidden_by_default(self, page: Page) -> None:
        launcher(page).click()
        expect(page.get_by_role("region", name="Developer inspector")).to_have_count(0)

    def test_the_flag_shows_tool_calls_and_verdicts(self, page: Page) -> None:
        page.goto("http://localhost:4173/?inspector=1")
        launcher(page).click()
        send(page, "any hybrids?")
        expect(log(page)).to_contain_text("Here are the hybrids I found.")
        panel = page.get_by_role("region", name="Developer inspector")
        expect(panel).to_contain_text("StockSearched")
        expect(panel).to_contain_text("model")


class TestOtherAddresses:
    """Not everyone reaches the site as `localhost`: a phone on the same network never does."""

    def test_the_whole_site_and_chat_work_from_another_address(self, page: Page) -> None:
        page.goto("http://127.0.0.1:4173")
        expect(page.locator("#vehicle-grid article").first).to_be_visible()
        launcher(page).click()
        send(page, "any hybrids?")
        link = log(page).get_by_role("link").first
        expect(link).to_be_visible()
        assert (link.get_attribute("href") or "").startswith("http://127.0.0.1:4173/?vehicle=")
        image = log(page).locator("img").first
        assert (image.get_attribute("src") or "").startswith("http://127.0.0.1:4010/assets/")
