# Omni-Auditor SaaS Dashboard — Frontend Integration Guide

This document describes how the React frontend integrates with the FastAPI backend.

---

## Technology Stack

| Technology | Purpose | Version |
|-----------|---------|---------|
| React | UI framework | 19 |
| TypeScript | Type safety | 5.x |
| Vite | Build tool / dev server | 7 |
| Tailwind CSS | Utility-first styling | 3.4.19 |
| shadcn/ui | Pre-built accessible components | latest |
| Recharts | Charting library (risk trends, 90-D vector) | latest |
| Zustand | Lightweight state management | latest |
| TanStack Query | Server state / data fetching | latest |
| React Router DOM | Client-side routing | v7 (HashRouter) |
| Axios | HTTP client | latest |

---

## API Client Setup

### TanStack Query Configuration

The frontend uses TanStack Query (React Query) for all server state management:

```typescript
// src/lib/queryClient.ts
import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,        // 5 minutes
      gcTime: 1000 * 60 * 30,          // 30 minutes (garbage collection)
      refetchOnWindowFocus: true,      // Refetch when user returns to tab
      retry: (failureCount, error: any) => {
        // Don't retry on 401/403
        if (error?.response?.status === 401) return false;
        if (error?.response?.status === 403) return false;
        return failureCount < 3;
      },
    },
  },
});
```

### Base URL

The API base URL is configured via the `VITE_API_URL` environment variable at build time:

```typescript
// src/lib/api.ts
import axios from 'axios';

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor: attach JWT token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('jwt_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: handle 401
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('jwt_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  },
);
```

---

## Authentication Flow

### Overview

```
+--------+                                    +----------+         +--------+
|  User  |                                    | Frontend |         | Backend|
+---+----+                                    +----+-----+         +---+----+
    |                                              |                    |
    |  Click "Sign in with GitHub"                 |                    |
    +---------------------------------------------->                    |
    |                                              |  GET /auth/github  |
    |                                              +------------------->
    |                                              |                    |
    |    302 Redirect to GitHub OAuth page         |                    |
    |<---------------------------------------------+                    |
    |                                              |                    |
    |  Authorize on GitHub...                      |                    |
    |  GitHub redirects to /auth/github/callback   |                    |
    +---------------------------------------------->                    |
    |                                              |  GET /auth/github/callback?code=...
    |                                              +------------------->
    |                                              |                    |
    |    JWT token + user profile in response      |                    |
    |<---------------------------------------------+                    |
    |                                              |                    |
    |  Store JWT in localStorage                   |                    |
    |  Redirect to /dashboard                      |                    |
    +---------------------------------------------->                    |
    |                                              |                    |
    |  All subsequent API calls include:           |                    |
    |  Authorization: Bearer <jwt_token>           |                    |
    |                                              |                    |
```

### Implementation

```typescript
// src/stores/authStore.ts
import { create } from 'zustand';

interface AuthState {
  token: string | null;
  user: User | null;
  isAuthenticated: boolean;
  setToken: (token: string) => void;
  setUser: (user: User) => void;
  logout: () => void;
}

interface User {
  id: string;
  username: string;
  email: string | null;
  name: string | null;
  avatar_url: string | null;
  is_active: boolean;
  created_at: string;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('jwt_token'),
  user: null,
  isAuthenticated: !!localStorage.getItem('jwt_token'),

  setToken: (token: string) => {
    localStorage.setItem('jwt_token', token);
    set({ token, isAuthenticated: true });
  },

  setUser: (user: User) => set({ user }),

  logout: () => {
    localStorage.removeItem('jwt_token');
    set({ token: null, user: null, isAuthenticated: false });
  },
}));
```

### Login Component

```tsx
// src/pages/Login.tsx
export function LoginPage() {
  const handleGitHubLogin = () => {
    window.location.href = '/api/v1/auth/github';
  };

  return (
    <div className="flex items-center justify-center min-h-screen">
      <button
        onClick={handleGitHubLogin}
        className="px-6 py-3 bg-gray-900 text-white rounded-lg"
      >
        Sign in with GitHub
      </button>
    </div>
  );
}
```

### OAuth Callback Handler

```tsx
// src/pages/OAuthCallback.tsx
import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';

export function OAuthCallbackPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setToken, setUser } = useAuthStore();

  useEffect(() => {
    const token = searchParams.get('token');
    const userJson = searchParams.get('user');

    if (token && userJson) {
      try {
        const user = JSON.parse(decodeURIComponent(userJson));
        setToken(token);
        setUser(user);
        navigate('/dashboard');
      } catch {
        navigate('/login?error=invalid_callback');
      }
    } else {
      navigate('/login?error=auth_failed');
    }
  }, [searchParams, navigate, setToken, setUser]);

  return <div>Authenticating...</div>;
}
```

> **Note**: In production, the callback data is passed via the backend redirect URL query params or a dedicated callback endpoint that sets a cookie and redirects to the frontend.

---

## State Management

### Zustand Store Structure

```typescript
// src/stores/
//   authStore.ts      — Authentication state (token, user)
//   orgStore.ts       — Selected organization context
//   projectStore.ts   — Selected project context
//   uiStore.ts        — UI state (sidebar, modals, theme)
```

```typescript
// src/stores/orgStore.ts
import { create } from 'zustand';

interface OrgState {
  selectedOrgId: string | null;
  setSelectedOrg: (orgId: string | null) => void;
}

export const useOrgStore = create<OrgState>((set) => ({
  selectedOrgId: null,
  setSelectedOrg: (orgId) => set({ selectedOrgId: orgId }),
}));
```

```typescript
// src/stores/projectStore.ts
import { create } from 'zustand';

interface ProjectState {
  selectedProjectId: string | null;
  setSelectedProject: (projectId: string | null) => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  selectedProjectId: null,
  setSelectedProject: (projectId) => set({ selectedProjectId: projectId }),
}));
```

```typescript
// src/stores/uiStore.ts
import { create } from 'zustand';

interface UIState {
  sidebarOpen: boolean;
  theme: 'light' | 'dark';
  toggleSidebar: () => void;
  setTheme: (theme: 'light' | 'dark') => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  theme: 'light',
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setTheme: (theme) => set({ theme }),
}));
```

---

## Page Routes

| Route | Page Component | API Dependencies | Description |
|-------|---------------|------------------|-------------|
| `/` | `LandingPage` | None | Marketing landing page |
| `/login` | `LoginPage` | `GET /auth/github` | GitHub sign-in |
| `/oauth/callback` | `OAuthCallbackPage` | Parses URL params | Handles OAuth callback |
| `/dashboard` | `DashboardPage` | `GET /projects`, `GET /findings/stats`, `GET /orgs` | Project list, risk cards, stats |
| `/projects/:id` | `ProjectDetailPage` | `GET /projects/:id`, `GET /projects/:id/risk-trend`, `GET /scans?project_id=...` | Scan history, trend chart, 90-D radar |
| `/scans/:id` | `ScanDetailPage` | `GET /scans/:id`, `GET /scans/:id/findings` | Findings table, SARIF export button |
| `/findings` | `FindingsPage` | `GET /findings`, `GET /findings/stats` | Global findings list with filters |
| `/settings` | `SettingsPage` | `GET /auth/me`, `GET /orgs`, `GET /orgs/:id/members` | Profile, org, team settings |

### Route Guard

```tsx
// src/components/ProtectedRoute.tsx
import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
```

---

## Data Flow

### Overview

```
User Interaction → React Query Hook → Axios Request → FastAPI
                                                       ↓
                                                PostgreSQL/Redis
                                                       ↓
UI Re-render ← Component State ← React Query Cache ← Response
      ↓
Recharts ← Transformed Data
```

### Example: Project Detail with Risk Trend

```tsx
// src/pages/ProjectDetail.tsx
import { useQuery } from '@tanstack/react-query';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';
import { api } from '@/lib/api';
import { useProjectStore } from '@/stores/projectStore';

function useProject(projectId: string) {
  return useQuery({
    queryKey: ['project', projectId],
    queryFn: async () => {
      const { data } = await api.get(`/projects/${projectId}`);
      return data;
    },
  });
}

function useRiskTrend(projectId: string) {
  return useQuery({
    queryKey: ['risk-trend', projectId],
    queryFn: async () => {
      const { data } = await api.get(`/projects/${projectId}/risk-trend`);
      return data;
    },
  });
}

export function ProjectDetailPage({ projectId }: { projectId: string }) {
  const { data: project, isLoading: projectLoading } = useProject(projectId);
  const { data: trend, isLoading: trendLoading } = useRiskTrend(projectId);

  if (projectLoading || trendLoading) return <div>Loading...</div>;

  return (
    <div>
      <h1>{project.name}</h1>
      <p>Risk Score: {project.latest_scan?.risk_score}</p>

      <LineChart width={800} height={300} data={trend}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
          dataKey="timestamp"
          tickFormatter={(ts) => new Date(ts).toLocaleDateString()}
        />
        <YAxis domain={[0, 1]} />
        <Tooltip />
        <Line
          type="monotone"
          dataKey="risk_score"
          stroke="#8884d8"
          strokeWidth={2}
        />
      </LineChart>
    </div>
  );
}
```

### Example: Findings List with Filters

```tsx
// src/pages/Findings.tsx
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useState } from 'react';

function useFindings(filters: Record<string, any>) {
  return useQuery({
    queryKey: ['findings', filters],
    queryFn: async () => {
      const { data } = await api.get('/findings', { params: filters });
      return data;
    },
  });
}

function useFindingStats(projectId?: string) {
  return useQuery({
    queryKey: ['finding-stats', projectId],
    queryFn: async () => {
      const { data } = await api.get('/findings/stats', {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return data;
    },
  });
}

export function FindingsPage() {
  const [severity, setSeverity] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();

  const { data: findings } = useFindings({ severity, status });
  const { data: stats } = useFindingStats();

  return (
    <div>
      {/* Severity filter buttons */}
      <div className="flex gap-2">
        {['critical', 'high', 'medium', 'low', 'info'].map((sev) => (
          <button key={sev} onClick={() => setSeverity(sev)}>
            {sev}: {stats?.by_severity?.[sev] || 0}
          </button>
        ))}
      </div>

      {/* Findings table */}
      <table>
        <thead>
          <tr>
            <th>Rule</th>
            <th>Title</th>
            <th>Severity</th>
            <th>Status</th>
            <th>File</th>
          </tr>
        </thead>
        <tbody>
          {findings?.items?.map((f: any) => (
            <tr key={f.id}>
              <td>{f.rule_id}</td>
              <td>{f.title}</td>
              <td>{f.severity}</td>
              <td>{f.status}</td>
              <td>{f.file_path}:{f.line_start}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

---

## Build & Deploy

### Development Mode

```bash
cd frontend
npm install
npm run dev
```

The dev server starts at `http://localhost:5173` with hot module replacement (HMR).

### Production Build

```bash
cd frontend
npm install
npm run build
```

Output is generated in the `dist/` directory. These are static files that can be served by any web server.

### Docker Build

```bash
# Build the frontend image
docker build -t omni-auditor-frontend ./frontend

# Or use docker-compose
docker-compose up -d frontend
```

The frontend Dockerfile uses a multi-stage build:

1. **Build stage** (`node:20-alpine`): Installs dependencies and builds the production bundle
2. **Serve stage** (`nginx:alpine`): Serves static files with nginx, proxies `/api/` to the backend

### Environment Variables

| Variable | Used In | Description |
|----------|---------|-------------|
| `VITE_API_URL` | Build time | Base URL for API requests (default: `http://localhost:8000`) |

> **Note**: Only variables prefixed with `VITE_` are available in the client-side code.

---

## File Structure

```
frontend/src/
├── main.tsx              # Entry point — renders <App />
├── App.tsx               # Root component with HashRouter routes
├── index.css             # Tailwind directives + global styles
│
├── components/
│   ├── ui/               # shadcn/ui components
│   ├── layout/
│   │   ├── Sidebar.tsx
│   │   ├── TopBar.tsx
│   │   └── AppLayout.tsx
│   ├── charts/
│   │   ├── RiskTrendChart.tsx
│   │   └── RiskVectorRadar.tsx
│   ├── findings/
│   │   ├── FindingTable.tsx
│   │   ├── FindingFilters.tsx
│   │   └── FindingDetail.tsx
│   └── scans/
│       ├── ScanCard.tsx
│       └── ScanTrigger.tsx
│
├── pages/
│   ├── LandingPage.tsx
│   ├── LoginPage.tsx
│   ├── OAuthCallbackPage.tsx
│   ├── DashboardPage.tsx
│   ├── ProjectDetailPage.tsx
│   ├── ScanDetailPage.tsx
│   ├── FindingsPage.tsx
│   └── SettingsPage.tsx
│
├── stores/
│   ├── authStore.ts
│   ├── orgStore.ts
│   ├── projectStore.ts
│   └── uiStore.ts
│
├── hooks/
│   ├── useProjects.ts
│   ├── useScans.ts
│   ├── useFindings.ts
│   ├── useOrganizations.ts
│   └── useAuth.ts
│
└── lib/
    ├── api.ts            # Axios instance with interceptors
    ├── queryClient.ts    # TanStack Query client config
    └── utils.ts          # General utilities
```

---

## Styling Guide

### Tailwind Configuration

The project uses Tailwind CSS v3.4.19 with shadcn/ui components. Key configuration:

```javascript
// tailwind.config.js
module.exports = {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Severity color palette
        severity: {
          critical: '#dc2626',  // red-600
          high: '#ea580c',      // orange-600
          medium: '#ca8a04',    // yellow-600
          low: '#16a34a',       // green-600
          info: '#2563eb',      // blue-600
        },
        // Baseline status colors
        baseline: {
          improved: '#16a34a',   // green
          stable: '#2563eb',     // blue
          degraded: '#ea580c',   // orange
          fractured: '#dc2626',  // red
        },
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};
```

### Common Component Patterns

```tsx
// Severity badge
function SeverityBadge({ severity }: { severity: string }) {
  const colorMap: Record<string, string> = {
    critical: 'bg-red-100 text-red-800',
    high: 'bg-orange-100 text-orange-800',
    medium: 'bg-yellow-100 text-yellow-800',
    low: 'bg-green-100 text-green-800',
    info: 'bg-blue-100 text-blue-800',
  };

  return (
    <span className={`px-2 py-1 rounded-full text-xs font-medium ${colorMap[severity]}`}>
      {severity}
    </span>
  );
}

// Status badge
function StatusBadge({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    open: 'bg-red-100 text-red-800',
    fixed: 'bg-green-100 text-green-800',
    false_positive: 'bg-gray-100 text-gray-800',
    accepted: 'bg-blue-100 text-blue-800',
  };

  return (
    <span className={`px-2 py-1 rounded-full text-xs font-medium ${colorMap[status]}`}>
      {status.replace('_', ' ')}
    </span>
  );
}
```

---

## API Integration Checklist

When building a new page or feature:

- [ ] Create a TanStack Query hook in `src/hooks/` for data fetching
- [ ] Add the API endpoint to the relevant backend router (if new)
- [ ] Use Zustand store for client-side state (selected items, UI state)
- [ ] Use React Query cache for server state (lists, details)
- [ ] Handle loading states with skeleton screens or spinners
- [ ] Handle error states with toast notifications
- [ ] Invalidate relevant query caches after mutations
- [ ] Export SARIF via direct download link or blob URL
