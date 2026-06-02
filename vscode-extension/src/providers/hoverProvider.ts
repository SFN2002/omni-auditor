import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import type { OmniReport } from '../types';

function containsTraversal(p: string): boolean {
    const normalised = path.normalize(p);
    return normalised.includes('..' + path.sep) || normalised.endsWith('..') || normalised.startsWith('..' + path.sep);
}

function resolveProjectRoot(filePath?: string): string | undefined {
    // 1. Walk up from the active file looking for src/main.py
    if (filePath) {
        let dir = path.dirname(filePath);
        while (dir !== path.dirname(dir)) {
            if (fs.existsSync(path.join(dir, 'src', 'main.py'))) {
                return dir;
            }
            dir = path.dirname(dir);
        }
    }
    // 2. Fallback: workspace root
    const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (ws) {
        if (fs.existsSync(path.join(ws, 'src', 'main.py'))) {
            return ws;
        }
    }
    // 3. No prompt in hover provider (synchronous context); user must configure or open from workspace
    return undefined;
}

/**
 * HoverProvider that reads the latest Omni-Auditor report from output.json
 * and surfaces spectral metrics on hover.
 *
 * Reads from the configured omniAuditor.projectRoot (where main.py writes
 * output.json) rather than holding in-memory state, so external CLI runs
 * are immediately reflected.
 */
export class OmniHoverProvider implements vscode.HoverProvider {
    provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.ProviderResult<vscode.Hover> {
        if (document.languageId !== 'python') {
            return null;
        }

        const report = this.readReport(document.fileName);
        if (!report) {
            return null;
        }

        const lineText = document.lineAt(position).text;
        const funcMatch = lineText.match(/def\s+(\w+)/);
        if (funcMatch) {
            const funcName = funcMatch[1];
            const metric = report.per_function_metrics.find(
                m => m.function_key.startsWith(`${funcName}@`)
            );
            if (metric) {
                return this.buildFunctionHover(report, metric);
            }
        }

        return this.buildModuleHover(report);
    }

    /** Locate output.json using the extension configuration. */
    private getOutputJsonPath(filePath?: string): string | undefined {
        const cfg = vscode.workspace.getConfiguration('omniAuditor');
        let projectRoot = cfg.get<string>('projectRoot') || '';
        if (!projectRoot) {
            projectRoot = resolveProjectRoot(filePath) || '';
        }
        return projectRoot ? path.join(projectRoot, 'output.json') : undefined;
    }

    private readReport(filePath?: string): OmniReport | null {
        const outputPath = this.getOutputJsonPath(filePath);
        if (!outputPath || !fs.existsSync(outputPath)) {
            return null;
        }
        try {
            const content = fs.readFileSync(outputPath, 'utf-8');
            if (!content.trim()) {
                return null;
            }
            return JSON.parse(content) as OmniReport;
        } catch {
            return null;
        }
    }

    private buildModuleHover(report: OmniReport): vscode.Hover {
        const md = new vscode.MarkdownString();
        md.isTrusted = true;
        md.appendMarkdown(`**Omni-Auditor Module Assessment**  \n\n`);
        md.appendMarkdown(`- **File:** \`${path.basename(report.file_path)}\`  \n`);
        md.appendMarkdown(`- **Score:** ${report.unified_risk_score.toFixed(4)}  \n`);
        md.appendMarkdown(`- **Tier:** ${report.risk_tier}  \n`);
        md.appendMarkdown(`- **Fusion Weights:** Analyzer=${report.fusion_weights[0].toFixed(2)} Validator=${report.fusion_weights[1].toFixed(2)} Security=${report.fusion_weights[2].toFixed(2)}  \n`);
        md.appendMarkdown(`- **Findings:** ${report.security_findings.length}  \n`);
        md.appendMarkdown(`- **Functions:** ${report.per_function_metrics.length}  \n`);
        return new vscode.Hover(md);
    }

    private buildFunctionHover(
        report: OmniReport,
        metric: import('../types').PerFunctionMetric
    ): vscode.Hover {
        const md = new vscode.MarkdownString();
        md.isTrusted = true;
        md.appendMarkdown(`**Omni-Auditor: \`${metric.function_key}\`**  \n\n`);
        md.appendMarkdown(`| Metric | Value |\n|--------|-------|\n`);
        md.appendMarkdown(`| Mahalanobis Distance | ${metric.mahalanobis_distance.toFixed(4)} |\n`);
        md.appendMarkdown(`| Rényi Entropy (discrete) | ${metric.renyi_entropy_discrete.toFixed(4)} |\n`);
        md.appendMarkdown(`| Rényi Entropy (differential) | ${metric.renyi_entropy_differential.toFixed(4)} |\n`);
        md.appendMarkdown(`| Rényi Z-Score | ${metric.renyi_z_score.toFixed(4)} |\n`);
        md.appendMarkdown(`| Anomaly Score | ${metric.anomaly_score.toFixed(4)} |\n\n`);
        md.appendMarkdown(`**14-D Feature Vector:**  \n`);
        md.appendMarkdown(`\`${metric.raw_feature_vector.map(v => v.toFixed(2)).join(', ')}\``);
        return new vscode.Hover(md);
    }
}
