from rest_framework.test import APITestCase
from accounts.models import User
from .models import Ticket, Comment

class TicketPermissionTests(APITestCase):
    def setUp(self):
        self.rahim = User.objects.create_user(
            username = 'rahim', password = 'pass12345'
        )
        self.karim = User.objects.create_user(
            username = 'karim', password = 'pass12345', role = User.Role.AGENT
        )
        self.rahim_ticket = Ticket.objects.create(
            title = 'Rahim ticket',
            description = 'rahim er problem',
            created_by = self.rahim
        )
        self.karim_ticket = Ticket.objects.create(
            title = 'karim ticket',
            description = 'karim er problem',
            created_by = self.karim
        )

    def test_anonymous_cannot_list_tickets(self):
        response = self.client.get('/api/v1/tickets/')
        self.assertEqual(response.status_code, 401)

    def test_user_sees_only_own_tickets(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.get('/api/v1/tickets/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

        self.assertEqual(response.data['results'][0]['id'], self.rahim_ticket.id)

    def test_user_cannot_delete_ticket(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.delete(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Ticket.objects.filter(id = self.rahim_ticket.id).exists())

    def test_agent_can_delete_ticket(self):
        self.client.force_authenticate(user = self.karim)
        response = self.client.delete(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Ticket.objects.filter(id = self.rahim_ticket.id).exists())

    def test_user_cannot_view_other_users_ticket(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.get(f"/api/v1/tickets/{self.karim_ticket.id}/")
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_update_ticket(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", 
            {'status': 'closed'}
        )
        self.assertEqual(response.status_code, 403)
        self.rahim_ticket.refresh_from_db()
        self.assertEqual(self.rahim_ticket.status, Ticket.Status.OPEN)

    def test_agent_can_update_status(self):
        self.client.force_authenticate(user = self.karim)
        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", 
            {'status': 'in_progress'}
        )
        self.assertEqual(response.status_code, 200)
        self.rahim_ticket.refresh_from_db()
        self.assertEqual(self.rahim_ticket.status, Ticket.Status.IN_PROGRESS)

    def test_agent_sees_all_tickets(self):
        self.client.force_authenticate(user = self.karim)
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_admin_sees_all_and_can_resolve(self):
        admin = User.objects.create_user(
            username = "boss", password = "pass12345", role = User.Role.ADMIN
        )
        self.client.force_authenticate(user = admin)
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.data["count"], 2)

        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", {"status": "resolved"}
        )
        self.assertEqual(response.status_code, 200)

    def test_agent_can_assign_ticket_to_agent(self):
        self.client.force_authenticate(user = self.karim)
        response = self.client.patch(
            f"/api/v1/tickets/{self.rahim_ticket.id}/", {"assigned_to": self.karim.id}
        )
        self.assertEqual(response.status_code, 200)
        self.rahim_ticket.refresh_from_db()
        self.assertEqual(self.rahim_ticket.assigned_to, self.karim)

    def test_user_cannot_create_ticket_as_someone_else(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.post(
            "/api/v1/tickets/",
            {"title": "Fake", "description": "Pretending to be karim", "created_by": self.karim.id},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["created_by"], self.rahim.id)

    def test_anonymous_cannot_view_ticket_or_comments(self):
        response = self.client.get(f"/api/v1/tickets/{self.rahim_ticket.id}/")
        self.assertEqual(response.status_code, 401)

        response = self.client.get(f"/api/v1/tickets/{self.rahim_ticket.id}/comments/")
        self.assertEqual(response.status_code, 401)


class CommentPrivacyTests(APITestCase):
    def setUp(self):
        self.rahim = User.objects.create_user(
            username = 'rahim', password = 'pass12345'
        )
        self.karim = User.objects.create_user(
            username = 'karim', password = 'pass12345', role = User.Role.AGENT
        )
        self.ticket = Ticket.objects.create(
            title = 'rahim ticket', 
            description = 'rahim er problem',
            created_by = self.rahim
        )
        Comment.objects.create(
            ticket = self.ticket, 
            author = self.rahim,
            body = 'public comment'
        )
        Comment.objects.create(
            ticket = self.ticket,
            author = self.karim,
            body = 'private note',
            is_internal = True,
        )

        self.url = f"/api/v1/tickets/{self.ticket.id}/comments/"

    def test_user_cannot_see_internal_comments(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertFalse(response.data[0]['is_internal'])

    def test_user_cannot_create_internal_comment(self):
        self.client.force_authenticate(user = self.rahim)
        response= self.client.post(
            self.url, 
            {'body': 'ami orivate banate chai', 'is_internal' : True}
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data['is_internal'])

    def test_comment_created_at_includes_time(self):
        self.client.force_authenticate(user = self.karim)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("T", response.data[0]['created_at'])

    def test_agent_can_see_internal_note(self):
        self.client.force_authenticate(user = self.karim)
        response = self.client.post(
            self.url,
            {'body': 'Team note', 'is_internal' : True} 
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['is_internal'])

    def test_user_cannot_see_comments_on_other_users_ticket(self):
        jamal = User.objects.create_user(username = "jamal", password = "pass12345")
        self.client.force_authenticate(user = jamal)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_user_creates_ticket_with_defaults(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.post(
            "/api/v1/tickets/",
            {"title": "New problem", "description": "Something is broken"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "open")
        self.assertEqual(response.data["priority"], "medium")
        self.assertEqual(response.data["created_by_username"], "rahim")

    def test_user_cannot_comment_as_someone_else(self):
        self.client.force_authenticate(user = self.rahim)
        response = self.client.post(
            self.url, {"body": "Fake comment", "author": self.karim.id}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["author"], self.rahim.id)

class TicketFilterTests(APITestCase):
    def setUp(self):
        self.karim = User.objects.create_user(
            username = "karim", password = "pass12345", role = User.Role.AGENT
        )
        Ticket.objects.create(
            title = "Payment page error",
            description = "Customers cannot pay",
            status = Ticket.Status.OPEN,
            created_by = self.karim,
        )
        Ticket.objects.create(
            title = "Dark mode request",
            description = "Please add dark mode",
            status = Ticket.Status.RESOLVED,
            created_by = self.karim,
        )
        self.client.force_authenticate(user = self.karim)

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


class APIDocsTests(APITestCase):
    def test_schema_is_available(self):
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)

    def test_docs_page_is_available(self):
        response = self.client.get("/api/docs/")
        self.assertEqual(response.status_code, 200)