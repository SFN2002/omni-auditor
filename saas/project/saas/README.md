# Omni-Auditor SaaS Dashboard

A full-stack web application that provides a web interface for the Omni-Auditor Python static analysis engine. Teams can manage projects, trigger security scans, view findings, track risk trends over time, and export results in SARIF format.

---

## Key Features

- **GitHub OAuth Authentication** — Secure login via GitHub with JWT session management
- **Organization Management** — Multi-tenant orgs with role-based access (owner, admin, member)
- **Project Dashboard** — Track all your repositories with risk scoring and trend analysis
- **Security Scanning** — Trigger manual, webhook-driven, or scheduled scans via Celery workers
- **90-D Risk Vector** — Multi-dimensional risk assessment across 30 security dimensions
- **Baseline Comparison** — Track security posture changes over time (improved/stable/degraded/fractured)
- **SARIF Export** — Export findings in standardized SARIF v2.1.0 format
- **GitHub Webhooks** — Automatic scan triggers on push and pull request events
- **Real-time Charts** — Risk trend visualization with Recharts
- **Responsive UI** — Modern React 19 frontend with Tailwind CSS

---

## Architecture

```
                    +-------------+
     User --------> |   Nginx     |  Port 80
                    |  (Proxy)    |
                    +------+------+
                           |
            +--------------+--------------+
            |                             |
     +------v------+             +--------v--------+
     |  Frontend   |             |    Backend      |
     |  React 19   |             |   FastAPI       |
     |  Port 5173  |             |   Port 8000     |
     +-------------+             +--------+--------+
                                        |
                           +------------+------------+
                           |            |            |
                     +-----v-----+ +----v----+ +----v----+
                     |  PostgreSQL| |  Redis  | | Celery  |
                     |   Port 5432| |Port 6379| | Worker  |
                     +------------+ +---------+ +---------+
```

### Request Flow

1. User accesses `http://localhost` → Nginx reverse proxy
2. `/api/*` → routed to FastAPI backend
3. `/docs`, `/redoc` → routed to FastAPI (Swagger/ReDoc)
4. All other paths → routed to React frontend
5. Backend queries PostgreSQL and Redis as needed
6. Celery workers process background scan tasks

### Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Frontend | React + TypeScript + Vite | 19 / 7 |
| Styling | Tailwind CSS + shadcn/ui | 3.4.19 |
| Charts | Recharts | latest |
| State | Zustand | latest |
| Data Fetching | TanStack Query | latest |
| Backend | FastAPI (async) | latest |
| Database | PostgreSQL | 15 |
| ORM | SQLAlchemy | 2.0 async |
| Cache/Queue | Redis + Celery | 7 / latest |
| Auth | GitHub OAuth2 + JWT | python-jose |
| Proxy | Nginx | alpine |

---

## Project Structure

```
.
├── README.md                 # This file
├── API_CONTRACT.md           # Full API documentation
├── FRONTEND_INTEGRATION.md   # Frontend integration guide
├── docker-compose.yml        # Docker Compose orchestration
├── nginx.conf                # Nginx reverse proxy config
├── init.sql                  # Database schema + seed data
├── .env.example              # Environment variable template
├── Makefile                  # Common commands
│
├── backend/                  # FastAPI application
│   ├── Dockerfile            # Backend container image
│   ├── requirements.txt      # Python dependencies
│   ├── main.py               # FastAPI app entry point
│   ├── config.py             # Pydantic settings
│   ├── schemas.py            # Pydantic request/response models
│   ├── models.py             # SQLAlchemy ORM models
│   ├── database.py           # DB connection & session management
│   ├── auth.py               # JWT & GitHub OAuth helpers
│   ├── celery_app.py         # Celery configuration
│   ├── tasks.py              # Background task definitions
│   └── api/                  # API route modules
│       ├── __init__.py
│       ├── auth.py           # OAuth + JWT endpoints
│       ├── orgs.py           # Organization CRUD
│       ├── projects.py       # Project CRUD + risk/baseline
│       ├── scans.py          # Scan CRUD + SARIF export
│       ├── findings.py       # Finding CRUD + statistics
│       ├── webhooks.py       # GitHub webhook receiver
│       └── health.py         # Health check endpoints
│
└── frontend/                 # React application
    ├── Dockerfile            # Frontend container image
    ├── nginx.conf            # Frontend nginx (SPA serving)
    ├── package.json          # Node dependencies
    ├── vite.config.ts        # Vite build configuration
    ├── tsconfig.json         # TypeScript configuration
    ├── tailwind.config.js    # Tailwind CSS configuration
    ├── postcss.config.js     # PostCSS configuration
    ├── index.html            # HTML entry point
    ├── public/               # Static assets
    └── src/                  # React source code
        ├── main.tsx          # React app entry
        ├── App.tsx           # Root component with routing
        ├── index.css         # Global styles
        ├── components/       # Reusable UI components
        ├── pages/            # Page-level components
        ├── stores/           # Zustand state stores
        ├── hooks/            # Custom React hooks
        └── lib/              # Utilities + API client
```

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker | 20.10+ | [Install Docker](https://docs.docker.com/get-docker/) |
| Docker Compose | 2.0+ | Included with Docker Desktop |
| GitHub OAuth App | — | [Create one here](https://github.com/settings/applications/new) |

### GitHub OAuth App Setup

1. Go to **Settings → Developer settings → OAuth Apps → New OAuth App**
2. Fill in the application details:
   - **Application name**: Omni-Auditor (dev)
   - **Homepage URL**: `http://localhost`
   - **Authorization callback URL**: `http://localhost/api/v1/auth/github/callback`
3. Click **Register application**
4. Click **Generate a new client secret**
5. Save the **Client ID** and **Client Secret** for your `.env` file

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repository-url>
cd saas

# 2. Copy environment template
cp .env.example .env

# 3. Edit .env with your GitHub OAuth credentials
#    GITHUB_CLIENT_ID=your_github_client_id
#    GITHUB_CLIENT_SECRET=your_github_client_secret
#    GITHUB_WEBHOOK_SECRET=your_webhook_secret

# 4. Start all services
docker-compose up -d

# 5. Open the application
open http://localhost
```

### Verify Everything is Running

```bash
# Check all containers are healthy
docker-compose ps

# View logs
docker-compose logs -f backend

# Test the API
curl http://localhost/api/v1/health
```

### Stop the Services

```bash
docker-compose down

# To also remove volumes (database data):
docker-compose down -v
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string (asyncpg) | `postgresql+asyncpg://postgres:postgres@db:5432/omniauditor` |
| `REDIS_URL` | Redis connection string | `redis://redis:6379/0` |
| `CELERY_BROKER_URL` | Celery message broker | `redis://redis:6379/1` |
| `GITHUB_CLIENT_ID` | GitHub OAuth app client ID | *(required)* |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth app client secret | *(required)* |
| `GITHUB_WEBHOOK_SECRET` | Secret for verifying webhook signatures | *(required)* |
| `SECRET_KEY` | JWT signing key (change in production!) | `your-super-secret-key-change-in-production` |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `JWT_EXPIRATION_HOURS` | JWT token lifetime in hours | `24` |
| `OMNI_AUDITOR_PATH` | Path to omni-auditor CLI in container | `/app/omni-auditor` |
| `FRONTEND_URL` | Frontend URL for CORS | `http://localhost:5173` |
| `VITE_API_URL` | API URL for frontend build | `http://localhost:8000` |
| `ENVIRONMENT` | Runtime environment | `development` |
| `LOG_LEVEL` | Logging level | `info` |
| `POSTGRES_USER` | PostgreSQL username | `postgres` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `postgres` |
| `POSTGRES_DB` | PostgreSQL database name | `omniauditor` |

---

## API Documentation

The API is self-documented with interactive explorers:

- **Swagger UI**: http://localhost/docs
- **ReDoc**: http://localhost/redoc
- **OpenAPI Schema**: http://localhost/openapi.json

For detailed endpoint documentation, see [API_CONTRACT.md](API_CONTRACT.md).

---

## Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `users` | GitHub-authenticated users with profile info |
| `organizations` | Teams/organizations with plan tiers |
| `organization_members` | Membership linking users to orgs with roles |
| `projects` | Code repositories to be scanned |
| `scans` | Individual security scan executions |
| `findings` | Vulnerability findings from scans |
| `baselines` | Security posture snapshots for comparison |
| `webhook_events` | Received GitHub webhook events |

### Relationships

```
users 1--* organization_members *--1 organizations
organizations 1--* projects
projects 1--* scans
projects 1--* findings
scans 1--* findings
projects 1--* baselines
projects 1--* webhook_events
```

### Indexes

All frequently queried columns are indexed: `github_id`, `slug`, `org_id`, `project_id`, `scan_id`, `severity`, `status`, `category`, `rule_id`, `baseline_status`, and more.

---

## GitHub Integration

### OAuth Authentication

The application uses GitHub OAuth 2.0 for user authentication:

1. User clicks "Sign in with GitHub" → frontend calls `/api/v1/auth/github`
2. Backend redirects to GitHub authorization page
3. User authorizes → GitHub redirects to `/api/v1/auth/github/callback`
4. Backend exchanges code for token, fetches user profile, creates JWT
5. Frontend stores JWT in localStorage for subsequent API calls

### Webhook Setup

To enable automatic scans on code changes:

1. In your GitHub repository, go to **Settings → Webhooks → Add webhook**
2. **Payload URL**: `https://your-domain.com/api/v1/webhooks/github`
3. **Content type**: `application/json`
4. **Secret**: Same value as `GITHUB_WEBHOOK_SECRET` in your `.env`
5. **Events**: Select "Push" and "Pull request"
6. Click **Add webhook**

The webhook handler will:
- Verify the signature using `GITHUB_WEBHOOK_SECRET`
- Store the event in `webhook_events`
- Automatically trigger a scan for pushes to the default branch
- Trigger scans for PR open, synchronize, and reopen events

---

## Development

### Running Services Separately

#### Backend Only

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000` with auto-reload.

#### Frontend Only

```bash
cd frontend
npm install
npm run dev
```

The dev server will start at `http://localhost:5173` with HMR.

#### Database Only

```bash
docker-compose up -d db redis
```

#### Celery Worker

```bash
cd backend
celery -A celery_app worker --loglevel=info
```

#### Celery Beat (Scheduler)

```bash
cd backend
celery -A celery_app beat --loglevel=info
```

### Running Tests

```bash
# Backend tests
cd backend
pytest

# Frontend tests
cd frontend
npm test
```

### Database Migrations

The project uses Alembic for database migrations:

```bash
cd backend

# Create a new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

---

## Key Features (Detailed)

- **Multi-tenant Organizations** — Create organizations, invite members with role-based permissions (owner/admin/member), and manage multiple teams
- **Project Management** — Link GitHub repositories, configure default branches, and track project health over time
- **Three Scan Triggers** — Initiate scans manually, automatically via GitHub webhooks, or on a schedule with Celery Beat
- **90-D Risk Vector** — A 30-dimensional security risk model covering injection, authentication, cryptography, cloud security, supply chain, and more. Each dimension scored 0.00-1.00
- **Baseline Comparison** — Compare current scan results against historical baselines to determine if security posture has improved, degraded, stayed stable, or fractured (critical/high increase >50%)
- **SARIF v2.1.0 Export** — Export any scan's findings in the industry-standard SARIF format for integration with other security tools
- **Interactive Risk Charts** — Visualize risk trends over time with Recharts line charts and radar charts for the 90-D vector
- **Finding Management** — Triage findings with status updates (open/fixed/false_positive/accepted) and filter by severity, status, category, or project
- **Aggregated Statistics** — Dashboard-level stats showing finding counts by severity, status, category, and time period (7d/30d)

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Support

For issues, feature requests, or contributions, please open an issue on the project repository.
