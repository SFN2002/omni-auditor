/**
 * Omni-Auditor VS Code Extension — Entry Point
 *
 * Wires together:
 *   • ApiClient        (spawns python -m src.main <file> --json)
 *   • DiagnosticsProvider + OmniHoverProvider
 *   • runAnalysis command
 *   • RiskDashboardPanel webview
 *   • Status-bar item + auto-analyse-on-save
 *
 * Integration: src/main.py (CLI) and src/engine/*.py (engines).
 */

import * as vscode from 'vscode';
import { ApiClient } from './utils/apiClient';
import { DiagnosticsProvider } from './providers/diagnostics';
import { OmniHoverProvider } from './providers/hoverProvider';
import { DecorationProvider } from './providers/decorationProvider';
import { runAnalysis, refreshStatusBar } from './commands/runAnalysis';
import { RiskDashboardPanel } from './panels/riskDashboard';
import type { OmniReport } from './types';

let decorationProvider: DecorationProvider | undefined;

export function activate(context: vscode.ExtensionContext): void {
    console.log('[Omni-Auditor] Extension activating…');

    // ── Shared state ──────────────────────────────────────────────────────
    const reportMap = new Map<string, OmniReport>();
    const apiClient = new ApiClient(context);
    const diagnosticsProvider = new DiagnosticsProvider();
    decorationProvider = new DecorationProvider(context.extensionUri);
    const dashboard = new RiskDashboardPanel(context);

    // ── Status bar ────────────────────────────────────────────────────────
    const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'omni-auditor.analyzeCurrentFile';
    statusBarItem.text = '$(shield) Omni-Auditor';
    statusBarItem.tooltip = 'Click to analyze current Python file';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // ── Commands ──────────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('omni-auditor.analyzeCurrentFile', async () => {
            if (!decorationProvider) { return; }
            const report = await runAnalysis(apiClient, diagnosticsProvider, decorationProvider, statusBarItem, reportMap);
            if (report) { dashboard.update(report); }
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('omni-auditor.showRiskDashboard', () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showInformationMessage('Open a Python file first.');
                return;
            }
            const report = reportMap.get(editor.document.uri.fsPath);
            if (!report) {
                vscode.window.showInformationMessage('Analyze the file first (Omni-Auditor: Analyze Current File).');
                return;
            }
            dashboard.show(report);
        })
    );

    // ── Diagnostics & Hover ───────────────────────────────────────────────
    context.subscriptions.push(diagnosticsProvider.collection);
    context.subscriptions.push(decorationProvider);
    context.subscriptions.push(
        vscode.languages.registerHoverProvider('python', new OmniHoverProvider())
    );

    // ── Auto-analyse on save ──────────────────────────────────────────────
    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(async (doc) => {
            if (doc.languageId !== 'python') { return; }
            const cfg = vscode.workspace.getConfiguration('omniAuditor');
            if (!cfg.get<boolean>('autoAnalyzeOnSave')) { return; }
            if (!decorationProvider) { return; }
            const report = await runAnalysis(apiClient, diagnosticsProvider, decorationProvider, statusBarItem, reportMap, doc.uri);
            if (report) { dashboard.update(report); }
        })
    );

    // ── Active editor switch ──────────────────────────────────────────────
    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor((editor) => {
            if (editor && editor.document.languageId === 'python') {
                refreshStatusBar(statusBarItem, reportMap);
                const report = reportMap.get(editor.document.uri.fsPath);
                if (report && decorationProvider) {
                    decorationProvider.applyDecorations(editor, report.security_findings);
                } else if (decorationProvider) {
                    decorationProvider.clearEditor(editor);
                }
            } else {
                statusBarItem.text = '$(shield) Omni-Auditor';
                statusBarItem.color = undefined;
                if (editor && decorationProvider) {
                    decorationProvider.clearEditor(editor);
                }
            }
        })
    );

    context.subscriptions.push(
        vscode.workspace.onDidCloseTextDocument((doc) => {
            const editor = vscode.window.visibleTextEditors.find(e => e.document.uri.fsPath === doc.uri.fsPath);
            if (editor && decorationProvider) {
                decorationProvider.clearEditor(editor);
            }
        })
    );

    // ── React to external output.json changes ─────────────────────────────
    context.subscriptions.push(
        apiClient.onDidChange((filePath) => {
            const report = apiClient.getCached(filePath);
            if (report) {
                reportMap.set(filePath, report);
                const uri = vscode.Uri.file(filePath);
                diagnosticsProvider.update(uri, report);
                const editor = vscode.window.activeTextEditor;
                if (editor && editor.document.uri.fsPath === filePath && decorationProvider) {
                    decorationProvider.applyDecorations(editor, report.security_findings);
                }
                refreshStatusBar(statusBarItem, reportMap);
            }
        })
    );

    // ── Startup analysis for already-open Python file ─────────────────────
    const active = vscode.window.activeTextEditor;
    if (active && active.document.languageId === 'python') {
        if (decorationProvider) {
            runAnalysis(apiClient, diagnosticsProvider, decorationProvider, statusBarItem, reportMap, active.document.uri)
                .then((report) => { if (report) { dashboard.update(report); } })
                .catch((err) => console.error('[Omni-Auditor] Startup error:', err));
        }
    }
}

export function deactivate(): void {
    decorationProvider?.dispose();
    decorationProvider = undefined;
}
