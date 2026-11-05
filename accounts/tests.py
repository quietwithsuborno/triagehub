"""Tests for sign-up, JWT login and refresh, and the User model."""

from rest_framework.test import APIClient, APITestCase
from .models import User


class RegisterTests(APITestCase):
    """POST /api/v1/auth/register/: who can sign up, and with what."""

    def test_register_creates_user_with_default_role(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "rahim",
                "email": "rahim@example.com",
                "password": "pass12345",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.data)

        user = User.objects.get(username="rahim")
        self.assertEqual(user.role, User.Role.USER)

    def test_register_ignores_role_field(self):
        self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "sneaky",
                "email": "sneaky@example.com",
                "password": "pass12345",
                "role": "admin",
            },
        )

        user = User.objects.get(username="sneaky")
        self.assertEqual(user.role, User.Role.USER)

    # Regression test: register once failed with "CSRF Failed" when the same
    # browser was logged into the admin panel (shared session cookie).
    # enforce_csrf_checks=True makes the test client behave like that browser.
    def test_register_works_while_logged_in_with_session(self):
        User.objects.create_user(username="suborno", password="pass12345")
        client = APIClient(enforce_csrf_checks=True)
        client.login(username="suborno", password="pass12345")
        response = client.post(
            "/api/v1/auth/register/",
            {
                "username": "newuser",
                "email": "new@example.com",
                "password": "pass12345",
            },
        )
        self.assertEqual(response.status_code, 201)

    # A leftover or expired token must not block sign-up (authentication is
    # turned off on RegisterView).
    def test_register_ignores_invalid_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer this-is-not-a-real-token")
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "newuser2",
                "email": "new2@example.com",
                "password": "pass12345",
            },
        )
        self.assertEqual(response.status_code, 201)

    def test_register_rejects_short_password(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "shorty", "email": "shorty@example.com", "password": "123"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="shorty").exists())

    # "12345678" is long enough, so only the common-password validator stops it.
    def test_register_rejects_common_password(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "weakuser",
                "email": "weak@example.com",
                "password": "12345678",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="weakuser").exists())


class JWTAuthTests(APITestCase):
    """Login and refresh with real tokens (no force_authenticate here)."""

    def setUp(self):
        self.user = User.objects.create_user(username="rahim", password="pass12345")
        self.login_url = "/api/v1/auth/login/"

    def test_login_returns_access_and_refresh(self):
        response = self.client.post(
            self.login_url, {"username": "rahim", "password": "pass12345"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_login_with_wrong_password_fails(self):
        response = self.client.post(
            self.login_url, {"username": "rahim", "password": "wrongpass"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)

    def test_access_token_opens_ticket_list(self):
        login = self.client.post(
            self.login_url, {"username": "rahim", "password": "pass12345"}
        )
        token = login.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get("/api/v1/tickets/")
        self.assertEqual(response.status_code, 200)

    def test_refresh_returns_new_access_token(self):
        login = self.client.post(
            self.login_url, {"username": "rahim", "password": "pass12345"}
        )
        response = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": login.data["refresh"]}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_refresh_with_invalid_token_fails(self):
        response = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": "this-is-not-a-real-token"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)


class UserModelTests(APITestCase):
    """The User model on its own, without the API."""

    def test_is_support_staff_by_role(self):
        # User(...) is not saved to the database; the property needs only role.
        user = User(role=User.Role.USER)
        agent = User(role=User.Role.AGENT)
        admin = User(role=User.Role.ADMIN)
        self.assertFalse(user.is_support_staff)
        self.assertTrue(agent.is_support_staff)
        self.assertTrue(admin.is_support_staff)
