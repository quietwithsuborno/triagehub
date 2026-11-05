from rest_framework.test import APIClient, APITestCase
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

    def test_register_works_while_logged_in_with_session(self):
        User.objects.create_user(username="suborno", password="pass12345")
        client = APIClient(enforce_csrf_checks=True)
        client.login(username="suborno", password="pass12345")
        response = client.post(
            "/api/v1/auth/register/",
            {"username": "newuser", "email": "new@example.com", "password": "pass12345"},
        )
        self.assertEqual(response.status_code, 201)

    def test_register_ignores_invalid_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer this-is-not-a-real-token")
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "newuser2", "email": "new2@example.com", "password": "pass12345"},
        )
        self.assertEqual(response.status_code, 201)

    def test_register_rejects_short_password(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "shorty", "email": "shorty@example.com", "password": "123"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username = "shorty").exists())

    def test_register_rejects_common_password(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "weakuser", "email": "weak@example.com", "password": "12345678"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username = "weakuser").exists())


class JWTAuthTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rahim", password="pass12345")
        self.login_url = "/api/v1/auth/login/"

    def test_login_returns_access_and_refresh(self):
        response = self.client.post(self.login_url, {"username": "rahim", "password": "pass12345"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_login_with_wrong_password_fails(self):
        response = self.client.post(self.login_url, {"username": "rahim", "password": "wrongpass"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)

    def test_access_token_opens_ticket_list(self):
        login = self.client.post(self.login_url, {"username": "rahim", "password": "pass12345"})
        token = login.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200)

    def test_refresh_returns_new_access_token(self):
        login = self.client.post(self.login_url, {"username": "rahim", "password": "pass12345"})
        response = self.client.post("/api/v1/auth/refresh/", {"refresh": login.data["refresh"]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_refresh_with_invalid_token_fails(self):
        response = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": "this-is-not-a-real-token"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)