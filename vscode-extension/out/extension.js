"use strict";
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
const child_process_1 = require("child_process");
const path = __importStar(require("path"));
// ── State ─────────────────────────────────────────────────────────────────────
const analysisCache = new Map();
const inFlight = new Map();
let statusBarItem;
let diagnosticCollection;
// ── Helpers ───────────────────────────────────────────────────────────────────
function escapeRegExp(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
function updateStatusBar() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        statusBarItem.text = '$(shield) Omni: N/A';
        statusBarItem.color = undefined;
        statusBarItem.tooltip = 'Open a Python file to analyze';
        return;
    }
    const report = analysisCache.get(editor.document.uri.fsPath);
    if (!report) {
        statusBarItem.text = '$(shield) Omni: --';
        statusBarItem.color = undefined;
        statusBarItem.tooltip = 'Omni-Auditor: no analysis yet. Click to scan.';
        return;
    }
    const score = report.unified_risk_score;
    const tier = report.risk_tier;
    statusBarItem.text = `$(shield) Omni: ${score.toFixed(4)} [${tier}]`;
    if (score >= 0.7 || tier === 'CRITICAL') {
        statusBarItem.color = '#ef4444';
    }
    else if (score >= 0.3) {
        statusBarItem.color = '#facc15';
    }
    else {
        statusBarItem.color = '#4ade80';
    }
    statusBarItem.tooltip = `Risk: ${score.toFixed(4)} (${tier})\nClick to rescan`;
}
function updateDiagnostics(uri, report) {
    const diagnostics = [];
    if (report) {
        for (const finding of report.security_findings ?? []) {
            const line = Math.max(0, (finding.line_number ?? 1) - 1);
            const range = new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER);
            const sev = finding.severity.toUpperCase();
            let severity;
            if (sev === 'CRITICAL' || sev === 'HIGH') {
                severity = vscode.DiagnosticSeverity.Error;
            }
            else if (sev === 'MEDIUM') {
                severity = vscode.DiagnosticSeverity.Warning;
            }
            else {
                severity = vscode.DiagnosticSeverity.Information;
            }
            diagnostics.push(new vscode.Diagnostic(range, `[${finding.category}] ${finding.severity}`, severity));
        }
        const tier = report.risk_tier.toUpperCase();
        if (tier === 'CRITICAL' || tier === 'HIGH') {
            const range = new vscode.Range(0, 0, 0, 0);
            diagnostics.push(new vscode.Diagnostic(range, `Structural risk tier: ${report.risk_tier} (Score: ${report.unified_risk_score.toFixed(4)})`, vscode.DiagnosticSeverity.Warning));
        }
    }
    diagnosticCollection.set(uri, diagnostics);
}
// ── Analysis Core ─────────────────────────────────────────────────────────────
function analyzePath(fsPath) {
    const existing = inFlight.get(fsPath);
    if (existing) {
        return existing;
    }
    const config = vscode.workspace.getConfiguration('omniAuditor');
    const cliPath = config.get('cliPath') || 'omni-auditor';
    const promise = new Promise((resolve) => {
        const proc = (0, child_process_1.spawn)(cliPath, ['--json', fsPath]);
        let stdout = '';
        let stderr = '';
        proc.stdout.on('data', (data) => {
            stdout += data.toString();
        });
        proc.stderr.on('data', (data) => {
            stderr += data.toString();
        });
        proc.on('error', (err) => {
            inFlight.delete(fsPath);
            const code = err.code;
            if (code === 'ENOENT') {
                vscode.window.showWarningMessage('Omni-Auditor CLI not found. Run: pip install omni-auditor');
            }
            else {
                vscode.window.showWarningMessage(`Omni-Auditor error: ${err.message}`);
            }
            resolve(null);
        });
        proc.on('close', (code) => {
            inFlight.delete(fsPath);
            if (code !== 0) {
                const msg = stderr.trim() || `Exited with code ${code ?? 'null'}`;
                const lowered = msg.toLowerCase();
                if (lowered.includes('not found') || lowered.includes('not recognized') || lowered.includes('cannot find')) {
                    vscode.window.showWarningMessage('Omni-Auditor CLI not found. Run: pip install omni-auditor');
                }
                else {
                    console.error(`Omni-Auditor failed for ${fsPath}: ${msg}`);
                }
                resolve(null);
                return;
            }
            try {
                const report = JSON.parse(stdout);
                analysisCache.set(fsPath, report);
                resolve(report);
            }
            catch (e) {
                console.error(`Omni-Auditor JSON parse error for ${fsPath}:`, e);
                resolve(null);
            }
        });
    });
    inFlight.set(fsPath, promise);
    return promise;
}
async function analyzeDocument(document) {
    return analyzePath(document.uri.fsPath);
}
// ── CodeLens Provider ─────────────────────────────────────────────────────────
class OmniCodeLensProvider {
    _onDidChangeCodeLenses = new vscode.EventEmitter();
    onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;
    refresh() {
        this._onDidChangeCodeLenses.fire();
    }
    provideCodeLenses(document) {
        const report = analysisCache.get(document.uri.fsPath);
        if (!report?.per_function_metrics?.length) {
            return [];
        }
        const lenses = [];
        const lines = document.getText().split(/\r?\n/);
        for (const metric of report.per_function_metrics) {
            const pattern = new RegExp(`^(\\s*)(async\\s+)?def\\s+${escapeRegExp(metric.function_name)}\\s*\\(`);
            for (let i = 0; i < lines.length; i++) {
                if (pattern.test(lines[i])) {
                    const range = new vscode.Range(i, 0, i, 0);
                    let title;
                    if (metric.anomaly_z > 1.0 && metric.blocks > 10) {
                        title = 'Omni: 🔥 Complex';
                    }
                    else if (metric.anomaly_z > 0.8) {
                        title = 'Omni: 🌡️ Elevated';
                    }
                    else {
                        title = 'Omni: ✅ Healthy';
                    }
                    lenses.push(new vscode.CodeLens(range, {
                        title,
                        tooltip: `Blocks: ${metric.blocks}, Anomaly Z: ${metric.anomaly_z.toFixed(4)}`,
                        command: ''
                    }));
                    break;
                }
            }
        }
        return lenses;
    }
}
const codeLensProvider = new OmniCodeLensProvider();
// ── Commands ──────────────────────────────────────────────────────────────────
async function scanActiveEditor() {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== 'python') {
        vscode.window.showInformationMessage('Open a Python file to scan.');
        return;
    }
    const report = await analyzePath(editor.document.uri.fsPath);
    if (report) {
        updateStatusBar();
        updateDiagnostics(editor.document.uri, report);
        codeLensProvider.refresh();
    }
}
async function scanWorkspace() {
    const files = await vscode.workspace.findFiles('**/*.py', '{**/node_modules/**,**/.git/**,**/venv/**,**/.venv/**,**/out/**,**/dist/**}', 20);
    if (files.length === 0) {
        vscode.window.showInformationMessage('No Python files found in workspace.');
        return;
    }
    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: 'Omni-Auditor: Scanning workspace',
        cancellable: false
    }, async (progress) => {
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            progress.report({
                increment: 100 / files.length,
                message: path.basename(file.fsPath)
            });
            const report = await analyzePath(file.fsPath);
            if (report) {
                updateDiagnostics(file, report);
            }
        }
    });
    updateStatusBar();
    codeLensProvider.refresh();
    vscode.window.showInformationMessage(`Omni-Auditor: Scanned ${files.length} file(s).`);
}
// ── Activation ────────────────────────────────────────────────────────────────
function activate(context) {
    console.log('[Omni-Auditor] Extension activating...');
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'omni-auditor.scanFile';
    context.subscriptions.push(statusBarItem);
    statusBarItem.show();
    diagnosticCollection = vscode.languages.createDiagnosticCollection('omni-auditor');
    context.subscriptions.push(diagnosticCollection);
    context.subscriptions.push(vscode.languages.registerCodeLensProvider('python', codeLensProvider));
    context.subscriptions.push(vscode.workspace.onDidSaveTextDocument(async (doc) => {
        if (doc.languageId !== 'python') {
            return;
        }
        const config = vscode.workspace.getConfiguration('omniAuditor');
        const runOnSave = config.get('runOnSave') ?? true;
        if (!runOnSave) {
            return;
        }
        const report = await analyzePath(doc.uri.fsPath);
        if (report) {
            updateStatusBar();
            updateDiagnostics(doc.uri, report);
            codeLensProvider.refresh();
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand('omni-auditor.scanFile', scanActiveEditor));
    context.subscriptions.push(vscode.commands.registerCommand('omni-auditor.scanWorkspace', scanWorkspace));
    const activeEditor = vscode.window.activeTextEditor;
    if (activeEditor && activeEditor.document.languageId === 'python') {
        analyzePath(activeEditor.document.uri.fsPath).then((report) => {
            if (report) {
                updateStatusBar();
                updateDiagnostics(activeEditor.document.uri, report);
                codeLensProvider.refresh();
            }
        }).catch((err) => {
            console.error('[Omni-Auditor] Startup analysis failed:', err);
        });
    }
    else {
        updateStatusBar();
    }
}
function deactivate() {
    // Disposables are managed by context.subscriptions
}
//# sourceMappingURL=extension.js.map