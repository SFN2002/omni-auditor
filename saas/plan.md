# Omni-Auditor SaaS Dashboard — Build Plan

## Overview
Build a complete SaaS Dashboard for the Omni-Auditor Python static analysis engine.
The project has 4 main components: Backend (FastAPI), Frontend (React), Database & Docker (PostgreSQL + Redis + nginx), and Integration layer.

## Stage 1 — Skill Loading & Design
- Read vibecoding-webapp-swarm SKILL.md (for React frontend)
- Read vibecoding-general-swarm SKILL.md (for FastAPI backend, Docker, integration)
- Create Design PRD (design.md) for frontend visual design

## Stage 2 — Parallel Agent Execution (5 agents)

### Agent 1: Backend Core (FastAPI)
**Path**: `saas/backend/`
**Skill**: vibecoding-general-swarm
- main.py: FastAPI app with CORS middleware
- models.py: SQLAlchemy async models (User, Project, Scan, Finding, Baseline)
- auth.py: GitHub OAuth2 + JWT token handling
- config.py: Pydantic Settings with env vars
- api/projects.py: Project CRUD endpoints
- api/scans.py: Scan CRUD + run analysis endpoint
- api/webhooks.py: GitHub webhook receiver
- api/findings.py: Findings list/filter/export
- tasks.py: Celery + Redis async task integration
- requirements.txt: All Python dependencies
- Dockerfile: Backend container image

### Agent 2: Frontend (React + TypeScript)
**Path**: `saas/frontend/`
**Skill**: vibecoding-webapp-swarm
- Vite + React 18 + TypeScript scaffold
- Tailwind CSS + shadcn/ui component setup
- Recharts for risk trends, severity distribution, 90-D vector
- React Query (TanStack Query) for API calls
- Zustand for global state
- Pages: Landing, Dashboard, Project Detail, Scan Detail, Settings
- Components: RiskChart, FindingTable, VectorChart, Sidebar, Header
- Dockerfile: Frontend container image (nginx)

### Agent 3: Infrastructure (Docker + DB)
**Path**: `saas/`
- docker-compose.yml: PostgreSQL 15 + Redis 7 + Backend + Frontend + Nginx
- init.sql: Database schema initialization
- .env.example: All required environment variables
- nginx.conf: Reverse proxy routing

### Agent 4: Integration & Coordinator
**Path**: `saas/`
- README.md: Setup instructions, API docs
- Integrate all agents' code
- Verify docker-compose up works end-to-end
- API contract documentation (OpenAPI)

## Stage 3 — Integration & Validation
- Review all code for consistency
- Verify Docker compose integration
- Ensure frontend-backend contract alignment
- Validate all key features are implemented

## Key Features Checklist
1. [ ] Real-time risk score trends (line chart over time)
2. [ ] Security findings table (sortable, filterable)
3. [ ] 90-D vector visualization (3D bar/radar chart)
4. [ ] Baseline comparison (IMPROVED/STABLE/DEGRADED/FRACTURED)
5. [ ] SARIF export button
6. [ ] GitHub OAuth login
7. [ ] Team/organization support (multi-user)

## File Structure
```
saas/
├── backend/
│   ├── main.py
│   ├── models.py
│   ├── auth.py
│   ├── config.py
│   ├── tasks.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── api/
│       ├── __init__.py
│       ├── projects.py
│       ├── scans.py
│       ├── findings.py
│       └── webhooks.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── Dockerfile
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── index.css
│       ├── stores/
│       ├── hooks/
│       ├── components/
│       ├── pages/
│       └── lib/
├── docker-compose.yml
├── nginx.conf
├── init.sql
├── .env.example
└── README.md
```
