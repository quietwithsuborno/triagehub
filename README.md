# TriageHub

An AI-powered helpdesk and ticket management system built with Django and Django REST Framework.

Users submit support tickets, agents handle them, and admins manage everything. When a customer submits a ticket, an AI reads it and fills in the category, the priority and a one-sentence summary. If the AI is slow, down or gives an unusable answer, the ticket is still created with default values.

A knowledge assistant answers questions about PrismERP from its public documentation ([docs.prismerp.net](https://docs.prismerp.net/)), with links to the pages it used. It also drafts replies to tickets for agents, and gives each module a short overview with a flowchart.

## Status

Phase 1 (core backend), Phase 2 (PostgreSQL and Docker), Phase 3 (JWT authentication and API docs), Phase 4 (AI triage) and Phase 5 (knowledge assistant) are complete.

Next: improvements to the assistant and the tickets (Phase 6), then a web interface (Phase 7).

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
- Questions answered from the PrismERP documentation, in English, Bangla or Banglish, with source links
- Reply drafts for agents, written from the documentation (never sent to the customer automatically)
- Module overviews: a summary in English and Bangla and a Mermaid flowchart for each module
- Works with any AI provider that has an OpenAI-compatible API, chosen in `.env`
- A log of every AI call (result, tokens, time, errors) in the admin panel
- Rate limits on login, registration, ticket creation, questions and reply drafts

## Tech Stack

- Python, Django, Django REST Framework
- django-filter
- PostgreSQL with pgvector (vector search)
- Docker and Docker Compose
- Simple JWT
- drf-spectacular
- openai (Python library, used to talk to any OpenAI-compatible AI provider)
- requests and Beautiful Soup (reading the documentation pages)

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

## Knowledge Assistant

The assistant uses the same `AI_BASE_URL`, `AI_API_KEY` and `AI_MODEL` as triage (it does not need `AI_TRIAGE_ENABLED`), plus an embedding model. Add this line to `.env`:

```
EMBEDDING_MODEL=gemini-embedding-2
```

### How it works

1. **Fetch:** the documentation pages are read and saved, split into chunks by heading. Images on the pages are described by the AI and saved as chunks too.
2. **Embed:** every chunk gets an embedding (a list of 768 numbers that stands for its meaning), stored in PostgreSQL with pgvector.
3. **Search:** a question is embedded the same way, and the 5 chunks closest in meaning are found.
4. **Answer:** the AI writes the answer from those chunks only. The code checks the answer before anyone sees it: the sources must be real chunks, the text must use only Bangla or Latin letters, and the links are built by the code, never written by the AI. If a check fails, the AI is asked once more.

The answer is in the language of the question: English, Bangla, or Banglish (Bangla written in Latin letters). When the documentation has no answer, the user gets a fixed message suggesting they send the question to the support team.

### Filling the knowledge base

Run these once, in this order:

```bash
docker compose exec web python manage.py fetch_docs https://docs.prismerp.net/ --limit 50
docker compose exec web python manage.py describe_images --limit 20
docker compose exec web python manage.py embed_chunks --limit 200
docker compose exec web python manage.py build_overviews
```

- `fetch_docs` reads up to `--limit` pages, starting from the given address and following its links, with a pause between pages.
- `describe_images` and `embed_chunks` do a limited number at a time because of free-tier limits. Run them again until they report nothing left.
- Running any of them again is safe: pages, images, chunks and modules that have not changed are skipped.

To check the quality:

```bash
docker compose exec web python manage.py search_docs "How do I cancel a purchase order?"
docker compose exec web python manage.py evaluate_answers --output evaluation_results.csv
```

`search_docs` shows which chunks a question finds, without asking the AI. `evaluate_answers` asks the 20 test questions in `knowledge/evaluation_questions.json` and writes the answers to a CSV file. Last run: 20 of 20 correct, all in the right language.

### Endpoints

| Method | URL | Who | What |
| --- | --- | --- | --- |
| POST | `/api/v1/knowledge/ask/` | Logged-in users | Ask a question; returns `found`, `answer` and `sources` |
| GET | `/api/v1/knowledge/modules/` | Logged-in users | All module overviews |
| GET | `/api/v1/knowledge/modules/{module}/` | Logged-in users | One module: summary in English and Bangla, and a Mermaid flowchart |
| POST | `/api/v1/tickets/{id}/suggested-reply/` | Agents and admins | A reply draft for a ticket, from the documentation |

The reply draft is never sent to the customer. The agent reads it, changes it if needed, and posts it as a comment. Customers get `403`.

When the AI is busy or its answer fails the checks twice, these endpoints return `503`. Every AI call (answers, embeddings, image descriptions, overviews) is in the admin panel under **AI logs**.

### Known limitations

- **Two questions in one message:** the second question is often missed, because the search finds chunks for the first one. Planned for Phase 6.
- **Free-tier model:** `gemini-flash-lite-latest`. Bigger free models timed out or reached the daily limit. Answers should be tested again with a paid key.
- **Part of the documentation only:** only some pages of docs.prismerp.net are loaded so far. Questions about other modules get "not found" until the rest is fetched.
- **Data:** with the free tier, only the public documentation and sample tickets go to the AI. Real customer data only with a paid plan.

## Rate Limits

Too many requests in a short time get `429 Too Many Requests`:

| What | Limit |
| --- | --- |
| Requests without login (login, register), per IP address | 20 per minute |
| Requests by one logged-in user | 120 per minute |
| New tickets by one user | 30 per hour |
| Questions to the knowledge assistant by one user | 20 per hour |
| Reply drafts by one agent | 60 per hour |

The limits are in `REST_FRAMEWORK` in `config/settings.py`.

## Running Tests

```bash
docker compose exec web python manage.py test
```

Tests never call a real AI or read real web pages, even when the AI is turned on in `.env`; they use fakes instead.