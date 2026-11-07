"""API tests for tickets, comments, filters, docs and categories.

Users in these tests: rahim is a customer (role user), karim is an agent,
jamal is a second customer. force_authenticate logs a user in without a real
token; the real JWT flow is tested in accounts/tests.py.
"""

from rest_framework.test import APITestCase
from accounts.models import User
from .models import Ticket, Comment, Category, AILog


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