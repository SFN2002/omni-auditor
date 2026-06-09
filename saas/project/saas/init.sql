-- ═══════════════════════════════════════════════════════════════════════════
-- Omni-Auditor SaaS Dashboard — Database Initialization
-- Creates schema, indexes, default admin user, and comprehensive seed data
-- ═══════════════════════════════════════════════════════════════════════════

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 1. USERS TABLE                                                        │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS users (
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

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 2. ORGANIZATIONS TABLE                                                │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    github_org_id BIGINT UNIQUE,
    avatar_url TEXT,
    plan VARCHAR(50) DEFAULT 'free',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 3. ORGANIZATION MEMBERS TABLE                                         │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS organization_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'member',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, user_id)
);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 4. PROJECTS TABLE                                                     │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    description TEXT,
    github_repo VARCHAR(500),
    github_repo_id BIGINT,
    default_branch VARCHAR(255) DEFAULT 'main',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(organization_id, slug)
);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 5. SCANS TABLE                                                        │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    status VARCHAR(50) DEFAULT 'pending',
    commit_sha VARCHAR(40),
    branch VARCHAR(255),
    risk_score DECIMAL(3,2),
    risk_vector_90d JSONB,
    findings_count INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    medium_count INTEGER DEFAULT 0,
    low_count INTEGER DEFAULT 0,
    info_count INTEGER DEFAULT 0,
    baseline_status VARCHAR(50),
    triggered_by VARCHAR(50) DEFAULT 'manual',
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 6. FINDINGS TABLE                                                     │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id UUID REFERENCES scans(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    rule_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    severity VARCHAR(50) NOT NULL,
    confidence VARCHAR(50),
    category VARCHAR(255),
    file_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    code_snippet TEXT,
    remediation TEXT,
    cwe_ids VARCHAR(50)[],
    owasp_category VARCHAR(100),
    status VARCHAR(50) DEFAULT 'open',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 7. BASELINES TABLE                                                    │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS baselines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    scan_id UUID REFERENCES scans(id),
    risk_score DECIMAL(3,2),
    risk_vector_90d JSONB,
    findings_distribution JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ 8. WEBHOOK EVENTS TABLE                                               │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE TABLE IF NOT EXISTS webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    event_type VARCHAR(100) NOT NULL,
    github_delivery VARCHAR(255),
    payload JSONB,
    processed BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ INDEXES FOR FREQUENTLY QUERIED COLUMNS                                │
-- └─────────────────────────────────────────────────────────────────────────┘
CREATE INDEX IF NOT EXISTS idx_users_github_id ON users(github_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE INDEX IF NOT EXISTS idx_orgs_slug ON organizations(slug);
CREATE INDEX IF NOT EXISTS idx_orgs_plan ON organizations(plan);

CREATE INDEX IF NOT EXISTS idx_org_members_org_id ON organization_members(organization_id);
CREATE INDEX IF NOT EXISTS idx_org_members_user_id ON organization_members(user_id);

CREATE INDEX IF NOT EXISTS idx_projects_org_id ON projects(organization_id);
CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug);
CREATE INDEX IF NOT EXISTS idx_projects_active ON projects(is_active);

CREATE INDEX IF NOT EXISTS idx_scans_project_id ON scans(project_id);
CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);
CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scans_risk_score ON scans(risk_score);
CREATE INDEX IF NOT EXISTS idx_scans_baseline_status ON scans(baseline_status);

CREATE INDEX IF NOT EXISTS idx_findings_scan_id ON findings(scan_id);
CREATE INDEX IF NOT EXISTS idx_findings_project_id ON findings(project_id);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_rule_id ON findings(rule_id);
CREATE INDEX IF NOT EXISTS idx_findings_owasp ON findings(owasp_category);

CREATE INDEX IF NOT EXISTS idx_baselines_project_id ON baselines(project_id);

CREATE INDEX IF NOT EXISTS idx_webhook_events_project ON webhook_events(project_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_processed ON webhook_events(processed);

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ SEED DATA — Sample Organization, User, Projects, Scans, Findings     │
-- └─────────────────────────────────────────────────────────────────────────┘

-- ─── Sample User ─────────────────────────────────────────────────────────
INSERT INTO users (id, github_id, username, email, avatar_url, name, is_active, created_at, updated_at)
VALUES (
    'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
    12345678,
    'devsecops-lead',
    'security@example.com',
    'https://avatars.githubusercontent.com/u/12345678?v=4',
    'Alex Security',
    true,
    NOW() - INTERVAL '30 days',
    NOW() - INTERVAL '30 days'
);

-- ─── Sample Organization ─────────────────────────────────────────────────
INSERT INTO organizations (id, name, slug, github_org_id, avatar_url, plan, created_at, updated_at)
VALUES (
    'b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22',
    'Acme Corp Security',
    'acme-corp-security',
    87654321,
    'https://avatars.githubusercontent.com/u/87654321?v=4',
    'pro',
    NOW() - INTERVAL '30 days',
    NOW() - INTERVAL '30 days'
);

-- ─── Organization Membership ─────────────────────────────────────────────
INSERT INTO organization_members (id, organization_id, user_id, role, created_at)
VALUES (
    'c2eebc99-9c0b-4ef8-bb6d-6bb9bd380a33',
    'b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22',
    'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
    'owner',
    NOW() - INTERVAL '30 days'
);

-- ─── Sample Projects ─────────────────────────────────────────────────────
INSERT INTO projects (id, organization_id, name, slug, description, github_repo, github_repo_id, default_branch, is_active, created_at, updated_at)
VALUES
    ('d3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22',
     'Web API Service',
     'web-api',
     'Main REST API gateway — Python/FastAPI microservice handling authentication, rate limiting, and core business logic.',
     'acme-corp/web-api',
     111111111,
     'main',
     true,
     NOW() - INTERVAL '28 days',
     NOW() - INTERVAL '28 days'),

    ('e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22',
     'Mobile App Backend',
     'mobile-app',
     'Backend services for the mobile application — user profiles, push notifications, and real-time messaging.',
     'acme-corp/mobile-app',
     222222222,
     'develop',
     true,
     NOW() - INTERVAL '25 days',
     NOW() - INTERVAL '25 days'),

    ('f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22',
     'ML Inference Service',
     'ml-service',
     'Machine learning model inference pipeline — model serving, feature store, and prediction caching.',
     'acme-corp/ml-service',
     333333333,
     'main',
     true,
     NOW() - INTERVAL '20 days',
     NOW() - INTERVAL '20 days');

-- ─── Sample Scans (10 scans across 3 projects) ───────────────────────────
-- Project 1: web-api (4 scans)
INSERT INTO scans (id, project_id, status, commit_sha, branch, risk_score, risk_vector_90d, findings_count, critical_count, high_count, medium_count, low_count, info_count, baseline_status, triggered_by, started_at, completed_at, created_at)
VALUES
    ('11111111-1111-1111-1111-111111111111',
     'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'completed',
     'a1b2c3d4e5f6789012345678901234567890abcd',
     'main',
     0.72,
     '{"D01_injection": 0.85, "D02_broken_auth": 0.45, "D03_sensitive_data": 0.62, "D04_xxe": 0.12, "D05_access_control": 0.78, "D06_security_misconfig": 0.55, "D07_xss": 0.91, "D08_insecure_deserialization": 0.33, "D09_known_vulns": 0.67, "D10_logging_monitoring": 0.44, "D11_crypto_failures": 0.58, "D12_ssrf": 0.72, "D13_file_upload": 0.39, "D14_command_injection": 0.81, "D15_race_conditions": 0.15, "D16_api_security": 0.88, "D17_secrets_management": 0.76, "D18_dependency_management": 0.63, "D19_code_quality": 0.41, "D20_error_handling": 0.52, "D21_session_management": 0.48, "D22_input_validation": 0.93, "D23_authentication": 0.56, "D24_authorization": 0.74, "D25_data_integrity": 0.29, "D26_network_security": 0.37, "D27_container_security": 0.61, "D28_cloud_security": 0.43, "D29_iac_security": 0.25, "D30_supply_chain": 0.69, "overall_risk_score": 0.72}'::jsonb,
     12, 2, 4, 3, 2, 1,
     'stable',
     'manual',
     NOW() - INTERVAL '28 days' + INTERVAL '5 minutes',
     NOW() - INTERVAL '28 days' + INTERVAL '12 minutes',
     NOW() - INTERVAL '28 days'),

    ('22222222-2222-2222-2222-222222222222',
     'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'completed',
     'b2c3d4e5f6a7890123456789012345678901abcde',
     'main',
     0.65,
     '{"D01_injection": 0.72, "D02_broken_auth": 0.38, "D03_sensitive_data": 0.55, "D04_xxe": 0.08, "D05_access_control": 0.65, "D06_security_misconfig": 0.48, "D07_xss": 0.82, "D08_insecure_deserialization": 0.25, "D09_known_vulns": 0.58, "D10_logging_monitoring": 0.38, "D11_crypto_failures": 0.49, "D12_ssrf": 0.61, "D13_file_upload": 0.31, "D14_command_injection": 0.72, "D15_race_conditions": 0.11, "D16_api_security": 0.79, "D17_secrets_management": 0.68, "D18_dependency_management": 0.55, "D19_code_quality": 0.35, "D20_error_handling": 0.45, "D21_session_management": 0.41, "D22_input_validation": 0.85, "D23_authentication": 0.48, "D24_authorization": 0.62, "D25_data_integrity": 0.22, "D26_network_security": 0.30, "D27_container_security": 0.52, "D28_cloud_security": 0.36, "D29_iac_security": 0.19, "D30_supply_chain": 0.58, "overall_risk_score": 0.65}'::jsonb,
     10, 1, 3, 3, 2, 1,
     'improved',
     'webhook',
     NOW() - INTERVAL '21 days' + INTERVAL '3 minutes',
     NOW() - INTERVAL '21 days' + INTERVAL '10 minutes',
     NOW() - INTERVAL '21 days'),

    ('33333333-3333-3333-3333-333333333333',
     'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'completed',
     'c3d4e5f6a7b8901234567890123456789012abcdef',
     'feature/auth-refresh',
     0.58,
     '{"D01_injection": 0.61, "D02_broken_auth": 0.28, "D03_sensitive_data": 0.48, "D04_xxe": 0.06, "D05_access_control": 0.55, "D06_security_misconfig": 0.39, "D07_xss": 0.71, "D08_insecure_deserialization": 0.19, "D09_known_vulns": 0.49, "D10_logging_monitoring": 0.32, "D11_crypto_failures": 0.40, "D12_ssrf": 0.51, "D13_file_upload": 0.24, "D14_command_injection": 0.61, "D15_race_conditions": 0.08, "D16_api_security": 0.68, "D17_secrets_management": 0.58, "D18_dependency_management": 0.47, "D19_code_quality": 0.28, "D20_error_handling": 0.37, "D21_session_management": 0.33, "D22_input_validation": 0.74, "D23_authentication": 0.39, "D24_authorization": 0.52, "D25_data_integrity": 0.17, "D26_network_security": 0.24, "D27_container_security": 0.43, "D28_cloud_security": 0.29, "D29_iac_security": 0.14, "D30_supply_chain": 0.47, "overall_risk_score": 0.58}'::jsonb,
     8, 1, 2, 2, 2, 1,
     'improved',
     'manual',
     NOW() - INTERVAL '14 days' + INTERVAL '7 minutes',
     NOW() - INTERVAL '14 days' + INTERVAL '15 minutes',
     NOW() - INTERVAL '14 days'),

    ('44444444-4444-4444-4444-444444444444',
     'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'completed',
     'd4e5f6a7b8c9012345678901234567890123abcdef',
     'main',
     0.81,
     '{"D01_injection": 0.92, "D02_broken_auth": 0.52, "D03_sensitive_data": 0.71, "D04_xxe": 0.18, "D05_access_control": 0.85, "D06_security_misconfig": 0.62, "D07_xss": 0.95, "D08_insecure_deserialization": 0.41, "D09_known_vulns": 0.74, "D10_logging_monitoring": 0.51, "D11_crypto_failures": 0.65, "D12_ssrf": 0.79, "D13_file_upload": 0.45, "D14_command_injection": 0.88, "D15_race_conditions": 0.22, "D16_api_security": 0.93, "D17_secrets_management": 0.82, "D18_dependency_management": 0.71, "D19_code_quality": 0.48, "D20_error_handling": 0.58, "D21_session_management": 0.55, "D24_authorization": 0.81, "D22_input_validation": 0.96, "D23_authentication": 0.63, "D25_data_integrity": 0.35, "D26_network_security": 0.43, "D27_container_security": 0.68, "D28_cloud_security": 0.50, "D29_iac_security": 0.31, "D30_supply_chain": 0.77, "overall_risk_score": 0.81}'::jsonb,
     15, 3, 5, 4, 2, 1,
     'degraded',
     'webhook',
     NOW() - INTERVAL '3 days' + INTERVAL '4 minutes',
     NOW() - INTERVAL '3 days' + INTERVAL '18 minutes',
     NOW() - INTERVAL '3 days');

-- Project 2: mobile-app (3 scans)
INSERT INTO scans (id, project_id, status, commit_sha, branch, risk_score, risk_vector_90d, findings_count, critical_count, high_count, medium_count, low_count, info_count, baseline_status, triggered_by, started_at, completed_at, created_at)
VALUES
    ('55555555-5555-5555-5555-555555555555',
     'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'completed',
     'e5f6a7b8c9d0123456789012345678901234abcdef',
     'develop',
     0.48,
     '{"D01_injection": 0.52, "D02_broken_auth": 0.35, "D03_sensitive_data": 0.68, "D04_xxe": 0.09, "D05_access_control": 0.45, "D06_security_misconfig": 0.38, "D07_xss": 0.58, "D08_insecure_deserialization": 0.15, "D09_known_vulns": 0.42, "D10_logging_monitoring": 0.28, "D11_crypto_failures": 0.48, "D12_ssrf": 0.35, "D13_file_upload": 0.55, "D14_command_injection": 0.42, "D15_race_conditions": 0.12, "D16_api_security": 0.51, "D17_secrets_management": 0.62, "D18_dependency_management": 0.44, "D19_code_quality": 0.32, "D20_error_handling": 0.35, "D21_session_management": 0.58, "D22_input_validation": 0.48, "D23_authentication": 0.52, "D24_authorization": 0.41, "D25_data_integrity": 0.18, "D26_network_security": 0.22, "D27_container_security": 0.15, "D28_cloud_security": 0.25, "D29_iac_security": 0.12, "D30_supply_chain": 0.38, "overall_risk_score": 0.48}'::jsonb,
     7, 0, 2, 3, 1, 1,
     'stable',
     'manual',
     NOW() - INTERVAL '24 days' + INTERVAL '6 minutes',
     NOW() - INTERVAL '24 days' + INTERVAL '14 minutes',
     NOW() - INTERVAL '24 days'),

    ('66666666-6666-6666-6666-666666666666',
     'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'completed',
     'f6a7b8c9d0e1234567890123456789012345abcdef',
     'develop',
     0.44,
     '{"D01_injection": 0.48, "D02_broken_auth": 0.31, "D03_sensitive_data": 0.62, "D04_xxe": 0.07, "D05_access_control": 0.41, "D06_security_misconfig": 0.34, "D07_xss": 0.52, "D08_insecure_deserialization": 0.13, "D09_known_vulns": 0.38, "D10_logging_monitoring": 0.25, "D11_crypto_failures": 0.43, "D12_ssrf": 0.31, "D13_file_upload": 0.50, "D14_command_injection": 0.38, "D15_race_conditions": 0.10, "D16_api_security": 0.46, "D17_secrets_management": 0.56, "D18_dependency_management": 0.40, "D19_code_quality": 0.28, "D20_error_handling": 0.31, "D21_session_management": 0.52, "D22_input_validation": 0.43, "D23_authentication": 0.47, "D24_authorization": 0.37, "D25_data_integrity": 0.15, "D26_network_security": 0.19, "D27_container_security": 0.13, "D28_cloud_security": 0.21, "D29_iac_security": 0.10, "D30_supply_chain": 0.34, "overall_risk_score": 0.44}'::jsonb,
     6, 0, 1, 3, 1, 1,
     'improved',
     'scheduled',
     NOW() - INTERVAL '17 days' + INTERVAL '2 minutes',
     NOW() - INTERVAL '17 days' + INTERVAL '11 minutes',
     NOW() - INTERVAL '17 days'),

    ('77777777-7777-7777-7777-777777777777',
     'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'failed',
     'a7b8c9d0e1f2345678901234567890123456abcdef',
     'feature/push-notifications',
     NULL,
     NULL,
     0, 0, 0, 0, 0, 0,
     NULL,
     'webhook',
     NOW() - INTERVAL '5 days' + INTERVAL '3 minutes',
     NOW() - INTERVAL '5 days' + INTERVAL '7 minutes',
     NOW() - INTERVAL '5 days');

-- Project 3: ml-service (3 scans)
INSERT INTO scans (id, project_id, status, commit_sha, branch, risk_score, risk_vector_90d, findings_count, critical_count, high_count, medium_count, low_count, info_count, baseline_status, triggered_by, started_at, completed_at, created_at)
VALUES
    ('88888888-8888-8888-8888-888888888888',
     'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'completed',
     'b8c9d0e1f2a3456789012345678901234567abcdef',
     'main',
     0.35,
     '{"D01_injection": 0.32, "D02_broken_auth": 0.18, "D03_sensitive_data": 0.55, "D04_xxe": 0.04, "D05_access_control": 0.28, "D06_security_misconfig": 0.25, "D07_xss": 0.22, "D08_insecure_deserialization": 0.42, "D09_known_vulns": 0.35, "D10_logging_monitoring": 0.38, "D11_crypto_failures": 0.31, "D12_ssrf": 0.28, "D13_file_upload": 0.45, "D14_command_injection": 0.48, "D15_race_conditions": 0.08, "D16_api_security": 0.35, "D17_secrets_management": 0.52, "D18_dependency_management": 0.58, "D19_code_quality": 0.42, "D20_error_handling": 0.28, "D21_session_management": 0.15, "D22_input_validation": 0.32, "D23_authentication": 0.18, "D24_authorization": 0.22, "D25_data_integrity": 0.35, "D26_network_security": 0.18, "D27_container_security": 0.48, "D28_cloud_security": 0.55, "D29_iac_security": 0.62, "D30_supply_chain": 0.45, "overall_risk_score": 0.35}'::jsonb,
     5, 0, 1, 2, 1, 1,
     'stable',
     'manual',
     NOW() - INTERVAL '18 days' + INTERVAL '8 minutes',
     NOW() - INTERVAL '18 days' + INTERVAL '20 minutes',
     NOW() - INTERVAL '18 days'),

    ('99999999-9999-9999-9999-999999999999',
     'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'completed',
     'c9d0e1f2a3b4567890123456789012345678abcdef',
     'main',
     0.39,
     '{"D01_injection": 0.38, "D02_broken_auth": 0.22, "D03_sensitive_data": 0.58, "D04_xxe": 0.06, "D05_access_control": 0.32, "D06_security_misconfig": 0.28, "D07_xss": 0.26, "D08_insecure_deserialization": 0.48, "D09_known_vulns": 0.39, "D10_logging_monitoring": 0.42, "D11_crypto_failures": 0.35, "D12_ssrf": 0.32, "D13_file_upload": 0.48, "D14_command_injection": 0.52, "D15_race_conditions": 0.10, "D16_api_security": 0.39, "D17_secrets_management": 0.56, "D18_dependency_management": 0.62, "D19_code_quality": 0.46, "D20_error_handling": 0.32, "D21_session_management": 0.18, "D22_input_validation": 0.36, "D23_authentication": 0.22, "D24_authorization": 0.26, "D25_data_integrity": 0.38, "D26_network_security": 0.22, "D27_container_security": 0.52, "D28_cloud_security": 0.58, "D29_iac_security": 0.65, "D30_supply_chain": 0.49, "overall_risk_score": 0.39}'::jsonb,
     6, 0, 2, 2, 1, 1,
     'stable',
     'scheduled',
     NOW() - INTERVAL '11 days' + INTERVAL '5 minutes',
     NOW() - INTERVAL '11 days' + INTERVAL '16 minutes',
     NOW() - INTERVAL '11 days'),

    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
     'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'running',
     'd0e1f2a3b4c5678901234567890123456789abcdef',
     'feature/model-serving',
     NULL,
     NULL,
     0, 0, 0, 0, 0, 0,
     NULL,
     'manual',
     NOW() - INTERVAL '2 hours',
     NULL,
     NOW() - INTERVAL '2 hours');

-- ─── Sample Findings (50+ findings across scans) ─────────────────────────

-- Findings for Scan 1 (web-api, 12 findings)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SQL-INJ-001', 'SQL Injection in User Login', 
     'User-supplied input is directly concatenated into a SQL query without parameterization, allowing attackers to bypass authentication or extract data.',
     'critical', 'high', 'injection',
     'src/auth/login.py', 45, 52,
     'query = "SELECT * FROM users WHERE username=''' + username + ''' AND password=''' + password + '''"\ncursor.execute(query)',
     'Use parameterized queries: cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))',
     ARRAY['CWE-89'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'XSS-001', 'Reflected XSS in Search Endpoint',
     'Search query parameter is reflected back in the HTML response without proper sanitization, enabling cross-site scripting attacks.',
     'critical', 'high', 'xss',
     'src/routes/search.py', 23, 28,
     'return f"<div>Results for: {query}</div>"',
     'Use template auto-escaping or html.escape() on user input before rendering.',
     ARRAY['CWE-79'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),
'STRIPE_API_KEY = "DEMO_FAKE_KEY_NOT_REAL"',
    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SECRET-001', 'Hardcoded API Key in Configuration',
     'A production API key is hardcoded in the source code, potentially exposing sensitive credentials.',
     'high', 'high', 'secrets',
     'config/settings.py', 12, 14,
     'STRIPE_API_KEY = "DEMO_FAKE_KEY_NOT_REAL"',
     'Move secrets to environment variables or a secrets manager (Vault, AWS Secrets Manager).',
     ARRAY['CWE-798'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'BROKEN-AUTH-001', 'Weak JWT Secret Key',
     'The application uses a short, predictable JWT secret key that can be brute-forced.',
     'high', 'medium', 'broken_auth',
     'src/auth/jwt_handler.py', 8, 10,
     'SECRET_KEY = "dev-secret"',
     'Use a cryptographically secure random secret of at least 256 bits from a secrets manager.',
     ARRAY['CWE-347'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'CMD-INJ-001', 'Command Injection in File Processing',
     'User-provided filename is passed directly to os.system() without validation, allowing arbitrary command execution.',
     'high', 'high', 'injection',
     'src/utils/file_processor.py', 67, 70,
     'os.system(f"convert {uploaded_file} {output_file}")',
     'Use subprocess.run() with a list of arguments and validate/sanitize all inputs.',
     ARRAY['CWE-78'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'ACCESS-001', 'Missing Authorization on Admin Endpoint',
     'The /admin/users endpoint does not verify the caller has admin privileges before returning all user data.',
     'high', 'medium', 'access_control',
     'src/routes/admin.py', 15, 22,
     '@app.get("/admin/users")\nasync def list_users():\n    return await db.fetch_all("SELECT * FROM users")',
     'Add role-based access control decorator: @require_role("admin")',
     ARRAY['CWE-862'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SSRF-001', 'Server-Side Request Forgery in Webhook Handler',
     'The webhook URL is user-controlled and the server makes requests to it without validation, enabling internal network access.',
     'medium', 'medium', 'ssrf',
     'src/webhooks/handler.py', 34, 40,
     'response = requests.post(webhook_url, json=payload)',
     'Validate URLs against an allowlist and block internal IP ranges (169.254.x.x, 10.x.x.x, etc.).',
     ARRAY['CWE-918'], 'A10:2021-Server-Side Request Forgery', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'CRYPTO-001', 'Use of Weak Hashing Algorithm (MD5)',
     'Passwords are hashed using MD5 which is cryptographically broken and vulnerable to rainbow table attacks.',
     'medium', 'high', 'crypto_failures',
     'src/auth/password.py', 5, 8,
     'return hashlib.md5(password.encode()).hexdigest()',
     'Use bcrypt, scrypt, or Argon2id for password hashing.',
     ARRAY['CWE-328'], 'A02:2021-Cryptographic Failures', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'MISCONFIG-001', 'Debug Mode Enabled in Production',
     'The application is running with DEBUG=True, exposing stack traces and sensitive configuration.',
     'medium', 'high', 'security_misconfig',
     'config/settings.py', 3, 5,
     'DEBUG = True\nSHOW_ERROR_DETAILS = True',
     'Set DEBUG=False in production and use a generic error page.',
     ARRAY['CWE-489'], 'A05:2021-Security Misconfiguration', 'fixed',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '25 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'API-001', 'Missing Rate Limiting on Login Endpoint',
     'The login endpoint does not implement rate limiting, making it susceptible to brute-force attacks.',
     'medium', 'medium', 'api_security',
     'src/routes/auth.py', 30, 38,
     '@app.post("/auth/login")\nasync def login(credentials: LoginRequest):',
     'Implement rate limiting using slowapi or nginx limit_req with a max of 5 attempts per minute.',
     ARRAY['CWE-307'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'INPUT-VAL-001', 'Path Traversal in File Upload',
     'Uploaded file names are not sanitized, allowing attackers to write files outside the upload directory.',
     'low', 'medium', 'input_validation',
     'src/routes/uploads.py', 42, 48,
     'with open(f"uploads/{filename}", "wb") as f:\n    f.write(file_content)',
     'Use uuid for filenames and os.path.basename() with path validation.',
     ARRAY['CWE-22'], 'A01:2021-Broken Access Control', 'false_positive',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '26 days'),

    (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'LOGGING-001', 'Sensitive Data in Log Files',
     'Passwords and tokens are being written to log files in plain text.',
     'info', 'high', 'logging_monitoring',
     'src/utils/logger.py', 55, 60,
     'logger.info(f"Login attempt: user={username}, password={password}")',
     'Never log sensitive data. Use structured logging and mask PII.',
     ARRAY['CWE-532'], 'A09:2021-Security Logging and Monitoring Failures', 'open',
     NOW() - INTERVAL '28 days', NOW() - INTERVAL '28 days');

-- Findings for Scan 2 (web-api, 10 findings)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SQL-INJ-002', 'SQL Injection in Report Query',
     'Report date range parameters are concatenated directly into SQL.',
     'high', 'high', 'injection',
     'src/reports/generator.py', 28, 35,
     'query = f"SELECT * FROM events WHERE date >= ''{start_date}'' AND date <= ''{end_date}''"',
     'Use parameterized queries for date range filters.',
     ARRAY['CWE-89'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'XSS-002', 'Stored XSS in Comment System',
     'User comments are stored and rendered without sanitization.',
     'high', 'high', 'xss',
     'src/routes/comments.py', 15, 20,
     'return {"comments": [{"text": c.text} for c in comments]}',
     'Sanitize HTML using bleach or similar library before storing/rendering.',
     ARRAY['CWE-79'], 'A03:2021-Injection', 'fixed',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '18 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SECRET-002', 'AWS Access Key in Environment File',
     'AWS credentials committed to the repository in .env file.',
     'high', 'high', 'secrets',
     '.env', 2, 4,
     'AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE',
     'Rotate credentials immediately and use IAM roles or secrets manager.',
     ARRAY['CWE-798'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'ACCESS-002', 'Insecure Direct Object Reference',
     'Users can access other users invoices by changing the ID parameter.',
     'medium', 'medium', 'access_control',
     'src/routes/billing.py', 44, 50,
     '@app.get("/invoices/{invoice_id}")\nasync def get_invoice(invoice_id: UUID):',
     'Verify the invoice belongs to the authenticated user before returning.',
     ARRAY['CWE-639'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'DEP-VULN-001', 'Vulnerable Dependency (requests 2.25.0)',
     'The requests library version 2.25.0 has a known CVE for session cookie leakage.',
     'medium', 'high', 'known_vulns',
     'requirements.txt', 5, 5,
     'requests==2.25.0',
     'Upgrade to requests>=2.31.0. Run pip audit regularly.',
     ARRAY['CWE-1104'], 'A06:2021-Vulnerable and Outdated Components', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'CRYPTO-002', 'Insecure TLS Configuration',
     'The application allows TLS 1.0 and 1.1 connections which are considered weak.',
     'medium', 'high', 'crypto_failures',
     'src/main.py', 88, 95,
     'ssl_context.minimum_version = ssl.TLSVersion.TLSv1',
     'Set minimum TLS version to 1.2 and configure strong cipher suites.',
     ARRAY['CWE-326'], 'A02:2021-Cryptographic Failures', 'accepted',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '19 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'API-002', 'Missing Input Validation on Pagination',
     'The limit parameter on paginated endpoints accepts negative values.',
     'low', 'medium', 'api_security',
     'src/utils/pagination.py', 10, 15,
     'limit = int(request.args.get("limit", 20))',
     'Validate and clamp pagination parameters: limit = min(max(limit, 1), 100)',
     ARRAY['CWE-20'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SESSION-001', 'Session Token Without Expiration',
     'Session tokens do not have an expiration date, leading to indefinite sessions.',
     'low', 'medium', 'session_management',
     'src/auth/session.py', 20, 25,
     'session["token"] = generate_token(user_id)',
     'Set explicit expiration: session["expires"] = now() + timedelta(hours=24)',
     ARRAY['CWE-613'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'NETWORK-001', 'CORS Allow-Origin Set to Wildcard',
     'CORS is configured to allow all origins, enabling cross-origin attacks.',
     'medium', 'high', 'network_security',
     'src/main.py', 45, 50,
     'allow_origins=["*"]',
     'Restrict CORS to specific domains: allow_origins=[FRONTEND_URL]',
     ARRAY['CWE-942'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days'),

    (gen_random_uuid(), '22222222-2222-2222-2222-222222222222', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'CONTAINER-001', 'Running as Root User',
     'Docker container runs as root user, increasing blast radius of container compromise.',
     'low', 'medium', 'container_security',
     'Dockerfile', 12, 15,
     'FROM python:3.11\n# No USER directive',
     'Add a non-root user: RUN useradd -m appuser && USER appuser',
     ARRAY['CWE-250'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '21 days', NOW() - INTERVAL '21 days');

-- Findings for Scan 3 (web-api, 8 findings)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SQL-INJ-003', 'SQL Injection via ORDER BY Clause',
     'Dynamic ORDER BY column names from user input are concatenated directly.',
     'medium', 'medium', 'injection',
     'src/reports/sorting.py', 18, 22,
     'query += f" ORDER BY {sort_column} {sort_direction}"',
     'Use a whitelist of allowed sort columns and validate direction.',
     ARRAY['CWE-89'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '14 days'),

    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'XSS-003', 'DOM-based XSS in Profile Page',
     'User-controlled data is inserted into the DOM via innerHTML.',
     'medium', 'medium', 'xss',
     'static/js/profile.js', 34, 38,
     'document.getElementById("bio").innerHTML = user.bio;',
     'Use textContent instead of innerHTML, or sanitize with DOMPurify.',
     ARRAY['CWE-79'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '14 days'),

    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'INPUT-VAL-002', 'No File Type Validation on Upload',
     'Any file type can be uploaded to the server including executable files.',
     'medium', 'high', 'input_validation',
     'src/routes/uploads.py', 10, 18,
     'async def upload(file: UploadFile):\n    content = await file.read()',
     'Validate file extensions and MIME types against an allowlist.',
     ARRAY['CWE-434'], 'A04:2021-Insecure Design', 'open',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '14 days'),

    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SSRF-002', 'SSRF via PDF Generation Service',
     'The PDF generation endpoint fetches external URLs without validation.',
     'high', 'medium', 'ssrf',
     'src/services/pdf_generator.py', 22, 28,
     'html = requests.get(url).text\nreturn generate_pdf(html)',
     'Validate URLs and use a dedicated service with network isolation.',
     ARRAY['CWE-918'], 'A10:2021-Server-Side Request Forgery', 'open',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '14 days'),

    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'API-003', 'Sensitive Data Exposure in API Response',
     'API returns full user objects including hashed passwords.',
     'medium', 'high', 'api_security',
     'src/routes/users.py', 55, 62,
     'return user_dict  # Contains password_hash field',
     'Use response models to exclude sensitive fields from API responses.',
     ARRAY['CWE-200'], 'A01:2021-Broken Access Control', 'fixed',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '12 days'),

    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'ERROR-HAND-001', 'Verbose Error Messages Leak Information',
     'Stack traces with file paths and system info are returned in 500 responses.',
     'low', 'high', 'error_handling',
     'src/middleware/error_handler.py', 12, 18,
     'return {"error": str(e), "traceback": traceback.format_exc()}',
     'Return generic error messages to clients, log details server-side.',
     ARRAY['CWE-209'], 'A04:2021-Insecure Design', 'open',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '14 days'),

    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SUPPLY-CHAIN-001', 'Unpinned Dependency Versions',
     'Requirements file uses loose version constraints.',
     'info', 'medium', 'dependency_management',
     'requirements.txt', 1, 10,
     'fastapi>=0.95.0\n sqlalchemy>=2.0',
     'Pin exact versions and use a lock file (poetry.lock, Pipfile.lock).',
     ARRAY['CWE-1104'], 'A06:2021-Vulnerable and Outdated Components', 'open',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '14 days'),

    (gen_random_uuid(), '33333333-3333-3333-3333-333333333333', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'LOGGING-002', 'Missing Security Event Logging',
     'Failed login attempts are not logged, preventing security monitoring.',
     'low', 'medium', 'logging_monitoring',
     'src/routes/auth.py', 55, 65,
     '# No logging on failed authentication\nreturn {"error": "Invalid credentials"}',
     'Log all authentication events with IP address and timestamp.',
     ARRAY['CWE-778'], 'A09:2021-Security Logging and Monitoring Failures', 'open',
     NOW() - INTERVAL '14 days', NOW() - INTERVAL '14 days');

-- Findings for Scan 4 (web-api, 15 findings - degraded scan)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SQL-INJ-004', 'Second-Order SQL Injection',
     'Data retrieved from the database is used in subsequent queries without re-parameterization.',
     'critical', 'medium', 'injection',
     'src/reports/aggregator.py', 42, 50,
     'user_pref = await db.fetch_one("SELECT preference FROM prefs WHERE user_id = ?", user_id)\nresult = await db.fetch_all(f"SELECT * FROM data WHERE category = ''{user_pref}''")',
     'Always use parameterized queries even for data retrieved from the database.',
     ARRAY['CWE-89'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'CMD-INJ-002', 'Remote Code Execution via eval()',
     'User input is passed to eval() allowing arbitrary code execution.',
     'critical', 'high', 'injection',
     'src/utils/calculator.py', 12, 15,
     'result = eval(user_expression)',
     'Use a safe math parser like ast.literal_eval() or a dedicated library.',
     ARRAY['CWE-95'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'XSS-004', 'Reflected XSS in Error Page',
     'Error messages reflect user input without encoding.',
     'critical', 'high', 'xss',
     'templates/error.html', 8, 12,
     '<p>Error: {{ error_message|safe }}</p>',
     'Remove the safe filter and use auto-escaping.',
     ARRAY['CWE-79'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SECRET-003', 'Database Connection String in Logs',
     'Full database connection string with credentials is logged on startup.',
     'high', 'high', 'secrets',
     'src/db/connection.py', 15, 18,
     'logger.info(f"Connected to database: {DATABASE_URL}")',
     'Never log connection strings. Log only sanitized connection info.',
     ARRAY['CWE-532'], 'A09:2021-Security Logging and Monitoring Failures', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'ACCESS-003', 'Horizontal Privilege Escalation',
     'Users can view other organization data by modifying the org_id parameter.',
     'high', 'medium', 'access_control',
     'src/routes/organizations.py', 30, 38,
     '@app.get("/orgs/{org_id}/data")\nasync def get_org_data(org_id: UUID):',
     'Verify the user is a member of the organization before returning data.',
     ARRAY['CWE-639'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'API-004', 'No API Authentication on Public Endpoint',
     'The /api/v1/public/stats endpoint returns sensitive data without authentication.',
     'high', 'high', 'api_security',
     'src/routes/public.py', 10, 20,
     '@app.get("/api/v1/public/stats")\nasync def get_stats():',
     'Add authentication requirement or return only non-sensitive data.',
     ARRAY['CWE-306'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'DESERIAL-001', 'Insecure Deserialization via pickle',
     'User-provided data is deserialized using pickle which can execute arbitrary code.',
     'high', 'high', 'insecure_deserialization',
     'src/cache/serializer.py', 8, 12,
     'return pickle.loads(base64.b64decode(data))',
     'Use JSON or MessagePack for serialization. Never unpickle untrusted data.',
     ARRAY['CWE-502'], 'A08:2021-Software and Data Integrity Failures', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'SSRF-003', 'SSRF in Image Proxy',
     'The image proxy endpoint can fetch internal resources.',
     'medium', 'high', 'ssrf',
     'src/routes/images.py', 18, 25,
     'img_data = requests.get(image_url).content\nreturn Response(img_data, media_type="image/png")',
     'Validate URLs, use an allowlist, and block private IP ranges.',
     ARRAY['CWE-918'], 'A10:2021-Server-Side Request Forgery', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'CRYPTO-003', 'Hardcoded Encryption Key',
     'The AES encryption key is hardcoded in the source code.',
     'high', 'high', 'crypto_failures',
     'src/utils/encryption.py', 5, 8,
     'ENCRYPTION_KEY = b"mysecretkey12345"',
     'Generate keys at runtime or load from a secrets manager.',
     ARRAY['CWE-321'], 'A02:2021-Cryptographic Failures', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'MISCONFIG-002', 'Exposed Prometheus Metrics',
     'Prometheus metrics endpoint is publicly accessible without authentication.',
     'medium', 'high', 'security_misconfig',
     'src/main.py', 110, 115,
     'app.mount("/metrics", make_asgi_app())',
     'Add authentication or restrict /metrics to internal network only.',
     ARRAY['CWE-552'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'IaC-001', 'Terraform State File Exposed',
     'Terraform state file is accessible via the web server.',
     'medium', 'high', 'iac_security',
     'nginx.conf', 25, 28,
     'location /terraform/ {\n    alias /data/terraform/;\n}',
     'Remove static file serving for sensitive directories, use remote state.',
     ARRAY['CWE-538'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'CLOUD-001', 'Overly Permissive S3 Bucket Policy',
     'S3 bucket allows public read access to all objects.',
     'medium', 'medium', 'cloud_security',
     'terraform/s3.tf', 15, 25,
     'principal = "*"\nactions = ["s3:GetObject"]',
     'Restrict access to specific IAM roles and CloudFront OAI.',
     ARRAY['CWE-284'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'RACE-001', 'Race Condition in Balance Update',
     'Concurrent requests can cause inconsistent balance calculations.',
     'low', 'low', 'race_conditions',
     'src/services/billing.py', 55, 65,
     'current = await get_balance(user_id)\nnew_balance = current - amount\nawait set_balance(user_id, new_balance)',
     'Use atomic database operations or distributed locks.',
     ARRAY['CWE-362'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'DATA-INT-001', 'Missing Integrity Check on Transfers',
     'Financial transfers lack cryptographic integrity verification.',
     'medium', 'medium', 'data_integrity',
     'src/services/transfers.py', 40, 48,
     'await db.execute("INSERT INTO transfers (from, to, amount) VALUES ($1, $2, $3)", from_user, to_user, amount)',
     'Add HMAC signatures to transfer records and verify on processing.',
     ARRAY['CWE-345'], 'A08:2021-Software and Data Integrity Failures', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'AUTHZ-001', 'Missing Authorization Check in Batch Operations',
     'Batch delete endpoint does not verify ownership of all items.',
     'high', 'medium', 'authorization',
     'src/routes/batch.py', 22, 30,
     '@app.post("/batch/delete")\nasync def batch_delete(items: List[UUID]):\n    for item in items:\n        await delete_item(item)',
     'Verify ownership of each item before deletion in batch operations.',
     ARRAY['CWE-862'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days'),

    (gen_random_uuid(), '44444444-4444-4444-4444-444444444444', 'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     'FILE-UP-002', 'ZIP Slip Vulnerability',
     'Archive extraction does not validate file paths, allowing directory traversal.',
     'medium', 'high', 'file_upload',
     'src/utils/archive.py', 18, 25,
     'zip_ref.extractall(destination_path)',
     'Validate extracted file paths are within the destination directory.',
     ARRAY['CWE-22'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '3 days', NOW() - INTERVAL '3 days');

-- Findings for Scan 5 (mobile-app, 7 findings)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '55555555-5555-5555-5555-555555555555', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'SENS-DATA-001', 'PII Stored in Plain Text',
     'User phone numbers and addresses are stored in the database without encryption.',
     'high', 'high', 'sensitive_data',
     'src/models/user_profile.py', 22, 28,
     'phone_number = Column(String(20))\nhome_address = Column(Text)',
     'Encrypt PII at rest using AES-256-GCM or a column-level encryption solution.',
     ARRAY['CWE-311'], 'A02:2021-Cryptographic Failures', 'open',
     NOW() - INTERVAL '24 days', NOW() - INTERVAL '24 days'),

    (gen_random_uuid(), '55555555-5555-5555-5555-555555555555', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'SESS-002', 'Insecure Session Token Storage',
     'Session tokens are stored in localStorage instead of httpOnly cookies.',
     'high', 'high', 'session_management',
     'static/js/auth.js', 15, 20,
     'localStorage.setItem("auth_token", token)',
     'Use httpOnly, Secure, SameSite=Strict cookies for token storage.',
     ARRAY['CWE-1004'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '24 days', NOW() - INTERVAL '24 days'),

    (gen_random_uuid(), '55555555-5555-5555-5555-555555555555', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'FILE-UP-001', 'Unrestricted File Upload Size',
     'No file size limit on avatar uploads, enabling DoS attacks.',
     'medium', 'medium', 'file_upload',
     'src/routes/profile.py', 40, 48,
     'async def upload_avatar(file: UploadFile):\n    content = await file.read()',
     'Add max file size validation: if len(content) > 5_000_000: raise Error',
     ARRAY['CWE-770'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '24 days', NOW() - INTERVAL '24 days'),

    (gen_random_uuid(), '55555555-5555-5555-5555-555555555555', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'PUSH-001', 'No Authentication on Push Notification API',
     'Push notification endpoint accepts requests without verifying sender identity.',
     'medium', 'medium', 'api_security',
     'src/routes/push.py', 18, 25,
     '@app.post("/push/send")\nasync def send_notification(req: PushRequest):',
     'Add API key authentication and rate limiting.',
     ARRAY['CWE-306'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '24 days', NOW() - INTERVAL '24 days'),

    (gen_random_uuid(), '55555555-5555-5555-5555-555555555555', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'BROKEN-AUTH-002', 'JWT Tokens Without Expiry Check',
     'JWT tokens are accepted even after the user account is disabled.',
     'medium', 'medium', 'broken_auth',
     'src/auth/jwt_handler.py', 35, 42,
     'payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])',
     'Check if user is still active in the database after JWT validation.',
     ARRAY['CWE-613'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '24 days', NOW() - INTERVAL '24 days'),

    (gen_random_uuid(), '55555555-5555-5555-5555-555555555555', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'INPUT-VAL-003', 'No Validation on Phone Number Format',
     'Phone numbers are stored without format validation.',
     'low', 'low', 'input_validation',
     'src/models/user_profile.py', 22, 22,
     'phone_number = Column(String(20))',
     'Add regex validation: r"^\+?[1-9]\d{1,14}$"',
     ARRAY['CWE-20'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '24 days', NOW() - INTERVAL '24 days'),

    (gen_random_uuid(), '55555555-5555-5555-5555-555555555555', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'DEP-VULN-002', 'Vulnerable Node.js Dependency (lodash 4.17.20)',
     'lodash version 4.17.20 has a prototype pollution vulnerability (CVE-2021-23337).',
     'medium', 'high', 'known_vulns',
     'package.json', 18, 18,
     '"lodash": "4.17.20"',
     'Upgrade to lodash>=4.17.21. Run npm audit regularly.',
     ARRAY['CWE-915'], 'A06:2021-Vulnerable and Outdated Components', 'open',
     NOW() - INTERVAL '24 days', NOW() - INTERVAL '24 days');

-- Findings for Scan 6 (mobile-app, 6 findings)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '66666666-6666-6666-6666-666666666666', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'SENS-DATA-002', 'Location Data Logged Without Consent',
     'User location data is logged for analytics without explicit consent.',
     'medium', 'medium', 'sensitive_data',
     'src/analytics/tracking.py', 28, 35,
     'logger.info(f"User {user_id} at location ({lat}, {lon})")',
     'Anonymize location data and obtain explicit consent before collection.',
     ARRAY['CWE-359'], 'A02:2021-Cryptographic Failures', 'open',
     NOW() - INTERVAL '17 days', NOW() - INTERVAL '17 days'),

    (gen_random_uuid(), '66666666-6666-6666-6666-666666666666', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'SESS-003', 'Session Fixation Vulnerability',
     'Session ID is not regenerated after authentication.',
     'medium', 'medium', 'session_management',
     'src/auth/session.py', 30, 38,
     'session["user_id"] = authenticated_user_id',
     'Regenerate session ID after login: session.regenerate()',
     ARRAY['CWE-384'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '17 days', NOW() - INTERVAL '17 days'),

    (gen_random_uuid(), '66666666-6666-6666-6666-666666666666', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'DEP-VULN-003', 'Outdated OpenSSL Version',
     'The container uses OpenSSL 1.1.1k which has known vulnerabilities.',
     'medium', 'high', 'known_vulns',
     'Dockerfile', 1, 3,
     'FROM node:16-alpine',
     'Upgrade to node:20-alpine with OpenSSL 3.x.',
     ARRAY['CWE-1104'], 'A06:2021-Vulnerable and Outdated Components', 'open',
     NOW() - INTERVAL '17 days', NOW() - INTERVAL '17 days'),

    (gen_random_uuid(), '66666666-6666-6666-6666-666666666666', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'CONTAINER-002', 'Sensitive Files Copied to Image',
     '.env and .git directories are included in the Docker image.',
     'low', 'high', 'container_security',
     'Dockerfile', 8, 12,
     'COPY . /app',
     'Use .dockerignore and only copy necessary files.',
     ARRAY['CWE-538'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '17 days', NOW() - INTERVAL '17 days'),

    (gen_random_uuid(), '66666666-6666-6666-6666-666666666666', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'API-005', 'GraphQL Query Depth Not Limited',
     'GraphQL endpoint allows unlimited query depth causing DoS.',
     'medium', 'medium', 'api_security',
     'src/graphql/schema.py', 15, 20,
     'class Query(graphene.ObjectType):\n    all_users = graphene.List(UserType)',
     'Implement query depth limiting using graphql-depth-limit.',
     ARRAY['CWE-770'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '17 days', NOW() - INTERVAL '17 days'),

    (gen_random_uuid(), '66666666-6666-6666-6666-666666666666', 'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     'MISCONFIG-003', 'Debug Headers in Response',
     'X-Powered-By and Server headers leak implementation details.',
     'info', 'high', 'security_misconfig',
     'src/main.py', 100, 105,
     'return response  # No header filtering',
     'Remove or override X-Powered-By and Server headers.',
     ARRAY['CWE-200'], 'A05:2021-Security Misconfiguration', 'fixed',
     NOW() - INTERVAL '17 days', NOW() - INTERVAL '15 days');

-- Findings for Scan 8 (ml-service, 5 findings)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '88888888-8888-8888-8888-888888888888', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'DESERIAL-002', 'Unsafe YAML Loading',
     'Model configuration files are loaded with yaml.load() instead of safe_load().',
     'high', 'high', 'insecure_deserialization',
     'src/config/loader.py', 12, 15,
     'config = yaml.load(open(config_path))',
     'Use yaml.safe_load() to prevent arbitrary object deserialization.',
     ARRAY['CWE-502'], 'A08:2021-Software and Data Integrity Failures', 'open',
     NOW() - INTERVAL '18 days', NOW() - INTERVAL '18 days'),

    (gen_random_uuid(), '88888888-8888-8888-8888-888888888888', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'SECRET-004', 'MLflow Tracking URI with Credentials',
     'MLflow tracking URI contains embedded credentials.',
     'high', 'high', 'secrets',
     'config/mlflow.yaml', 3, 5,
     'tracking_uri: "http://admin:password@mlflow.internal:5000"',
     'Use environment variables or a secrets manager for credentials.',
     ARRAY['CWE-798'], 'A07:2021-Identification and Authentication Failures', 'open',
     NOW() - INTERVAL '18 days', NOW() - INTERVAL '18 days'),

    (gen_random_uuid(), '88888888-8888-8888-8888-888888888888', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'CONTAINER-003', 'GPU Container Runs as Root',
     'The ML serving container runs as root to access GPU devices.',
     'medium', 'medium', 'container_security',
     'Dockerfile.gpu', 18, 22,
     'USER root',
     'Use the NVIDIA container runtime and a non-root user.',
     ARRAY['CWE-250'], 'A05:2021-Security Misconfiguration', 'open',
     NOW() - INTERVAL '18 days', NOW() - INTERVAL '18 days'),

    (gen_random_uuid(), '88888888-8888-8888-8888-888888888888', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'IaC-002', 'Overly Permissive Security Group',
     'Security group allows SSH from 0.0.0.0/0.',
     'medium', 'high', 'iac_security',
     'terraform/security_groups.tf', 10, 18,
     'cidr_blocks = ["0.0.0.0/0"]\nfrom_port = 22',
     'Restrict SSH to bastion host or VPN IP range.',
     ARRAY['CWE-284'], 'A01:2021-Broken Access Control', 'open',
     NOW() - INTERVAL '18 days', NOW() - INTERVAL '18 days'),

    (gen_random_uuid(), '88888888-8888-8888-8888-888888888888', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'CLOUD-002', 'S3 Bucket Without Encryption',
     'Model artifacts stored in S3 without server-side encryption.',
     'medium', 'high', 'cloud_security',
     'terraform/s3.tf', 20, 28,
     '# No server_side_encryption_configuration block',
     'Enable SSE-S3 or SSE-KMS encryption on all S3 buckets.',
     ARRAY['CWE-311'], 'A02:2021-Cryptographic Failures', 'open',
     NOW() - INTERVAL '18 days', NOW() - INTERVAL '18 days');

-- Findings for Scan 9 (ml-service, 6 findings)
INSERT INTO findings (id, scan_id, project_id, rule_id, title, description, severity, confidence, category, file_path, line_start, line_end, code_snippet, remediation, cwe_ids, owasp_category, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), '99999999-9999-9999-9999-999999999999', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'DESERIAL-003', 'Unsafe pickle Loading of Model Weights',
     'Model weights are loaded using pickle.loads() from an untrusted source.',
     'high', 'high', 'insecure_deserialization',
     'src/models/loader.py', 18, 22,
     'model = pickle.loads(requests.get(model_url).content)',
     'Use torch.load(weights_only=True) or verify model signatures before loading.',
     ARRAY['CWE-502'], 'A08:2021-Software and Data Integrity Failures', 'open',
     NOW() - INTERVAL '11 days', NOW() - INTERVAL '11 days'),

    (gen_random_uuid(), '99999999-9999-9999-9999-999999999999', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'CMD-INJ-003', 'Command Injection in Model Training Script',
     'Hyperparameters are interpolated into shell commands.',
     'high', 'medium', 'injection',
     'src/training/runner.py', 45, 52,
     'os.system(f"python train.py --lr {learning_rate} --epochs {epochs}")',
     'Use subprocess with argument lists and validate numeric inputs.',
     ARRAY['CWE-78'], 'A03:2021-Injection', 'open',
     NOW() - INTERVAL '11 days', NOW() - INTERVAL '11 days'),

    (gen_random_uuid(), '99999999-9999-9999-9999-999999999999', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'SUPPLY-CHAIN-002', 'Untrusted Model Registry',
     'Models are downloaded from a public registry without signature verification.',
     'medium', 'medium', 'supply_chain',
     'src/models/registry.py', 25, 32,
     'model = download_from_huggingface(model_name)',
     'Verify model checksums and use signed model artifacts.',
     ARRAY['CWE-494'], 'A08:2021-Software and Data Integrity Failures', 'open',
     NOW() - INTERVAL '11 days', NOW() - INTERVAL '11 days'),

    (gen_random_uuid(), '99999999-9999-9999-9999-999999999999', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'SENS-DATA-003', 'Training Data Contains PII',
     'Training dataset includes unanonymized user records.',
     'medium', 'medium', 'sensitive_data',
     'src/data/preprocessing.py', 55, 62,
     'df = pd.read_csv("user_data.csv")  # Contains email, phone columns',
     'Anonymize or remove PII columns before using data for training.',
     ARRAY['CWE-359'], 'A02:2021-Cryptographic Failures', 'open',
     NOW() - INTERVAL '11 days', NOW() - INTERVAL '11 days'),

    (gen_random_uuid(), '99999999-9999-9999-9999-999999999999', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'DEP-VULN-004', 'Vulnerable Python Dependency (urllib3 1.26.0)',
     'urllib3 1.26.0 has a certificate validation bypass vulnerability.',
     'medium', 'high', 'known_vulns',
     'requirements.txt', 8, 8,
     'urllib3==1.26.0',
     'Upgrade to urllib3>=1.26.18 or 2.x.',
     ARRAY['CWE-295'], 'A06:2021-Vulnerable and Outdated Components', 'open',
     NOW() - INTERVAL '11 days', NOW() - INTERVAL '11 days'),

    (gen_random_uuid(), '99999999-9999-9999-9999-999999999999', 'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     'DATA-INT-002', 'Model Prediction Inputs Not Validated',
     'Adversarial inputs can cause incorrect predictions or model crashes.',
     'low', 'medium', 'data_integrity',
     'src/inference/predictor.py', 30, 38,
     'prediction = model.predict(input_data)',
     'Add input validation, sanitization, and adversarial detection.',
     ARRAY['CWE-20'], 'A04:2021-Insecure Design', 'open',
     NOW() - INTERVAL '11 days', NOW() - INTERVAL '11 days');

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ SAMPLE BASELINES (3 baselines)                                          │
-- └─────────────────────────────────────────────────────────────────────────┘
INSERT INTO baselines (id, project_id, scan_id, risk_score, risk_vector_90d, findings_distribution, created_at)
VALUES
    (gen_random_uuid(),
     'd3eebc99-9c0b-4ef8-bb6d-6bb9bd380a44',
     '11111111-1111-1111-1111-111111111111',
     0.72,
     '{"D01_injection": 0.85, "D02_broken_auth": 0.45, "D03_sensitive_data": 0.62, "D07_xss": 0.91, "D17_secrets_management": 0.76, "D22_input_validation": 0.93, "overall_risk_score": 0.72}'::jsonb,
     '{"critical": 2, "high": 4, "medium": 3, "low": 2, "info": 1}'::jsonb,
     NOW() - INTERVAL '28 days'),

    (gen_random_uuid(),
     'e4eebc99-9c0b-4ef8-bb6d-6bb9bd380a55',
     '55555555-5555-5555-5555-555555555555',
     0.48,
     '{"D03_sensitive_data": 0.68, "D17_secrets_management": 0.62, "D21_session_management": 0.58, "D13_file_upload": 0.55, "overall_risk_score": 0.48}'::jsonb,
     '{"critical": 0, "high": 2, "medium": 3, "low": 1, "info": 1}'::jsonb,
     NOW() - INTERVAL '24 days'),

    (gen_random_uuid(),
     'f5eebc99-9c0b-4ef8-bb6d-6bb9bd380a66',
     '88888888-8888-8888-8888-888888888888',
     0.35,
     '{"D08_insecure_deserialization": 0.42, "D17_secrets_management": 0.52, "D18_dependency_management": 0.58, "D29_iac_security": 0.62, "overall_risk_score": 0.35}'::jsonb,
     '{"critical": 0, "high": 1, "medium": 2, "low": 1, "info": 1}'::jsonb,
     NOW() - INTERVAL '18 days');

-- ┌─────────────────────────────────────────────────────────────────────────┐
-- │ VERIFICATION                                                            │
-- └─────────────────────────────────────────────────────────────────────────┘
-- Verify counts
DO $$
DECLARE
    user_count INT;
    org_count INT;
    project_count INT;
    scan_count INT;
    finding_count INT;
    baseline_count INT;
BEGIN
    SELECT COUNT(*) INTO user_count FROM users;
    SELECT COUNT(*) INTO org_count FROM organizations;
    SELECT COUNT(*) INTO project_count FROM projects;
    SELECT COUNT(*) INTO scan_count FROM scans;
    SELECT COUNT(*) INTO finding_count FROM findings;
    SELECT COUNT(*) INTO baseline_count FROM baselines;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Omni-Auditor Database Seed Complete';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Users:        %', user_count;
    RAISE NOTICE 'Organizations: %', org_count;
    RAISE NOTICE 'Projects:     %', project_count;
    RAISE NOTICE 'Scans:        %', scan_count;
    RAISE NOTICE 'Findings:     %', finding_count;
    RAISE NOTICE 'Baselines:    %', baseline_count;
    RAISE NOTICE '========================================';
END $$;
