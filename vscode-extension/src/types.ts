/**
 * Shared type definitions for Omni-Auditor extension.
 *
 * Mirrors the JSON schema emitted by:
 *   python -m src.main <file> --json
 *
 * Integration point: src/main.py lines 979-995 (JSON payload construction).
 */

export type RiskTier = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

/** Single security threat emitted by SafetyGuard / VulnerabilityScanner. */
export interface SecurityFinding {
    severity: Severity;
    line_number: number;
    category: string;
    node_path: string;
    confidence_score: number;
}

/** Per-function anomaly report from StatisticalValidator. */
export interface PerFunctionMetric {
    function_key: string;
    mahalanobis_distance: number;
    renyi_entropy_discrete: number;
    renyi_entropy_differential: number;
    renyi_z_score: number;
    anomaly_score: number;
    /** 14-D spectral feature vector (order, size, fiedler, …). */
    raw_feature_vector: number[];
}

/** Optional full-dimension vectors if CLI is extended to emit them. */
export interface OmniVectors {
    aggregate_56d?: number[];
    anomaly_16d?: number[];
    threat_18d?: number[];
}

/** Top-level JSON report produced by Omni-Auditor --json. */
export interface OmniReport {
    file_path: string;
    unified_risk_score: number;
    risk_tier: RiskTier;
    /** Adaptive fusion weights: [Analyzer, Validator, Security]. */
    fusion_weights: [number, number, number];
    per_function_metrics: PerFunctionMetric[];
    security_findings: SecurityFinding[];
    vectors?: OmniVectors;
}
