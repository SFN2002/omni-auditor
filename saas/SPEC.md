# Omni-Auditor SaaS Dashboard — Technical Specification

## 1. Project Overview

Omni-Auditor SaaS Dashboard is a full-stack web application that provides a web interface for the Omni-Auditor Python static analysis engine. It allows teams to manage projects, trigger security scans, view findings, track risk trends over time, and export results in SARIF format.

**Architecture**: FastAPI backend + React frontend + PostgreSQL + Redis + Docker

---

## 2. Technology Stack

### Backend
- **Framework**: FastAPI (async)
- **Database**: PostgreSQL 15 (SQLAlchemy 2.0 async)
- **Cache/Task Queue**: Redis 7 + Celery
- **Auth**: GitHub OAuth2 + JWT (python-jose)
- **Migration**: Alembic
- **Python**: 3.11+

### Frontend
- **Framework**: React 19 + TypeScript + Vite 7
- **Styling**: Tailwind CSS v3.4.19 + shadcn/ui
- **Charts**: Recharts
- **State**: Zustand
- **Data Fetching**: TanStack Query (React Query)
- **Routing**: React Router DOM v7 (HashRouter)

### Infrastructure
- **Orchestration**: Docker Compose
- **Proxy**: Nginx (reverse proxy)
- **Task Worker**: Celery + Redis

---

## 3. Database Schema

### 3.1 Users Table
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    avatar_url TEXT,
    name VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 3.2 Organizations Table
```sql
CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    github_org_id BIGINT UNIQUE,
    avatar_url TEXT,
    plan VARCHAR(50) DEFAULT 'free', -- free, pro, enterprise
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 3.3 Organization Members Table
```sql
CREATE TABLE organization_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'member', -- owner, admin, member
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, user_id)
);
```

### 3.4 Projects Table
```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    description TEXT,
    github_repo VARCHAR(500), -- owner/repo format
    github_repo_id BIGINT,
    default_branch VARCHAR(255) DEFAULT 'main',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, slug)
);
```

### 3.5 Scans Table
```sql
CREATE TABLE scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    status VARCHAR(50) DEFAULT 'pending', -- pending, running, completed, failed
    commit_sha VARCHAR(40),
    branch VARCHAR(255),
    risk_score DECIMAL(3,2), -- 0.00 to 1.00
    risk_vector_90d JSONB, -- 90-D risk vector
    findings_count INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    medium_count INTEGER DEFAULT 0,
    low_count INTEGER DEFAULT 0,
    info_count INTEGER DEFAULT 0,
    baseline_status VARCHAR(50), -- improved, stable, degraded, fractured
    triggered_by VARCHAR(50) DEFAULT 'manual', -- manual, webhook, scheduled
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 3.6 Findings Table
```sql
CREATE TABLE findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id UUID REFERENCES scans(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    rule_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    severity VARCHAR(50) NOT NULL, -- critical, high, medium, low, info
    confidence VARCHAR(50), -- high, medium, low
    category VARCHAR(255), -- injection, xss, secrets, etc.
    file_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    code_snippet TEXT,
    remediation TEXT,
    cwe_ids VARCHAR(50)[], -- CWE identifiers
    owasp_category VARCHAR(100),
    status VARCHAR(50) DEFAULT 'open', -- open, fixed, false_positive, accepted
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 3.7 Baselines Table
```sql
CREATE TABLE baselines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    scan_id UUID REFERENCES scans(id),
    risk_score DECIMAL(3,2),
    risk_vector_90d JSONB,
    findings_distribution JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 3.8 Webhook Events Table
```sql
CREATE TABLE webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    event_type VARCHAR(100) NOT NULL, -- push, pull_request
    github_delivery VARCHAR(255),
    payload JSONB,
    processed BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 4. API Endpoints

### 4.1 Authentication
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/auth/github` | Initiate GitHub OAuth flow |
| GET | `/api/v1/auth/github/callback` | GitHub OAuth callback |
| POST | `/api/v1/auth/refresh` | Refresh JWT token |
| GET | `/api/v1/auth/me` | Get current user |

### 4.2 Organizations
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/orgs` | List user's organizations |
| POST | `/api/v1/orgs` | Create organization |
| GET | `/api/v1/orgs/{id}` | Get organization details |
| PUT | `/api/v1/orgs/{id}` | Update organization |
| DELETE | `/api/v1/orgs/{id}` | Delete organization |
| GET | `/api/v1/orgs/{id}/members` | List members |
| POST | `/api/v1/orgs/{id}/members` | Add member |
| DELETE | `/api/v1/orgs/{id}/members/{user_id}` | Remove member |

### 4.3 Projects
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/projects` | List projects (with org filter) |
| POST | `/api/v1/projects` | Create project |
| GET | `/api/v1/projects/{id}` | Get project details |
| PUT | `/api/v1/projects/{id}` | Update project |
| DELETE | `/api/v1/projects/{id}` | Delete project |
| GET | `/api/v1/projects/{id}/risk-trend` | Risk score trend over time |
| GET | `/api/v1/projects/{id}/baseline` | Get baseline comparison |

### 4.4 Scans
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/scans` | List scans (with project filter) |
| POST | `/api/v1/scans` | Trigger new scan |
| GET | `/api/v1/scans/{id}` | Get scan details |
| DELETE | `/api/v1/scans/{id}` | Delete scan |
| GET | `/api/v1/scans/{id}/findings` | Get scan findings |
| GET | `/api/v1/scans/{id}/export/sarif` | Export scan as SARIF |

### 4.5 Findings
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/findings` | List findings (project/scan filter, sortable) |
| GET | `/api/v1/findings/{id}` | Get finding details |
| PUT | `/api/v1/findings/{id}` | Update finding status |
| GET | `/api/v1/findings/stats` | Finding statistics |

### 4.6 Webhooks
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/webhooks/github` | GitHub webhook receiver |

### 4.7 Health
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/health/db` | Database health |
| GET | `/api/v1/health/redis` | Redis health |

---

## 5. Data Models (Pydantic)

### 5.1 User
```python
class UserBase(BaseModel):
    username: str
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None

class User(UserBase):
    id: UUID
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True
```

### 5.2 Organization
```python
class OrganizationBase(BaseModel):
    name: str
    slug: str

class Organization(OrganizationBase):
    id: UUID
    plan: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class OrganizationWithMembers(Organization):
    members: List[OrganizationMember]
```

### 5.3 Project
```python
class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None
    github_repo: Optional[str] = None

class Project(ProjectBase):
    id: UUID
    slug: str
    organization_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class ProjectWithStats(Project):
    latest_scan: Optional[Scan] = None
    risk_trend: List[RiskPoint] = []
```

### 5.4 Scan
```python
class ScanBase(BaseModel):
    project_id: UUID
    commit_sha: Optional[str] = None
    branch: Optional[str] = "main"

class Scan(ScanBase):
    id: UUID
    status: str
    risk_score: Optional[float] = None
    risk_vector_90d: Optional[dict] = None
    findings_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    info_count: int
    baseline_status: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True
```

### 5.5 Finding
```python
class FindingBase(BaseModel):
    rule_id: str
    title: str
    severity: str  # critical, high, medium, low, info
    file_path: Optional[str] = None
    line_start: Optional[int] = None

class Finding(FindingBase):
    id: UUID
    scan_id: UUID
    project_id: UUID
    description: Optional[str] = None
    confidence: Optional[str] = None
    category: Optional[str] = None
    code_snippet: Optional[str] = None
    remediation: Optional[str] = None
    cwe_ids: Optional[List[str]] = None
    owasp_category: Optional[str] = None
    status: str
    created_at: datetime
    
    class Config:
        from_attributes = True
```

### 5.6 RiskPoint
```python
class RiskPoint(BaseModel):
    timestamp: datetime
    risk_score: float
    scan_id: UUID
    commit_sha: Optional[str] = None
```

---

## 6. 90-D Risk Vector Structure

The 90-D risk vector is a multi-dimensional risk assessment with the following dimensions:

```json
{
  "D01_injection": 0.15,
  "D02_broken_auth": 0.08,
  "D03_sensitive_data": 0.22,
  "D04_xxe": 0.05,
  "D05_access_control": 0.12,
  "D06_security_misconfig": 0.18,
  "D07_xss": 0.25,
  "D08_insecure_deserialization": 0.03,
  "D09_known_vulns": 0.35,
  "D10_logging_monitoring": 0.42,
  "D11_crypto_failures": 0.19,
  "D12_ssrf": 0.07,
  "D13_file_upload": 0.11,
  "D14_command_injection": 0.14,
  "D15_race_conditions": 0.02,
  "D16_api_security": 0.28,
  "D17_secrets_management": 0.45,
  "D18_dependency_management": 0.33,
  "D19_code_quality": 0.21,
  "D20_error_handling": 0.16,
  "D21_session_management": 0.09,
  "D22_input_validation": 0.27,
  "D23_authentication": 0.13,
  "D24_authorization": 0.17,
  "D25_data_integrity": 0.06,
  "D26_network_security": 0.23,
  "D27_container_security": 0.31,
  "D28_cloud_security": 0.29,
  "D29_iac_security": 0.20,
  "D30_supply_chain": 0.38,
  "overall_risk_score": 0.24
}
```

---

## 7. Baseline Comparison Logic

```
IMPROVED:   Overall risk score decreased by > 10% from baseline
STABLE:     Overall risk score changed by <= 10% from baseline
DEGRADED:   Overall risk score increased by > 10% from baseline
FRACTURED:  Critical or High severity findings count increased by > 50%
```

---

## 8. SARIF Export Format

Follows SARIF v2.1.0 specification. Export includes:
- Tool information (Omni-Auditor)
- All findings as SARIF results
- Code locations with file paths and line numbers
- Severity mappings: critical/error, high/error, medium/warning, low/note, info/note

---

## 9. Frontend Routes

| Route | Page | Description |
|-------|------|-------------|
| `/` | Landing | Marketing page |
| `/dashboard` | Dashboard | Project list, risk overview |
| `/projects/:id` | Project Detail | Scan history, trend chart |
| `/scans/:id` | Scan Detail | Findings table, 90-D vector, SARIF export |
| `/settings` | Settings | Team, billing, integrations |

---

## 10. Environment Variables

```env
# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/omniauditor

# Redis
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1

# GitHub OAuth
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret
GITHUB_WEBHOOK_SECRET=your_webhook_secret

# JWT
SECRET_KEY=your-super-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# Omni-Auditor
OMNI_AUDITOR_PATH=/app/omni-auditor

# Frontend URL (for CORS)
FRONTEND_URL=http://localhost:5173

# Environment
ENVIRONMENT=development
LOG_LEVEL=info
```

---

## 11. Docker Compose Services

| Service | Image | Ports | Description |
|---------|-------|-------|-------------|
| db | postgres:15-alpine | 5432 | PostgreSQL database |
| redis | redis:7-alpine | 6379 | Redis cache + message broker |
| backend | omni-auditor-backend (build) | 8000 | FastAPI application |
| frontend | omni-auditor-frontend (build) | 5173 | React dev server / nginx |
| nginx | nginx:alpine | 80, 443 | Reverse proxy |
| celery | omni-auditor-backend (build) | — | Celery worker |
| celery-beat | omni-auditor-backend (build) | — | Celery beat scheduler |
