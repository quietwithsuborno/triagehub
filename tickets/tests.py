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
        self.karim.ticket = Ticket.objects.create(
            title = 'karim ticket',
            description = 'karim er problem',
            created_by = self.karim
        )

    def test_anonymouns_cannot_list_tickets(self):
        response = self.client.get('/api/v1/tickets/')
        self.assertEqual(response.status_code, 403)

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