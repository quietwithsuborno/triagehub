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
- SQLite (PostgreSQL planned)

## Getting Started

1. Clone the repository and go into the folder.
2. Create and activate a virtual environment:

```bash
   python -m venv venv
   venv\Scripts\activate
```

3. Install the dependencies:

```bash
   pip install -r requirements.txt
```

4. Create the database and an admin account:

```bash
   python manage.py migrate
   python manage.py createsuperuser
```

5. Run the server:

```bash
   python manage.py runserver
```

The API is available at `http://127.0.0.1:8000/api/v1/` and the admin panel at `http://127.0.0.1:8000/admin/`.

## Running Tests

```bash
python manage.py test
```
