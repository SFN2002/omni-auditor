"use strict";
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
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const apiClient_1 = require("./utils/apiClient");
const diagnostics_1 = require("./providers/diagnostics");
const hoverProvider_1 = require("./providers/hoverProvider");
const decorationProvider_1 = require("./providers/decorationProvider");
const runAnalysis_1 = require("./commands/runAnalysis");
const riskDashboard_1 = require("./panels/riskDashboard");
let decorationProvider;
function activate(context) {
    console.log('[Omni-Auditor] Extension activating…');
    // ── Shared state ──────────────────────────────────────────────────────
    const reportMap = new Map();
    const apiClient = new apiClient_1.ApiClient(context);
    const diagnosticsProvider = new diagnostics_1.DiagnosticsProvider();
    decorationProvider = new decorationProvider_1.DecorationProvider(context.extensionUri);
    const dashboard = new riskDashboard_1.RiskDashboardPanel(context);
    // ── Status bar ────────────────────────────────────────────────────────
    const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'omni-auditor.analyzeCurrentFile';
    statusBarItem.text = '$(shield) Omni-Auditor';
    statusBarItem.tooltip = 'Click to analyze current Python file';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    // ── Commands ──────────────────────────────────────────────────────────
    context.subscriptions.push(vscode.commands.registerCommand('omni-auditor.analyzeCurrentFile', async () => {
        if (!decorationProvider) {
            return;
        }
        const report = await (0, runAnalysis_1.runAnalysis)(apiClient, diagnosticsProvider, decorationProvider, statusBarItem, reportMap);
        if (report) {
            dashboard.update(report);
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand('omni-auditor.showRiskDashboard', () => {
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
    }));
    // ── Diagnostics & Hover ───────────────────────────────────────────────
    context.subscriptions.push(diagnosticsProvider.collection);
    context.subscriptions.push(decorationProvider);
    context.subscriptions.push(vscode.languages.registerHoverProvider('python', new hoverProvider_1.OmniHoverProvider()));
    // ── Auto-analyse on save ──────────────────────────────────────────────
    context.subscriptions.push(vscode.workspace.onDidSaveTextDocument(async (doc) => {
        if (doc.languageId !== 'python') {
            return;
        }
        const cfg = vscode.workspace.getConfiguration('omniAuditor');
        if (!cfg.get('autoAnalyzeOnSave')) {
            return;
        }
        if (!decorationProvider) {
            return;
        }
        const report = await (0, runAnalysis_1.runAnalysis)(apiClient, diagnosticsProvider, decorationProvider, statusBarItem, reportMap, doc.uri);
        if (report) {
            dashboard.update(report);
        }
    }));
    // ── Active editor switch ──────────────────────────────────────────────
    context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (editor && editor.document.languageId === 'python') {
            (0, runAnalysis_1.refreshStatusBar)(statusBarItem, reportMap);
            const report = reportMap.get(editor.document.uri.fsPath);
            if (report && decorationProvider) {
                decorationProvider.applyDecorations(editor, report.security_findings);
            }
            else if (decorationProvider) {
                decorationProvider.clearDecorations();
            }
        }
        else {
            statusBarItem.text = '$(shield) Omni-Auditor';
            statusBarItem.color = undefined;
            decorationProvider?.clearDecorations();
        }
    }));
    context.subscriptions.push(vscode.workspace.onDidCloseTextDocument((_doc) => {
        decorationProvider?.clearDecorations();
    }));
    // ── React to external output.json changes ─────────────────────────────
    context.subscriptions.push(apiClient.onDidChange((filePath) => {
        const report = apiClient.getCached(filePath);
        if (report) {
            reportMap.set(filePath, report);
            const uri = vscode.Uri.file(filePath);
            diagnosticsProvider.update(uri, report);
            const editor = vscode.window.activeTextEditor;
            if (editor && editor.document.uri.fsPath === filePath && decorationProvider) {
                decorationProvider.applyDecorations(editor, report.security_findings);
            }
            (0, runAnalysis_1.refreshStatusBar)(statusBarItem, reportMap);
        }
    }));
    // ── Startup analysis for already-open Python file ─────────────────────
    const active = vscode.window.activeTextEditor;
    if (active && active.document.languageId === 'python') {
        if (decorationProvider) {
            (0, runAnalysis_1.runAnalysis)(apiClient, diagnosticsProvider, decorationProvider, statusBarItem, reportMap, active.document.uri)
                .then((report) => { if (report) {
                dashboard.update(report);
            } })
                .catch((err) => console.error('[Omni-Auditor] Startup error:', err));
        }
    }
}
function deactivate() {
    decorationProvider?.dispose();
    decorationProvider = undefined;
}
//# sourceMappingURL=extension.js.map