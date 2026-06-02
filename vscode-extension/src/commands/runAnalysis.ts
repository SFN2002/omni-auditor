import * as vscode from 'vscode';
import * as path from 'path';
import type { ApiClient } from '../utils/apiClient';
import type { DiagnosticsProvider } from '../providers/diagnostics';
import type { DecorationProvider } from '../providers/decorationProvider';
import type { OmniReport } from '../types';

export async function runAnalysis(
    apiClient: ApiClient,
    diagnosticsProvider: DiagnosticsProvider,
    decorationProvider: DecorationProvider,
    statusBarItem: vscode.StatusBarItem,
    reportMap: Map<string, OmniReport>,
    fileUri?: vscode.Uri
): Promise<OmniReport | null> {
    const targetUri = fileUri ?? vscode.window.activeTextEditor?.document.uri;
    if (!targetUri) {
        vscode.window.showInformationMessage('Omni-Auditor: No file selected.');
        return null;
    }
    if (targetUri.scheme !== 'file') {
        vscode.window.showWarningMessage('Omni-Auditor: Can only analyze local files.');
        return null;
    }
    const filePath = targetUri.fsPath;
    const fileName = path.basename(filePath);

    return vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: `Omni-Auditor: Analyzing ${fileName}`,
            cancellable: true,
        },
        async (_progress, token) => {
            decorationProvider.clearDecorations();
            const report = await apiClient.analyze(filePath, token);
            if (!report) { return null; }
            reportMap.set(filePath, report);
            diagnosticsProvider.update(targetUri, report);

            const editor = vscode.window.activeTextEditor;
            if (editor && editor.document.uri.toString() === targetUri.toString()) {
                decorationProvider.applyDecorations(editor, report.security_findings);
            }

            refreshStatusBar(statusBarItem, reportMap);
            vscode.window.showInformationMessage(
                `Omni-Auditor: ${fileName} → ${report.risk_tier} (${report.unified_risk_score.toFixed(4)})`
            );
            return report;
        }
    );
}

export function refreshStatusBar(item: vscode.StatusBarItem, reportMap: Map<string, OmniReport>): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        item.text = '$(shield) Omni-Auditor';
        item.color = undefined;
        item.tooltip = 'Open a Python file';
        return;
    }
    const report = reportMap.get(editor.document.uri.fsPath);
    if (!report) {
        item.text = '$(shield) Omni-Auditor: --';
        item.color = undefined;
        item.tooltip = 'Click to analyze';
        return;
    }
    const { unified_risk_score: score, risk_tier: tier } = report;
    item.text = `$(shield) Omni: ${score.toFixed(2)} [${tier}]`;
    item.color = tierColor(tier);
    item.tooltip = [
        `Score: ${score.toFixed(4)}`,
        `Tier: ${tier}`,
        `Weights: A=${report.fusion_weights[0].toFixed(2)} V=${report.fusion_weights[1].toFixed(2)} S=${report.fusion_weights[2].toFixed(2)}`,
        `Findings: ${report.security_findings.length}`,
        `Functions: ${report.per_function_metrics.length}`,
    ].join('\n');
}

function tierColor(tier: string): string | undefined {
    switch (tier) {
        case 'CRITICAL': return '#ef4444';
        case 'HIGH': return '#f97316';
        case 'MEDIUM': return '#facc15';
        case 'LOW': return '#22c55e';
        default: return undefined;
    }
}
