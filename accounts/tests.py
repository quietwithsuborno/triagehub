from rest_framework.test import APITestCase
from .models import User

class RegisterTests(APITestCase):
    def test_register_creates_user_with_default_role(self):
        response = self.client.post(
            '/api/v1/auth/register/',
            {
                'username': 'rahim',
                'email': 'rahim@example.com',
                'password': 'pass12345',
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertNotIn('password', response.data)

        user = User.objects.get(username='rahim')
        self.assertEqual(user.role, User.Role.USER)

    def test_register_ignores_role_field(self):
        self.client.post(
            '/api/v1/auth/register/',
            {
                'username': 'sneaky',
                'email': 'sneaky@example.com',
                'password': 'pass12345',
                'role': 'admin'
            }
        )

        user = User.objects.get(username='sneaky')
        self.assertEqual(user.role, User.Role.USER)