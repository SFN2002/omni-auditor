# Omni-Auditor SaaS Dashboard — API Contract

Complete reference for all REST API endpoints. All endpoints are prefixed with `/api/v1`.

**Base URL**: `http://localhost/api/v1`

---

## Table of Contents

- [Authentication](#authentication)
- [Organizations](#organizations)
- [Projects](#projects)
- [Scans](#scans)
- [Findings](#findings)
- [Webhooks](#webhooks)
- [Health](#health)

---

## Authentication

All protected endpoints require a JWT token in the `Authorization` header:

```
Authorization: Bearer <jwt_token>
```

### Endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/api/v1/auth/github` | Initiates GitHub OAuth flow | No |
| `GET` | `/api/v1/auth/github/callback` | OAuth callback — returns JWT token and user | No |
| `POST` | `/api/v1/auth/refresh` | Refresh JWT token with new expiration | Yes |
| `GET` | `/api/v1/auth/me` | Returns current authenticated user | Yes |

---

### `GET /api/v1/auth/github`

Initiates the GitHub OAuth 2.0 authorization flow. Redirects the user to GitHub's authorization page.

**Response**: `302 Redirect` to GitHub authorization URL

---

### `GET /api/v1/auth/github/callback`

Handles the GitHub OAuth callback after user authorization.

**Query Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | Yes | Authorization code from GitHub |

**Response** — `200 OK`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
    "username": "devsecops-lead",
    "email": "security@example.com",
    "name": "Alex Security",
    "avatar_url": "https://avatars.githubusercontent.com/u/12345678?v=4",
    "is_active": true,
    "created_at": "2024-01-15T08:30:00Z"
  }
}
```

**Error Responses**:
- `400 Bad Request` — Missing authorization code
- `401 Unauthorized` — Invalid or expired code
- `503 Service Unavailable` — GitHub API error

---

### `POST /api/v1/auth/refresh`

Refreshes the JWT access token with a new expiration time.

**Request Headers**:

| Header | Value | Required |
|--------|-------|----------|
| `Authorization` | `Bearer <jwt_token>` | Yes |

**Response** — `200 OK`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

---

### `GET /api/v1/auth/me`

Returns the current authenticated user's profile.

**Request Headers**:

| Header | Value | Required |
|--------|-------|----------|
| `Authorization` | `Bearer <jwt_token>` | Yes |

**Response** — `200 OK`:

```json
{
  "id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
  "username": "devsecops-lead",
  "email": "security@example.com",
  "name": "Alex Security",
  "avatar_url": "https://avatars.githubusercontent.com/u/12345678?v=4",
  "is_active": true,
  "created_at": "2024-01-15T08:30:00Z",
  "updated_at": "2024-02-10T14:22:00Z"
}
```

---

## Organizations

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/api/v1/orgs` | List user's organizations | Yes |
| `POST` | `/api/v1/orgs` | Create organization | Yes |
| `GET` | `/api/v1/orgs/{id}` | Get organization details | Yes |
| `PUT` | `/api/v1/orgs/{id}` | Update organization | Yes |
| `DELETE` | `/api/v1/orgs/{id}` | Delete organization | Yes |
| `GET` | `/api/v1/orgs/{id}/members` | List members | Yes |
| `POST` | `/api/v1/orgs/{id}/members` | Add member | Yes |
| `DELETE` | `/api/v1/orgs/{id}/members/{user_id}` | Remove member | Yes |

---

### `GET /api/v1/orgs`

List all organizations the current user is a member of.

**Response** — `200 OK`:

```json
[
  {
    "id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
    "name": "Acme Corp Security",
    "slug": "acme-corp-security",
    "github_org_id": 87654321,
    "avatar_url": "https://avatars.githubusercontent.com/u/87654321?v=4",
    "plan": "pro",
    "created_at": "2024-01-15T08:30:00Z",
    "updated_at": "2024-01-15T08:30:00Z"
  }
]
```

---

### `POST /api/v1/orgs`

Create a new organization. The creator is automatically added as owner.

**Request Body**:

```json
{
  "name": "Acme Corp Security",
  "slug": "acme-corp-security",
  "github_org_id": 87654321,
  "avatar_url": "https://avatars.githubusercontent.com/u/87654321?v=4"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Organization display name |
| `slug` | string | Yes | URL-friendly identifier (auto-generated if not provided) |
| `github_org_id` | integer | No | Linked GitHub organization ID |
| `avatar_url` | string | No | Organization avatar image URL |

**Response** — `201 Created`:

```json
{
  "id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "name": "Acme Corp Security",
  "slug": "acme-corp-security",
  "github_org_id": 87654321,
  "avatar_url": "https://avatars.githubusercontent.com/u/87654321?v=4",
  "plan": "free",
  "created_at": "2024-02-14T10:00:00Z",
  "updated_at": "2024-02-14T10:00:00Z"
}
```

**Error Responses**:
- `409 Conflict` — Organization with slug already exists

---

### `GET /api/v1/orgs/{id}`

Get organization details including member and project counts.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Organization ID |

**Response** — `200 OK`:

```json
{
  "id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "name": "Acme Corp Security",
  "slug": "acme-corp-security",
  "github_org_id": 87654321,
  "avatar_url": "https://avatars.githubusercontent.com/u/87654321?v=4",
  "plan": "pro",
  "member_count": 5,
  "project_count": 3,
  "created_at": "2024-01-15T08:30:00Z",
  "updated_at": "2024-01-15T08:30:00Z"
}
```

**Error Responses**:
- `404 Not Found` — Organization not found
- `403 Forbidden` — User is not a member

---

### `PUT /api/v1/orgs/{id}`

Update an organization. Only owners and admins can update.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Organization ID |

**Request Body**:

```json
{
  "name": "Acme Corp Security Team",
  "slug": "acme-corp-security-team"
}
```

**Response** — `200 OK`:

```json
{
  "id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "name": "Acme Corp Security Team",
  "slug": "acme-corp-security-team",
  "github_org_id": 87654321,
  "avatar_url": "https://avatars.githubusercontent.com/u/87654321?v=4",
  "plan": "pro",
  "created_at": "2024-01-15T08:30:00Z",
  "updated_at": "2024-02-14T12:00:00Z"
}
```

**Error Responses**:
- `404 Not Found` — Organization not found
- `403 Forbidden` — Only owners and admins can update

---

### `DELETE /api/v1/orgs/{id}`

Delete an organization and all its data. Only owners can delete.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Organization ID |

**Response** — `204 No Content`

**Error Responses**:
- `404 Not Found` — Organization not found
- `403 Forbidden` — Only owners can delete

---

### `GET /api/v1/orgs/{id}/members`

List all members of an organization with user details.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Organization ID |

**Response** — `200 OK`:

```json
[
  {
    "id": "c2eebc99-9c0b-4ef8-bb6d-6bb9bd380a33",
    "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
    "user_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
    "role": "owner",
    "user": {
      "id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
      "username": "devsecops-lead",
      "email": "security@example.com",
      "name": "Alex Security",
      "avatar_url": "https://avatars.githubusercontent.com/u/12345678?v=4",
      "is_active": true,
      "created_at": "2024-01-15T08:30:00Z"
    },
    "created_at": "2024-01-15T08:30:00Z"
  }
]
```

---

### `POST /api/v1/orgs/{id}/members`

Add a member to an organization. Only owners and admins can add members.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Organization ID |

**Request Body**:

```json
{
  "user_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
  "role": "member"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | UUID | Yes | User to add |
| `role` | string | No | `owner`, `admin`, or `member` (default: `member`) |

**Response** — `201 Created`:

```json
{
  "id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "user_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
  "role": "member",
  "created_at": "2024-02-14T12:00:00Z"
}
```

**Error Responses**:
- `404 Not Found` — User not found
- `409 Conflict` — User is already a member
- `403 Forbidden` — Only owners and admins can add members

---

### `DELETE /api/v1/orgs/{id}/members/{user_id}`

Remove a member from an organization. Owners can remove anyone; members can only remove themselves. Cannot remove the last owner.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Organization ID |
| `user_id` | UUID | Member user ID to remove |

**Response** — `204 No Content`

**Error Responses**:
- `404 Not Found` — Member not found
- `403 Forbidden` — Insufficient permissions
- `400 Bad Request` — Cannot remove the last owner

---

## Projects

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/api/v1/projects` | List projects (paginated, org filter) | Yes |
| `POST` | `/api/v1/projects` | Create project | Yes |
| `GET` | `/api/v1/projects/{id}` | Get project with latest scan and risk trend | Yes |
| `PUT` | `/api/v1/projects/{id}` | Update project | Yes |
| `DELETE` | `/api/v1/projects/{id}` | Delete (soft-delete) project | Yes |
| `GET` | `/api/v1/projects/{id}/risk-trend` | Risk trend data over time | Yes |
| `GET` | `/api/v1/projects/{id}/baseline` | Baseline comparison data | Yes |

---

### `GET /api/v1/projects`

List all projects with optional organization filter and pagination.

**Query Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `org_id` | UUID | No | — | Filter by organization ID |
| `page` | integer | No | `1` | Page number (min: 1) |
| `page_size` | integer | No | `20` | Items per page (min: 1, max: 100) |

**Response** — `200 OK`:

```json
{
  "items": [
    {
      "id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
      "name": "Web API Service",
      "slug": "web-api",
      "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
      "description": "Main REST API gateway — Python/FastAPI microservice.",
      "github_repo": "acme-corp/web-api",
      "default_branch": "main",
      "is_active": true,
      "created_at": "2024-01-17T10:00:00Z",
      "updated_at": "2024-01-17T10:00:00Z"
    }
  ],
  "total": 3,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

---

### `POST /api/v1/projects`

Create a new project within an organization.

**Request Body**:

```json
{
  "name": "Web API Service",
  "description": "Main REST API gateway",
  "github_repo": "acme-corp/web-api",
  "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "default_branch": "main"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Project display name |
| `description` | string | No | Project description |
| `github_repo` | string | No | GitHub repo in `owner/repo` format |
| `organization_id` | UUID | Yes | Parent organization ID |
| `default_branch` | string | No | Default branch (default: `main`) |

**Response** — `201 Created`:

```json
{
  "id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "name": "Web API Service",
  "slug": "web-api",
  "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "description": "Main REST API gateway",
  "github_repo": "acme-corp/web-api",
  "default_branch": "main",
  "is_active": true,
  "created_at": "2024-02-14T12:00:00Z",
  "updated_at": "2024-02-14T12:00:00Z"
}
```

**Error Responses**:
- `409 Conflict` — Project with slug already exists in organization

---

### `GET /api/v1/projects/{id}`

Get a project with its latest scan and risk trend data.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Project ID |

**Response** — `200 OK`:

```json
{
  "id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "name": "Web API Service",
  "slug": "web-api",
  "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "description": "Main REST API gateway",
  "github_repo": "acme-corp/web-api",
  "github_repo_id": 111111111,
  "default_branch": "main",
  "is_active": true,
  "created_at": "2024-01-17T10:00:00Z",
  "updated_at": "2024-01-17T10:00:00Z",
  "latest_scan": {
    "id": "44444444-4444-4444-4444-444444444444",
    "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
    "status": "completed",
    "commit_sha": "d4e5f6a7b8c9012345678901234567890123abcdef",
    "branch": "main",
    "risk_score": 0.81,
    "risk_vector_90d": { "D01_injection": 0.92, "overall_risk_score": 0.81 },
    "findings_count": 15,
    "critical_count": 3,
    "high_count": 5,
    "medium_count": 4,
    "low_count": 2,
    "info_count": 1,
    "baseline_status": "degraded",
    "triggered_by": "webhook",
    "started_at": "2024-02-11T14:04:00Z",
    "completed_at": "2024-02-11T14:18:00Z",
    "created_at": "2024-02-11T14:00:00Z"
  },
  "risk_trend": [
    {
      "timestamp": "2024-01-17T10:05:00Z",
      "risk_score": 0.72,
      "scan_id": "11111111-1111-1111-1111-111111111111",
      "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd"
    },
    {
      "timestamp": "2024-01-24T10:03:00Z",
      "risk_score": 0.65,
      "scan_id": "22222222-2222-2222-2222-222222222222",
      "commit_sha": "b2c3d4e5f6a7890123456789012345678901abcde"
    }
  ]
}
```

---

### `PUT /api/v1/projects/{id}`

Update a project. Only provided fields are updated.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Project ID |

**Request Body**:

```json
{
  "name": "Web API Service v2",
  "description": "Updated description",
  "github_repo": "acme-corp/web-api-v2",
  "default_branch": "develop"
}
```

**Response** — `200 OK`:

```json
{
  "id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "name": "Web API Service v2",
  "slug": "web-api-service-v2",
  "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
  "description": "Updated description",
  "github_repo": "acme-corp/web-api-v2",
  "default_branch": "develop",
  "is_active": true,
  "created_at": "2024-01-17T10:00:00Z",
  "updated_at": "2024-02-14T12:30:00Z"
}
```

---

### `DELETE /api/v1/projects/{id}`

Soft-delete a project (sets `is_active` to `false`).

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Project ID |

**Response** — `204 No Content`

---

### `GET /api/v1/projects/{id}/risk-trend`

Get risk score trend data for charting.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Project ID |

**Query Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `points` | integer | No | `30` | Number of data points (max: 100) |

**Response** — `200 OK`:

```json
[
  {
    "timestamp": "2024-01-17T10:05:00Z",
    "risk_score": 0.72,
    "scan_id": "11111111-1111-1111-1111-111111111111",
    "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd"
  },
  {
    "timestamp": "2024-01-24T10:03:00Z",
    "risk_score": 0.65,
    "scan_id": "22222222-2222-2222-2222-222222222222",
    "commit_sha": "b2c3d4e5f6a7890123456789012345678901abcde"
  },
  {
    "timestamp": "2024-02-11T14:04:00Z",
    "risk_score": 0.81,
    "scan_id": "44444444-4444-4444-4444-444444444444",
    "commit_sha": "d4e5f6a7b8c9012345678901234567890123abcdef"
  }
]
```

---

### `GET /api/v1/projects/{id}/baseline`

Get baseline comparison data. Auto-creates baseline if none exists.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Project ID |

**Response** — `200 OK` (status: `improved`, `stable`, `degraded`, `fractured`):

```json
{
  "has_baseline": true,
  "has_scan": true,
  "status": "degraded",
  "baseline_risk_score": 0.58,
  "current_risk_score": 0.81,
  "percent_change": 39.66,
  "baseline_distribution": {
    "critical": 1,
    "high": 2,
    "medium": 2,
    "low": 2,
    "info": 1
  },
  "current_distribution": {
    "critical": 3,
    "high": 5,
    "medium": 4,
    "low": 2,
    "info": 1
  },
  "latest_scan_id": "44444444-4444-4444-4444-444444444444",
  "latest_commit_sha": "d4e5f6a7b8c9012345678901234567890123abcdef"
}
```

---

## Scans

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/api/v1/scans` | List scans (paginated, filters) | Yes |
| `POST` | `/api/v1/scans` | Trigger new scan | Yes |
| `GET` | `/api/v1/scans/{id}` | Get scan details | Yes |
| `DELETE` | `/api/v1/scans/{id}` | Delete scan | Yes |
| `GET` | `/api/v1/scans/{id}/findings` | Get scan findings | Yes |
| `GET` | `/api/v1/scans/{id}/export/sarif` | Export SARIF v2.1.0 | Yes |

---

### `GET /api/v1/scans`

List scans with optional project and status filters.

**Query Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `project_id` | UUID | No | — | Filter by project ID |
| `status` | string | No | — | Filter by status: `pending`, `running`, `completed`, `failed` |
| `page` | integer | No | `1` | Page number |
| `page_size` | integer | No | `20` | Items per page |

**Response** — `200 OK`:

```json
{
  "items": [
    {
      "id": "44444444-4444-4444-4444-444444444444",
      "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
      "status": "completed",
      "commit_sha": "d4e5f6a7b8c9012345678901234567890123abcdef",
      "branch": "main",
      "risk_score": 0.81,
      "risk_vector_90d": { "overall_risk_score": 0.81 },
      "findings_count": 15,
      "critical_count": 3,
      "high_count": 5,
      "medium_count": 4,
      "low_count": 2,
      "info_count": 1,
      "baseline_status": "degraded",
      "triggered_by": "webhook",
      "started_at": "2024-02-11T14:04:00Z",
      "completed_at": "2024-02-11T14:18:00Z",
      "created_at": "2024-02-11T14:00:00Z"
    }
  ],
  "total": 10,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

---

### `POST /api/v1/scans`

Trigger a new scan for a project. Creates a pending scan and enqueues a Celery task.

**Request Body**:

```json
{
  "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "commit_sha": "e5f6a7b8c9d0123456789012345678901234abcdef",
  "branch": "main",
  "triggered_by": "manual"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project_id` | UUID | Yes | Project to scan |
| `commit_sha` | string | No | Git commit SHA to scan |
| `branch` | string | No | Git branch (default: project's default branch) |
| `triggered_by` | string | No | `manual`, `webhook`, or `scheduled` |

**Response** — `201 Created`:

```json
{
  "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "status": "pending",
  "commit_sha": "e5f6a7b8c9d0123456789012345678901234abcdef",
  "branch": "main",
  "triggered_by": "manual",
  "created_at": "2024-02-14T12:30:00Z"
}
```

**Error Responses**:
- `404 Not Found` — Project not found or inactive
- `503 Service Unavailable` — Celery not available

---

### `GET /api/v1/scans/{id}`

Get detailed information about a scan.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Scan ID |

**Response** — `200 OK`:

```json
{
  "id": "44444444-4444-4444-4444-444444444444",
  "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "status": "completed",
  "commit_sha": "d4e5f6a7b8c9012345678901234567890123abcdef",
  "branch": "main",
  "risk_score": 0.81,
  "risk_vector_90d": { "overall_risk_score": 0.81 },
  "findings_count": 15,
  "critical_count": 3,
  "high_count": 5,
  "medium_count": 4,
  "low_count": 2,
  "info_count": 1,
  "baseline_status": "degraded",
  "triggered_by": "webhook",
  "started_at": "2024-02-11T14:04:00Z",
  "completed_at": "2024-02-11T14:18:00Z",
  "created_at": "2024-02-11T14:00:00Z"
}
```

---

### `DELETE /api/v1/scans/{id}`

Delete a scan and all its associated findings.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Scan ID |

**Response** — `204 No Content`

---

### `GET /api/v1/scans/{id}/findings`

Get all findings for a specific scan, sorted by severity.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Scan ID |

**Query Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | integer | No | `1` | Page number |
| `page_size` | integer | No | `20` | Items per page |

**Response** — `200 OK`:

```json
{
  "items": [
    {
      "id": "f1eebc99-9c0b-4ef8-bb6d-6bb9bd380a55",
      "scan_id": "44444444-4444-4444-4444-444444444444",
      "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
      "rule_id": "SQL-INJ-004",
      "title": "Second-Order SQL Injection",
      "description": "Data retrieved from the database is used in subsequent queries without re-parameterization.",
      "severity": "critical",
      "confidence": "medium",
      "category": "injection",
      "file_path": "src/reports/aggregator.py",
      "line_start": 42,
      "line_end": 50,
      "code_snippet": "user_pref = await db.fetch_one(...)\nresult = await db.fetch_all(f\"SELECT * FROM data WHERE category = '{user_pref}'\")",
      "remediation": "Always use parameterized queries even for data retrieved from the database.",
      "cwe_ids": ["CWE-89"],
      "owasp_category": "A03:2021-Injection",
      "status": "open",
      "created_at": "2024-02-11T14:00:00Z",
      "updated_at": "2024-02-11T14:00:00Z"
    }
  ],
  "total": 15,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

---

### `GET /api/v1/scans/{id}/export/sarif`

Export scan findings as SARIF v2.1.0 JSON.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Scan ID |

**Response** — `200 OK` (SARIF v2.1.0):

```json
{
  "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
  "version": "2.1.0",
  "runs": [
    {
      "tool": {
        "driver": {
          "name": "Omni-Auditor",
          "informationUri": "https://github.com/omniauditor/omni-auditor",
          "version": "1.0.0",
          "rules": [
            {
              "id": "SQL-INJ-004",
              "name": "Second-Order SQL Injection",
              "shortDescription": { "text": "Second-Order SQL Injection" },
              "fullDescription": { "text": "Data retrieved from the database is used in subsequent queries..." },
              "defaultConfiguration": { "level": "error" },
              "properties": {
                "category": "injection",
                "severity": "critical",
                "confidence": "medium",
                "owasp": "A03:2021-Injection"
              },
              "help": { "text": "..." },
              "relationships": [
                { "target": { "id": "CWE-89", "index": 0, "toolComponent": { "index": 0 } }, "kinds": ["relevant"] }
              ]
            }
          ],
          "taxa": [
            {
              "name": "CWE",
              "shortDescription": { "text": "Common Weakness Enumeration" }
            }
          ]
        }
      },
      "results": [
        {
          "ruleId": "SQL-INJ-004",
          "ruleIndex": 0,
          "level": "error",
          "message": { "text": "Second-Order SQL Injection" },
          "locations": [
            {
              "physicalLocation": {
                "artifactLocation": { "uri": "src/reports/aggregator.py" },
                "region": {
                  "startLine": 42,
                  "endLine": 50,
                  "snippet": { "text": "user_pref = await db.fetch_one(...)" }
                }
              }
            }
          ]
        }
      ],
      "automationDetails": {
        "id": "omni-auditor/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "guid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
      },
      "properties": {
        "scanId": "...",
        "projectId": "...",
        "commitSha": "...",
        "riskScore": 0.81,
        "triggeredBy": "webhook",
        "baselineStatus": "degraded"
      }
    }
  ]
}
```

---

## Findings

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/api/v1/findings` | List findings (paginated, filterable) | Yes |
| `GET` | `/api/v1/findings/{id}` | Get finding details | Yes |
| `PUT` | `/api/v1/findings/{id}` | Update finding status | Yes |
| `GET` | `/api/v1/findings/stats` | Aggregated statistics | Yes |

---

### `GET /api/v1/findings`

List findings with comprehensive filtering, sorting, and pagination.

**Query Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `severity` | string | No | — | Filter: `critical`, `high`, `medium`, `low`, `info` |
| `status` | string | No | — | Filter: `open`, `fixed`, `false_positive`, `accepted` |
| `project_id` | UUID | No | — | Filter by project |
| `scan_id` | UUID | No | — | Filter by scan |
| `category` | string | No | — | Filter by category (e.g., `injection`, `xss`) |
| `sort_by` | string | No | `created_at` | Sort field: `created_at`, `severity`, `status`, `file_path`, `title`, `rule_id` |
| `sort_order` | string | No | `desc` | Sort order: `asc` or `desc` |
| `page` | integer | No | `1` | Page number |
| `page_size` | integer | No | `20` | Items per page |

**Response** — `200 OK`:

```json
{
  "items": [
    {
      "id": "f1eebc99-9c0b-4ef8-bb6d-6bb9bd380a55",
      "scan_id": "44444444-4444-4444-4444-444444444444",
      "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
      "rule_id": "SQL-INJ-004",
      "title": "Second-Order SQL Injection",
      "description": "Data retrieved from the database is used in subsequent queries without re-parameterization.",
      "severity": "critical",
      "confidence": "medium",
      "category": "injection",
      "file_path": "src/reports/aggregator.py",
      "line_start": 42,
      "line_end": 50,
      "code_snippet": "user_pref = await db.fetch_one(...)\nresult = await db.fetch_all(f\"SELECT * FROM data WHERE category = '{user_pref}'\")",
      "remediation": "Always use parameterized queries even for data retrieved from the database.",
      "cwe_ids": ["CWE-89"],
      "owasp_category": "A03:2021-Injection",
      "status": "open",
      "created_at": "2024-02-11T14:00:00Z",
      "updated_at": "2024-02-11T14:00:00Z"
    }
  ],
  "total": 50,
  "page": 1,
  "page_size": 20,
  "pages": 3
}
```

---

### `GET /api/v1/findings/{id}`

Get detailed information about a single finding.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Finding ID |

**Response** — `200 OK`:

```json
{
  "id": "f1eebc99-9c0b-4ef8-bb6d-6bb9bd380a55",
  "scan_id": "44444444-4444-4444-4444-444444444444",
  "project_id": "d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44",
  "rule_id": "SQL-INJ-004",
  "title": "Second-Order SQL Injection",
  "description": "Data retrieved from the database is used in subsequent queries without re-parameterization.",
  "severity": "critical",
  "confidence": "medium",
  "category": "injection",
  "file_path": "src/reports/aggregator.py",
  "line_start": 42,
  "line_end": 50,
  "code_snippet": "user_pref = await db.fetch_one(...)\nresult = await db.fetch_all(f\"SELECT * FROM data WHERE category = '{user_pref}'\")",
  "remediation": "Always use parameterized queries even for data retrieved from the database.",
  "cwe_ids": ["CWE-89"],
  "owasp_category": "A03:2021-Injection",
  "status": "open",
  "created_at": "2024-02-11T14:00:00Z",
  "updated_at": "2024-02-11T14:00:00Z"
}
```

---

### `PUT /api/v1/findings/{id}`

Update a finding's status (triage).

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Finding ID |

**Request Body**:

```json
{
  "status": "fixed"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | `open`, `fixed`, `false_positive`, or `accepted` |

**Response** — `200 OK`: Returns the updated finding object (same as `GET /findings/{id}`)

---

### `GET /api/v1/findings/stats`

Get aggregated finding statistics for the dashboard.

**Query Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `project_id` | UUID | No | Filter by project |
| `scan_id` | UUID | No | Filter by scan |

**Response** — `200 OK`:

```json
{
  "total": 50,
  "by_severity": {
    "critical": 6,
    "high": 16,
    "medium": 15,
    "low": 8,
    "info": 5
  },
  "by_status": {
    "open": 42,
    "fixed": 4,
    "false_positive": 2,
    "accepted": 2
  },
  "by_category": {
    "injection": 10,
    "xss": 5,
    "secrets": 4,
    "access_control": 6,
    "api_security": 5,
    "crypto_failures": 4,
    "ssrf": 3,
    "security_misconfig": 3,
    "error_handling": 2,
    "container_security": 2,
    "network_security": 1,
    "session_management": 2,
    "input_validation": 2,
    "logging_monitoring": 1
  },
  "recent_count_7d": 15,
  "recent_count_30d": 50
}
```

---

## Webhooks

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/api/v1/webhooks/github` | GitHub webhook receiver | Signature only |

---

### `POST /api/v1/webhooks/github`

Receive and process GitHub webhook events. Validates signature, stores event, and may trigger scans.

**Request Headers**:

| Header | Required | Description |
|--------|----------|-------------|
| `X-GitHub-Event` | Yes | Event type: `push`, `pull_request` |
| `X-Hub-Signature-256` | Yes* | SHA-256 signature (required if `GITHUB_WEBHOOK_SECRET` is set) |
| `X-GitHub-Delivery` | No | Unique delivery ID |

**Request Body**: GitHub webhook payload (JSON)

**Response** — `202 Accepted`:

```json
{
  "status": "accepted",
  "event_id": "e5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66",
  "event_type": "push",
  "scan_triggered": true,
  "scan_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "commit_sha": "e5f6a7b8c9d0123456789012345678901234abcdef"
}
```

Or when no scan is triggered:

```json
{
  "status": "accepted",
  "event_id": "e5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66",
  "event_type": "push",
  "scan_triggered": false
}
```

**Error Responses**:
- `400 Bad Request` — Missing event header or invalid JSON
- `401 Unauthorized` — Missing or invalid signature

---

## Health

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/api/v1/health` | Basic health check | No |
| `GET` | `/api/v1/health/db` | Database connectivity | No |
| `GET` | `/api/v1/health/redis` | Redis connectivity | No |

---

### `GET /api/v1/health`

Basic health check for load balancers and monitoring.

**Response** — `200 OK`:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "development"
}
```

---

### `GET /api/v1/health/db`

Verify database connectivity by executing a test query.

**Response** — `200 OK`:

```json
{
  "status": "ok",
  "database": "connected",
  "timestamp": "2024-02-14T12:35:00.123456+00:00"
}
```

Or on failure:

```json
{
  "status": "error",
  "database": "disconnected: <error details>",
  "timestamp": "2024-02-14T12:35:00.123456+00:00"
}
```

---

### `GET /api/v1/health/redis`

Verify Redis connectivity with a PING command.

**Response** — `200 OK`:

```json
{
  "status": "ok",
  "redis": "connected",
  "timestamp": "2024-02-14T12:35:00.123456+00:00"
}
```

Or on failure:

```json
{
  "status": "error",
  "redis": "disconnected: <error details>",
  "timestamp": "2024-02-14T12:35:00.123456+00:00"
}
```

---

## Common Error Responses

All endpoints may return these error responses:

### `401 Unauthorized`

```json
{
  "detail": "Not authenticated"
}
```

Returned when the JWT token is missing, expired, or invalid.

### `403 Forbidden`

```json
{
  "detail": "You do not have permission to perform this action"
}
```

Returned when the authenticated user lacks permission for the requested operation.

### `404 Not Found`

```json
{
  "detail": "Resource not found"
}
```

Returned when the requested resource does not exist.

### `422 Unprocessable Entity`

```json
{
  "detail": "Validation error",
  "errors": [
    {
      "loc": ["body", "name"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

Returned when request validation fails (missing fields, invalid format, etc.).

### `500 Internal Server Error`

```json
{
  "detail": "An internal server error occurred"
}
```

Returned for unexpected server errors. Check server logs for details.

---

## Data Types

### Severity Levels

| Level | Numeric Priority | SARIF Mapping |
|-------|-----------------|---------------|
| `critical` | 1 | `error` |
| `high` | 2 | `error` |
| `medium` | 3 | `warning` |
| `low` | 4 | `note` |
| `info` | 5 | `note` |

### Finding Status Values

| Status | Description |
|--------|-------------|
| `open` | Finding is active and unaddressed |
| `fixed` | Finding has been remediated |
| `false_positive` | Finding is not a real issue |
| `accepted` | Risk has been accepted |

### Scan Status Values

| Status | Description |
|--------|-------------|
| `pending` | Scan is queued but not started |
| `running` | Scan is currently executing |
| `completed` | Scan finished successfully |
| `failed` | Scan encountered an error |

### Baseline Status Values

| Status | Condition |
|--------|-----------|
| `improved` | Risk score decreased by > 10% from baseline |
| `stable` | Risk score changed by <= 10% from baseline |
| `degraded` | Risk score increased by > 10% from baseline |
| `fractured` | Critical or High findings increased by > 50% |

### Trigger Types

| Type | Description |
|------|-------------|
| `manual` | User-triggered via API or UI |
| `webhook` | Triggered by GitHub webhook |
| `scheduled` | Triggered by Celery Beat |
