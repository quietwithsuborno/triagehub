# TriageHub

An AI-powered helpdesk and ticket management system built with Django and Django REST Framework.

Users submit support tickets, agents handle them, and admins manage everything. An AI triage feature (category, priority and a short summary for each new ticket) is planned for a later phase.

## Status

Phase 1 (core backend, without AI) is complete. 

Planned next: PostgreSQL and Docker, JWT authentication, and AI triage.

## Features

- User registration and login with three roles: user, agent, admin
- Ticket create, list, update and delete, with status and priority
- Comments on tickets, with private internal notes for agents
- Role-based permissions: users see only their own tickets, agents and admins see all
- Filtering (status, priority, category), search and pagination
- Django admin panel
- Automated tests

## Tech Stack

- Python, Django, Django REST Framework
- django-filter
- PostgreSQL
- Docker and Docker Compose

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

## Running Tests

```bash
docker compose exec web python manage.py test
```