# TriageHub

An AI-powered helpdesk and ticket management system built with Django and Django REST Framework.

Users submit support tickets, agents handle them, and admins manage everything. When a customer submits a ticket, an AI reads it and fills in the category, the priority and a one-sentence summary. If the AI is slow, down or gives an unusable answer, the ticket is still created with default values.

## Status

Phase 1 (core backend), Phase 2 (PostgreSQL and Docker), Phase 3 (JWT authentication and API docs) and Phase 4 (AI triage) are complete.

Next: a knowledge assistant that answers questions from product documentation.

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
- AI triage of new tickets: category, priority and summary
- Works with any AI provider that has an OpenAI-compatible API, chosen in `.env`
- A log of every AI call (result, tokens, time, errors) in the admin panel
- Rate limits on login, registration and ticket creation

## Tech Stack

- Python, Django, Django REST Framework
- django-filter
- PostgreSQL
- Docker and Docker Compose
- Simple JWT
- drf-spectacular
- openai (Python library, used to talk to any OpenAI-compatible AI provider)

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

5. Log in to the admin panel and add the ticket categories, for example Account, Billing, Bug, Feature Request and General. The AI can only choose from the categories that exist here.

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

## AI Triage

AI triage is off by default, so the project runs without any API key. To turn it on, set these lines in your `.env` and restart the web container:

```
AI_TRIAGE_ENABLED=True
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
AI_API_KEY=your-key
AI_MODEL=gemini-flash-lite-latest
AI_TIMEOUT_SECONDS=10
```

```bash
docker compose up -d --force-recreate web
```

The example uses Google Gemini. Any provider with an OpenAI-compatible API works (for example OpenAI or Groq): change `AI_BASE_URL`, `AI_API_KEY` and `AI_MODEL` only. No code change is needed. Never commit a real key; `.env` is in `.gitignore`.

How it works:

- **Customer tickets** get category, priority and summary from the AI.
- **Agent and admin tickets** keep the category and priority the agent chose; the AI only writes the summary.
- **If the AI fails** (timeout, network, wrong key, or an answer that does not pass validation), the ticket is still created with the defaults: priority medium, no category, empty summary.
- **Every AI call is logged** in the admin panel under **AI logs**, with the status, the AI's raw answer, token counts and the time it took. Check there first when something looks wrong.

Common errors in the AI log:

| Error | Meaning | What to do |
| --- | --- | --- |
| 404 | The model name does not exist at this URL | Check `AI_MODEL` and `AI_BASE_URL` |
| 401 or 403 | The key is wrong or not allowed | Check `AI_API_KEY` |
| 429 | The provider's rate limit was reached | Wait, or use a paid plan |
| 503 | The provider is busy right now | Try again later, or use a less busy model |

Cost: one ticket uses about 325 input and 40 output tokens. With a small model such as Gemini Flash-Lite on a paid plan, that is roughly 5 US cents per 1,000 tickets. The free tier of Gemini is enough for testing, but Google may use free-tier data to improve its products, so use a paid plan for real customer data.

## Rate Limits

Too many requests in a short time get `429 Too Many Requests`:

| What | Limit |
| --- | --- |
| Requests without login (login, register), per IP address | 20 per minute |
| Requests by one logged-in user | 120 per minute |
| New tickets by one user | 30 per hour |

The limits are in `REST_FRAMEWORK` in `config/settings.py`.

## Running Tests

```bash
docker compose exec web python manage.py test
```

Tests never call a real AI, even when it is turned on in `.env`; they use a fake AI client instead.