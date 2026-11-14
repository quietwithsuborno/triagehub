"""Tests for tickets, comments, filters, docs, categories and AI triage.

Users in these tests: rahim is a customer (role user), karim is an agent,
jamal is a second customer. force_authenticate logs a user in without a real
token; the real JWT flow is tested in accounts/tests.py.
"""

from unittest import mock
from rest_framework.test import APITestCase
from accounts.models import User
from .models import Ticket, Comment, Category, AILog
from .triage import (
    FakeAIClient,
    OpenAICompatibleClient,
    TriageValidationError,
    build_triage_prompt,
    get_ai_client,
    parse_triage_response,
    run_triage,
)
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.core.cache import cache
from rest_framework.throttling import SimpleRateThrottle


class TicketPermissionTests(APITestCase):
    """Who can see, create, change and delete which tickets."""

    def setUp(self):
        self.rahim = User.objects.create_user(username="rahim", password="pass12345")
        self.karim = User.objects.create_user(
            username="karim", password="pass12345", role=User.Role.AGENT
        )
        self.rahim_ticket = Ticket.objects.create(
            title="Rahim ticket", description="rahim er problem", created_by=self.rahim
        )
        self.karim_ticket = Ticket.objects.create(
            title="karim ticket", description="karim er problem", created_by=self.karim
        )

    def test_anonymous_cannot_list_tickets(self):
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 401)

    def test_user_sees_only_own_tickets(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

        self.assertEqual(response.data["results"][0]["id"], self.rahim_ticket.id)

    def test_user_cannot_delete_ticket(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.delete(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Ticket.objects.filter(id=self.rahim_ticket.id).exists())

    def test_agent_can_delete_ticket(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.delete(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Ticket.objects.filter(id=self.rahim_ticket.id).exists())

    # 404, not 403: a customer should not even learn that the ticket exists.
    def test_user_cannot_view_other_users_ticket(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.get(f"/api/v1/tickets/{self.karim_ticket.id}/")
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_update_ticket(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", {"status": "closed"}
        )
        self.assertEqual(response.status_code, 403)
        # refresh_from_db reloads the row, to prove the database did not change.
        self.rahim_ticket.refresh_from_db()
        self.assertEqual(self.rahim_ticket.status, Ticket.Status.OPEN)

    def test_agent_can_update_status(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", {"status": "in_progress"}
        )
        self.assertEqual(response.status_code, 200)
        self.rahim_ticket.refresh_from_db()
        self.assertEqual(self.rahim_ticket.status, Ticket.Status.IN_PROGRESS)

    def test_agent_sees_all_tickets(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_admin_sees_all_and_can_resolve(self):
        admin = User.objects.create_user(
            username="boss", password="pass12345", role=User.Role.ADMIN
        )
        self.client.force_authenticate(user=admin)
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.data["count"], 2)

        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", {"status": "resolved"}
        )
        self.assertEqual(response.status_code, 200)

    def test_agent_can_assign_ticket_to_agent(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", {"assigned_to": self.karim.id}
        )
        self.assertEqual(response.status_code, 200)
        self.rahim_ticket.refresh_from_db()
        self.assertEqual(self.rahim_ticket.assigned_to, self.karim)

    def test_user_creates_ticket_with_defaults(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.post(
            "/api/v1/tickets/",
            {"title": "New problem", "description": "Something is broken"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "open")
        self.assertEqual(response.data["priority"], "medium")
        self.assertEqual(response.data["created_by_username"], "rahim")

    # created_by is read-only, so the value in the request is ignored.
    def test_user_cannot_create_ticket_as_someone_else(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.post(
            "/api/v1/tickets/",
            {
                "title": "Fake",
                "description": "Pretending to be karim",
                "created_by": self.karim.id,
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["created_by"], self.rahim.id)

    def test_anonymous_cannot_view_ticket_or_comments(self):
        response = self.client.get(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 401)

        response = self.client.get(f"/api/v1/tickets/{self.rahim_ticket.id}/comments/")
        self.assertEqual(response.status_code, 401)

    # The request still succeeds (201); the server just replaces the values.
    def test_user_cannot_set_triage_fields_on_create(self):
        billing = Category.objects.create(name="Billing")
        self.client.force_authenticate(user=self.rahim)
        response = self.client.post(
            "/api/v1/tickets/",
            {
                "title": "Please fix fast",
                "description": "I want this to be urgent",
                "priority": "urgent",
                "status": "closed",
                "category": billing.id,
                "assigned_to": self.karim.id,
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["priority"], "medium")
        self.assertEqual(response.data["status"], "open")
        self.assertIsNone(response.data["category"])
        self.assertIsNone(response.data["assigned_to"])

    def test_agent_can_set_triage_fields_on_create(self):
        billing = Category.objects.create(name="Billing")
        self.client.force_authenticate(user=self.karim)
        response = self.client.post(
            "/api/v1/tickets/",
            {
                "title": "Server down for all customers",
                "description": "Reported by phone",
                "priority": "urgent",
                "category": billing.id,
                "assigned_to": self.karim.id,
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["priority"], "urgent")
        self.assertEqual(response.data["category"], billing.id)
        self.assertEqual(response.data["assigned_to"], self.karim.id)

    def test_cannot_assign_ticket_to_regular_user(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", {"assigned_to": self.rahim.id}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("assigned_to", response.data)
        self.rahim_ticket.refresh_from_db()
        self.assertIsNone(self.rahim_ticket.assigned_to)

    def test_ticket_shows_category_name(self):
        bug = Category.objects.create(name="Bug")
        self.rahim_ticket.category = bug
        self.rahim_ticket.save()

        self.client.force_authenticate(user=self.rahim)
        response = self.client.get(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["category"], bug.id)
        self.assertEqual(response.data["category_name"], "Bug")

    def test_ticket_without_category_has_null_category_name(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.get(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("category_name", response.data)
        self.assertIsNone(response.data["category_name"])


class CommentPrivacyTests(APITestCase):
    """Internal notes stay hidden from customers.

    setUp makes one ticket with one public comment (by rahim) and one
    internal note (by karim).
    """

    def setUp(self):
        self.rahim = User.objects.create_user(username="rahim", password="pass12345")
        self.karim = User.objects.create_user(
            username="karim", password="pass12345", role=User.Role.AGENT
        )
        self.ticket = Ticket.objects.create(
            title="rahim ticket", description="rahim er problem", created_by=self.rahim
        )
        Comment.objects.create(
            ticket=self.ticket, author=self.rahim, body="public comment"
        )
        Comment.objects.create(
            ticket=self.ticket,
            author=self.karim,
            body="private note",
            is_internal=True,
        )

        self.url = f"/api/v1/tickets/{self.ticket.id}/comments/"

    def test_user_cannot_see_internal_comments(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertFalse(response.data[0]["is_internal"])

    def test_user_cannot_create_internal_comment(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.post(
            self.url, {"body": "ami orivate banate chai", "is_internal": True}
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["is_internal"])

    # created_at was once a date only. A "T" in the ISO string
    # (2026-11-05T10:30:00Z) means the time is included.
    def test_comment_created_at_includes_time(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("T", response.data[0]["created_at"])

    def test_agent_can_create_internal_note(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.post(
            self.url, {"body": "Team note", "is_internal": True}
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["is_internal"])

    def test_user_cannot_see_comments_on_other_users_ticket(self):
        jamal = User.objects.create_user(username="jamal", password="pass12345")
        self.client.force_authenticate(user=jamal)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_comment_as_someone_else(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.post(
            self.url, {"body": "Fake comment", "author": self.karim.id}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["author"], self.rahim.id)

    def test_comments_show_author_name_and_role(self):
        self.client.force_authenticate(user=self.karim)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data[0]["author_username"], "rahim")
        self.assertEqual(response.data[0]["author_role"], "user")

        self.assertEqual(response.data[1]["author_username"], "karim")
        self.assertEqual(response.data[1]["author_role"], "agent")


class TicketFilterTests(APITestCase):
    """Filtering, search and pagination on the ticket list (as an agent)."""

    def setUp(self):
        self.karim = User.objects.create_user(
            username="karim", password="pass12345", role=User.Role.AGENT
        )
        Ticket.objects.create(
            title="Payment page error",
            description="Customers cannot pay",
            status=Ticket.Status.OPEN,
            created_by=self.karim,
        )
        Ticket.objects.create(
            title="Dark mode request",
            description="Please add dark mode",
            status=Ticket.Status.RESOLVED,
            created_by=self.karim,
        )
        self.client.force_authenticate(user=self.karim)

    def test_filter_by_status(self):
        response = self.client.get("/api/v1/tickets/?status=resolved")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Dark mode request")

    def test_search_by_word(self):
        response = self.client.get("/api/v1/tickets/?search=payment")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Payment page error")

    def test_filter_by_priority(self):
        Ticket.objects.create(
            title="Server down",
            description="Nothing loads",
            priority=Ticket.Priority.URGENT,
            created_by=self.karim,
        )
        response = self.client.get("/api/v1/tickets/?priority=urgent")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Server down")

    def test_filter_by_category(self):
        billing = Category.objects.create(name="Billing")
        Ticket.objects.create(
            title="Invoice missing",
            description="No invoice email",
            category=billing,
            created_by=self.karim,
        )
        response = self.client.get(f"/api/v1/tickets/?category={billing.id}")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Invoice missing")

    def test_search_in_description(self):
        response = self.client.get("/api/v1/tickets/?search=customers")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Payment page error")

    def test_list_is_paginated(self):
        for i in range(11):
            Ticket.objects.create(
                title=f"Bulk ticket {i}", description="bulk", created_by=self.karim
            )
        response = self.client.get("/api/v1/tickets/")
        # 2 tickets from setUp + 11 here = 13 in total, but PAGE_SIZE is 10.
        self.assertEqual(response.data["count"], 13)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertIsNotNone(response.data["next"])


class APIDocsTests(APITestCase):
    """The schema and Swagger page load (and the schema builds without errors)."""

    def test_schema_is_available(self):
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)

    def test_docs_page_is_available(self):
        response = self.client.get("/api/docs/")
        self.assertEqual(response.status_code, 200)


class CategoryAPITests(APITestCase):
    """Read-only category list."""

    def setUp(self):
        self.rahim = User.objects.create_user(username="rahim", password="pass12345")
        Category.objects.create(name="Bug")
        Category.objects.create(name="Billing")
        self.url = "/api/v1/categories/"

    def test_logged_in_user_can_list_categories(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        names = [category["name"] for category in response.data]
        self.assertEqual(names, ["Billing", "Bug"])  # sorted by name, not by id

    def test_anonymous_cannot_list_categories(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_user_cannot_create_category(self):
        self.client.force_authenticate(user=self.rahim)
        response = self.client.post(self.url, {"name": "Hacked"})
        # 405 Method Not Allowed: POST does not exist on this endpoint for anyone.
        self.assertEqual(response.status_code, 405)
        self.assertFalse(Category.objects.filter(name="Hacked").exists())


class AILogModelTests(APITestCase):
    """The AI log model on its own, without any AI call."""

    def setUp(self):
        self.rahim = User.objects.create_user(username="rahim", password="pass12345")
        self.ticket = Ticket.objects.create(
            title="Cannot login", description="Error 500", created_by=self.rahim
        )

    def test_log_is_linked_to_its_ticket(self):
        log = AILog.objects.create(
            ticket=self.ticket,
            purpose=AILog.Purpose.TRIAGE,
            status=AILog.Status.SUCCESS,
            input_tokens=1200,
            output_tokens=80,
        )
        self.assertEqual(list(self.ticket.ai_logs.all()), [log])

    def test_deleting_ticket_keeps_its_logs(self):
        log = AILog.objects.create(
            ticket=self.ticket,
            purpose=AILog.Purpose.TRIAGE,
            status=AILog.Status.ERROR,
            error_message="Timeout",
        )
        self.ticket.delete()
        log.refresh_from_db()
        self.assertIsNone(log.ticket)
        self.assertEqual(log.error_message, "Timeout")


class TriagePromptTests(SimpleTestCase):
    """The triage prompt and the fake AI client (no database needed)."""

    def setUp(self):
        self.instructions, self.ticket_text = build_triage_prompt(
            "Cannot login", "Error 500 since morning", ["Account", "Billing", "Bug"]
        )

    def test_prompt_lists_every_category(self):
        self.assertIn("Account, Billing, Bug", self.instructions)

    # Guards against drift: if a new priority is added to the model,
    # the AI must be told about it too.
    def test_prompt_lists_every_priority_value(self):
        for value in Ticket.Priority.values:
            self.assertIn(f"- {value}:", self.instructions)

    def test_customer_text_stays_out_of_instructions(self):
        self.assertTrue(self.ticket_text.startswith("<ticket>"))
        self.assertTrue(self.ticket_text.endswith("</ticket>"))
        self.assertIn("Cannot login", self.ticket_text)
        self.assertNotIn("Cannot login", self.instructions)

    def test_fake_client_returns_its_text_and_records_the_call(self):
        client = FakeAIClient(text='{"priority": "high"}')
        response = client.complete(self.instructions, self.ticket_text)
        self.assertEqual(response.text, '{"priority": "high"}')
        self.assertEqual(response.provider, "fake")
        self.assertEqual(client.calls, [(self.instructions, self.ticket_text)])

    def test_fake_client_can_simulate_a_failure(self):
        client = FakeAIClient(error=TimeoutError("AI took too long"))
        with self.assertRaises(TimeoutError):
            client.complete(self.instructions, self.ticket_text)


class TriageValidationTests(SimpleTestCase):
    """The check of the AI's answer: what is accepted, fixed or rejected."""

    CATEGORIES = ["Account", "Billing", "Bug"]

    def parse(self, text):
        return parse_triage_response(text, self.CATEGORIES)

    def assertRejected(self, text, reason):
        # assertRaisesMessage also checks that the error explains the reason.
        with self.assertRaisesMessage(TriageValidationError, reason):
            self.parse(text)

    # --- accepted ---

    def test_valid_answer_is_accepted(self):
        result = self.parse(
            '{"category": "Billing", "priority": "high", "summary": "Charged twice."}'
        )
        self.assertEqual(result.category_name, "Billing")
        self.assertEqual(result.priority, "high")
        self.assertEqual(result.summary, "Charged twice.")

    def test_extra_words_and_code_fence_around_json_are_ignored(self):
        text = (
            "Here is the triage:\n```json\n"
            '{"category": "Bug", "priority": "low", "summary": "Typo on page."}'
            "\n```"
        )
        self.assertEqual(self.parse(text).category_name, "Bug")

    def test_case_and_spaces_are_fixed(self):
        result = self.parse(
            '{"category": " billing ", "priority": " URGENT ", "summary": " Down. "}'
        )
        self.assertEqual(result.category_name, "Billing")  # our exact spelling
        self.assertEqual(result.priority, "urgent")
        self.assertEqual(result.summary, "Down.")

    def test_null_category_is_allowed(self):
        result = self.parse(
            '{"category": null, "priority": "low", "summary": "General question."}'
        )
        self.assertIsNone(result.category_name)

    # Boundary: exactly the limit must still pass.
    def test_summary_of_300_characters_is_allowed(self):
        summary = "a" * 300
        result = self.parse(
            f'{{"category": "Bug", "priority": "low", "summary": "{summary}"}}'
        )
        self.assertEqual(len(result.summary), 300)

    # --- rejected ---

    def test_answer_without_json_is_rejected(self):
        self.assertRejected("Sorry, I cannot help with that.", "No JSON object")

    def test_broken_json_is_rejected(self):
        self.assertRejected("{category: Bug}", "not valid JSON")

    def test_unknown_category_is_rejected(self):
        self.assertRejected(
            '{"category": "Payment", "priority": "high", "summary": "x"}',
            "Unknown category",
        )

    def test_category_that_is_not_text_is_rejected(self):
        self.assertRejected(
            '{"category": 2, "priority": "high", "summary": "x"}',
            "Category must be text",
        )

    def test_unknown_priority_is_rejected(self):
        self.assertRejected(
            '{"category": "Bug", "priority": "critical", "summary": "x"}',
            "Unknown priority",
        )

    def test_missing_priority_is_rejected(self):
        self.assertRejected(
            '{"category": "Bug", "summary": "x"}', "Priority is missing"
        )

    def test_missing_summary_is_rejected(self):
        self.assertRejected(
            '{"category": "Bug", "priority": "low"}', "Summary is missing"
        )

    def test_blank_summary_is_rejected(self):
        self.assertRejected(
            '{"category": "Bug", "priority": "low", "summary": "   "}',
            "Summary is missing",
        )

    def test_summary_over_300_characters_is_rejected(self):
        summary = "a" * 301
        self.assertRejected(
            f'{{"category": "Bug", "priority": "low", "summary": "{summary}"}}',
            "the limit is 300",
        )


class RunTriageTests(APITestCase):
    """run_triage with a fake AI: the result it returns and the log it writes."""

    VALID_ANSWER = (
        '{"category": "billing", "priority": "high", "summary": "Charged twice."}'
    )

    def setUp(self):
        self.rahim = User.objects.create_user(username="rahim", password="pass12345")
        Category.objects.create(name="Billing")
        Category.objects.create(name="Bug")
        self.ticket = Ticket.objects.create(
            title="Double payment",
            description="My card was charged twice.",
            created_by=self.rahim,
        )

    def test_no_client_means_no_call_and_no_log(self):
        self.assertIsNone(run_triage(self.ticket, None))
        self.assertEqual(AILog.objects.count(), 0)

    def test_valid_answer_returns_result_and_logs_success(self):
        result = run_triage(self.ticket, FakeAIClient(text=self.VALID_ANSWER))
        self.assertEqual(result.category_name, "Billing")
        self.assertEqual(result.priority, "high")

        log = AILog.objects.get()  # exactly one log, or this line fails
        self.assertEqual(log.status, AILog.Status.SUCCESS)
        self.assertEqual(log.ticket, self.ticket)
        self.assertEqual(log.provider, "fake")
        self.assertEqual(log.input_tokens, 100)
        self.assertEqual(log.raw_response, self.VALID_ANSWER)
        self.assertIsNotNone(log.latency_ms)

    def test_ai_error_returns_none_and_logs_error(self):
        client = FakeAIClient(error=TimeoutError("AI took too long"))
        self.assertIsNone(run_triage(self.ticket, client))

        log = AILog.objects.get()
        self.assertEqual(log.status, AILog.Status.ERROR)
        self.assertEqual(log.error_message, "TimeoutError: AI took too long")
        self.assertIsNone(log.input_tokens)  # no answer, so no token count

    def test_invalid_answer_returns_none_and_logs_invalid(self):
        answer = '{"category": "Payment", "priority": "high", "summary": "x"}'
        self.assertIsNone(run_triage(self.ticket, FakeAIClient(text=answer)))

        log = AILog.objects.get()
        self.assertEqual(log.status, AILog.Status.INVALID)
        self.assertIn("Unknown category", log.error_message)
        self.assertEqual(log.raw_response, answer)  # kept for debugging
        self.assertEqual(log.input_tokens, 100)  # the call still cost tokens

    def test_prompt_uses_categories_from_the_database(self):
        client = FakeAIClient(text=self.VALID_ANSWER)
        run_triage(self.ticket, client)
        instructions, ticket_text = client.calls[0]
        self.assertIn("Billing, Bug", instructions)
        self.assertIn("My card was charged twice.", ticket_text)


class TicketTriageAPITests(APITestCase):
    """Creating a ticket through the API runs the AI triage (with a fake AI).

    mock.patch swaps get_ai_client for the length of one request, so the real
    view code runs unchanged but talks to a FakeAIClient.
    """

    VALID_ANSWER = (
        '{"category": "Billing", "priority": "high", "summary": "Charged twice."}'
    )

    def setUp(self):
        self.rahim = User.objects.create_user(username="rahim", password="pass12345")
        self.karim = User.objects.create_user(
            username="karim", password="pass12345", role=User.Role.AGENT
        )
        self.billing = Category.objects.create(name="Billing")
        self.bug = Category.objects.create(name="Bug")

    def create_ticket(self, user, ai, **extra_fields):
        """POST a new ticket as user, with ai as the AI client."""
        self.client.force_authenticate(user=user)
        data = {
            "title": "Double payment",
            "description": "My card was charged twice.",
            **extra_fields,
        }
        with mock.patch("tickets.triage.get_ai_client", return_value=ai):
            return self.client.post("/api/v1/tickets/", data)

    def test_customer_ticket_gets_the_ai_values(self):
        response = self.create_ticket(self.rahim, FakeAIClient(text=self.VALID_ANSWER))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["category"], self.billing.id)
        self.assertEqual(response.data["priority"], "high")
        self.assertEqual(response.data["ai_summary"], "Charged twice.")
        # The database has them too, not only the response.
        ticket = Ticket.objects.get(id=response.data["id"])
        self.assertEqual(ticket.priority, "high")

    def test_ai_error_still_creates_the_ticket_with_defaults(self):
        ai = FakeAIClient(error=TimeoutError("AI took too long"))
        response = self.create_ticket(self.rahim, ai)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["category"])
        self.assertEqual(response.data["priority"], "medium")
        self.assertEqual(response.data["ai_summary"], "")
        self.assertEqual(AILog.objects.get().status, AILog.Status.ERROR)

    def test_invalid_ai_answer_still_creates_the_ticket_with_defaults(self):
        response = self.create_ticket(self.rahim, FakeAIClient(text="Sorry."))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["priority"], "medium")
        self.assertEqual(AILog.objects.get().status, AILog.Status.INVALID)

    def test_agent_ticket_keeps_agent_choices_and_gets_summary(self):
        response = self.create_ticket(
            self.karim,
            FakeAIClient(text=self.VALID_ANSWER),
            priority="urgent",
            category=self.bug.id,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["priority"], "urgent")  # not the AI's "high"
        self.assertEqual(response.data["category"], self.bug.id)  # not Billing
        self.assertEqual(response.data["ai_summary"], "Charged twice.")

    def test_description_of_5000_characters_is_accepted(self):
        response = self.create_ticket(self.rahim, None, description="a" * 5000)
        self.assertEqual(response.status_code, 201, response.data)

    def test_description_over_5000_characters_is_rejected(self):
        ai = FakeAIClient(text=self.VALID_ANSWER)
        response = self.create_ticket(self.rahim, ai, description="a" * 5001)
        self.assertEqual(response.status_code, 400)
        self.assertIn("description", response.data)
        self.assertEqual(ai.calls, [])  # the AI was never asked

    def test_unexpected_triage_error_still_returns_201(self):
        self.client.force_authenticate(user=self.rahim)
        # Patch where views looks the name up: tickets.views, not tickets.triage.
        with mock.patch(
            "tickets.views.triage_ticket", side_effect=RuntimeError("a bug")
        ):
            with self.assertLogs("tickets.views", level="ERROR"):
                response = self.client.post(
                    "/api/v1/tickets/", {"title": "T", "description": "D"}
                )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(Ticket.objects.filter(id=response.data["id"]).exists())


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class AIClientSettingsTests(SimpleTestCase):
    """get_ai_client reads .env settings; the real client, without network."""

    # Proves the guard at the end of settings.py: tests never call a real AI,
    # even when .env turns AI triage on.
    def test_ai_is_off_while_tests_run(self):
        self.assertFalse(settings.AI_TRIAGE_ENABLED)

    @override_settings(AI_TRIAGE_ENABLED=False)
    def test_ai_off_gives_no_client(self):
        self.assertIsNone(get_ai_client())

    @override_settings(
        AI_TRIAGE_ENABLED=True, AI_BASE_URL=GEMINI_URL, AI_API_KEY="", AI_MODEL="m"
    )
    def test_missing_key_gives_no_client_and_a_warning(self):
        with self.assertLogs("tickets.triage", level="WARNING"):
            self.assertIsNone(get_ai_client())

    @override_settings(
        AI_TRIAGE_ENABLED=True,
        AI_BASE_URL=GEMINI_URL,
        AI_API_KEY="test-key",
        AI_MODEL="gemini-test",
    )
    def test_full_settings_give_the_real_client(self):
        client = get_ai_client()
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual(client.provider, "generativelanguage.googleapis.com")
        self.assertEqual(client.model, "gemini-test")

    def test_real_client_sends_two_messages_and_reads_tokens(self):
        client = OpenAICompatibleClient(GEMINI_URL, "test-key", "gemini-test", 10)
        # Replace the openai library's client with a mock: no network call.
        client.client = mock.Mock()
        fake_response = mock.Mock()
        fake_response.choices = [mock.Mock(message=mock.Mock(content="answer"))]
        fake_response.usage = mock.Mock(prompt_tokens=321, completion_tokens=12)
        client.client.chat.completions.create.return_value = fake_response

        response = client.complete("our rules", "<ticket>...</ticket>")

        client.client.chat.completions.create.assert_called_once_with(
            model="gemini-test",
            messages=[
                {"role": "system", "content": "our rules"},
                {"role": "user", "content": "<ticket>...</ticket>"},
            ],
        )
        self.assertEqual(response.text, "answer")
        self.assertEqual(response.input_tokens, 321)
        self.assertEqual(response.output_tokens, 12)


class ThrottleTests(APITestCase):
    """Speed limits: too many requests in a short time get 429.

    Tests normally run with very high limits (see TESTING in settings.py).
    Here mock.patch.dict sets low limits for one test only.
    """

    def setUp(self):
        # Throttling counts requests in the cache. Start every test from zero.
        cache.clear()

    def test_too_many_login_attempts_get_429(self):
        data = {"username": "nobody", "password": "wrong-password"}
        with mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"anon": "3/minute"}):
            for _ in range(3):
                response = self.client.post("/api/v1/auth/login/", data)
                self.assertEqual(response.status_code, 401)
            response = self.client.post("/api/v1/auth/login/", data)
        self.assertEqual(response.status_code, 429)

    def test_too_many_new_tickets_get_429_but_reading_still_works(self):
        rahim = User.objects.create_user(username="rahim", password="pass12345")
        self.client.force_authenticate(user=rahim)
        data = {"title": "T", "description": "D"}
        with mock.patch.dict(
            SimpleRateThrottle.THROTTLE_RATES, {"ticket_create": "2/hour"}
        ):
            for _ in range(2):
                response = self.client.post("/api/v1/tickets/", data)
                self.assertEqual(response.status_code, 201)
            response = self.client.post("/api/v1/tickets/", data)
            self.assertEqual(response.status_code, 429)
            # The stricter limit is only for creating; the list still opens.
            self.assertEqual(self.client.get("/api/v1/tickets/").status_code, 200)
