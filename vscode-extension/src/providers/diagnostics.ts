import * as vscode from 'vscode';
import type { OmniReport, SecurityFinding, Severity } from '../types';

const SEVERITY_MAP: Record<Severity, vscode.DiagnosticSeverity> = {
    CRITICAL: vscode.DiagnosticSeverity.Error,
    HIGH: vscode.DiagnosticSeverity.Warning,
    MEDIUM: vscode.DiagnosticSeverity.Information,
    LOW: vscode.DiagnosticSeverity.Hint,
};

export class DiagnosticsProvider {
    public readonly collection: vscode.DiagnosticCollection;

    constructor() {
        this.collection = vscode.languages.createDiagnosticCollection('omni-auditor');
    }

    public update(uri: vscode.Uri, report: OmniReport | null): void {
        if (!report) { this.collection.set(uri, []); return; }
        const diagnostics: vscode.Diagnostic[] = [];
        for (const f of report.security_findings) {
            const line = Math.max(0, f.line_number - 1);
            const range = new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER);
            const sev = SEVERITY_MAP[f.severity] ?? vscode.DiagnosticSeverity.Information;
            const msg = `[${f.severity}] ${f.category} | ${f.node_path} | confidence ${(f.confidence_score * 100).toFixed(0)}%`;
            const d = new vscode.Diagnostic(range, msg, sev);
            d.code = f.category;
            d.source = 'Omni-Auditor';
            diagnostics.push(d);
        }
        if (report.risk_tier === 'CRITICAL' || report.risk_tier === 'HIGH') {
            diagnostics.push(new vscode.Diagnostic(
                new vscode.Range(0, 0, 0, 0),
                `Structural Risk: ${report.risk_tier} (score ${report.unified_risk_score.toFixed(4)})`,
                vscode.DiagnosticSeverity.Warning
            ));
        }
        this.collection.set(uri, diagnostics);
    }

    public dispose(): void { this.collection.dispose(); }
}
