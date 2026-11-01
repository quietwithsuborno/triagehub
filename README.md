# TriageHub

An AI-powered helpdesk and ticket management system built with Django and Django REST Framework.

Users submit support tickets, agents handle them, and admins manage everything. An AI triage feature (category, priority and a short summary for each new ticket) is planned for a later phase.

## Status

Phase 1 (core backend), Phase 2 (PostgreSQL and Docker) and Phase 3 (JWT authentication and API docs) are complete. 

Next: AI triage.

## Features

- User registration and login with three roles: user, agent, admin
- Ticket create, list, update and delete, with status and priority
- Comments on tickets, with private internal notes for agents
- Role-based permissions: users see only their own tickets, agents and admins see all
- Filtering (status, priority, category), search and pagination
- Django admin panel
- Automated tests
- JWT authentication with access and refresh tokens
- Interactive API documentation with Swagger (OpenAPI 3)

## Tech Stack

- Python, Django, Django REST Framework
- django-filter
- PostgreSQL
- Docker and Docker Compose
- Simple JWT
- drf-spectacular

## Getting Started

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running.

1. Clone the repository and go into the folder.
2. Create your `.env` file from the example, then open it and set a new `SECRET_KEY`:

```bash
   copy .env.example .env
```

3. Build and start the containers:

```bash
   docker compose up --build -d
```

4. Create the database tables and an admin account:

```bash
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
```

The API is available at `http://127.0.0.1:8000/api/v1/` and the admin panel at `http://127.0.0.1:8000/admin/`.

## API Documentation

Open http://127.0.0.1:8000/api/docs/ to see all endpoints in Swagger UI and try them in the browser.

## Authentication

The API uses JWT tokens.

1. Log in to get an access token and a refresh token:

   ```
   POST /api/v1/auth/login/
   {"username": "your-username", "password": "your-password"}
   ```

2. Send the access token with every request:

   ```
   Authorization: Bearer <access-token>
   ```

3. The access token expires after about 5 minutes. Get a new one with the refresh token:

   ```
   POST /api/v1/auth/refresh/
   {"refresh": "<refresh-token>"}
   ```

In Swagger, log in with `/api/v1/auth/login/`, then click **Authorize** and paste the access token.

## Running Tests

```bash
docker compose exec web python manage.py test
```