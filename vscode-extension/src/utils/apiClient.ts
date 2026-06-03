import * as vscode from 'vscode';
import { spawn } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import type { OmniReport } from '../types';

interface CacheEntry { report: OmniReport; timestamp: number; }
const CACHE_TTL_MS = 5 * 60 * 1000;

function isInsideWorkspace(projectRoot: string): boolean {
    const wsPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!wsPath) { return false; }
    const resolved = path.resolve(projectRoot);
    const wsResolved = path.resolve(wsPath);
    return resolved === wsResolved || resolved.startsWith(wsResolved + path.sep);
}

function containsTraversal(p: string): boolean {
    const normalised = path.normalize(p);
    return normalised.includes('..' + path.sep) || normalised.endsWith('..') || normalised.startsWith('..' + path.sep);
}

function isValidPythonPath(p: string): boolean {
    const base = path.basename(p).toLowerCase();
    return base.includes('python');
}

async function resolvePythonPath(): Promise<string> {
    // 1. Official Python extension interpreter (if installed)
    const pythonExtPath = vscode.workspace.getConfiguration('python').get<string>('pythonPath');
    if (pythonExtPath && pythonExtPath.trim().length > 0) {
        return pythonExtPath.trim();
    }

    // 2. Omni-Auditor manual setting
    const omniPath = vscode.workspace.getConfiguration('omniAuditor').get<string>('pythonPath');
    if (omniPath && omniPath.trim().length > 0) {
        return omniPath.trim();
    }

    // 3. Platform fallback
    return process.platform === 'win32' ? 'python' : 'python3';
}

async function resolveProjectRoot(filePath?: string): Promise<string | undefined> {
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
    // 3. Prompt user
    const input = await vscode.window.showInputBox({
        prompt: 'Omni-Auditor project root (directory containing src/main.py)',
        placeHolder: 'e.g. /home/user/omni-auditor',
        validateInput: (value) => {
            if (!value) { return 'Path is required'; }
            if (containsTraversal(value)) { return 'Path cannot contain ".." traversal'; }
            if (!fs.existsSync(path.join(value, 'src', 'main.py'))) {
                return 'No src/main.py found in that directory';
            }
            return undefined;
        },
    });
    return input ? path.resolve(input) : undefined;
}

export class ApiClient {
    private readonly cache = new Map<string, CacheEntry>();
    private readonly inFlight = new Map<string, Promise<OmniReport | null>>();
    private readonly _onDidChange = new vscode.EventEmitter<string>();
    public readonly onDidChange = this._onDidChange.event;
    private outputWatcher?: vscode.FileSystemWatcher;

    constructor(private readonly context: vscode.ExtensionContext) {
        const outPath = this.getOutputJsonPath();
        if (outPath) {
            this.outputWatcher = vscode.workspace.createFileSystemWatcher(outPath);
            this.outputWatcher.onDidChange(() => this.handleOutputJsonChanged());
            this.outputWatcher.onDidCreate(() => this.handleOutputJsonChanged());
            this.context.subscriptions.push(this.outputWatcher);
        }
    }

    private getConfig() {
        const cfg = vscode.workspace.getConfiguration('omniAuditor');
        return {
            pythonPath: cfg.get<string>('pythonPath') || '',
            projectRoot: cfg.get<string>('projectRoot') || '',
            threshold: cfg.get<number>('threshold') || 0.7,
        };
    }

    private getOutputJsonPath(): string | undefined {
        const { projectRoot } = this.getConfig();
        const root = projectRoot || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        return root ? path.join(root, 'output.json') : undefined;
    }

    private handleOutputJsonChanged(): void {
        const p = this.getOutputJsonPath();
        if (!p || !fs.existsSync(p)) { return; }
        try {
            const report: OmniReport = JSON.parse(fs.readFileSync(p, 'utf-8'));
            this.cache.set(report.file_path, { report, timestamp: Date.now() });
            this._onDidChange.fire(report.file_path);
        } catch { /* ignore */ }
    }

    public clearCache(): void { this.cache.clear(); }

    public getCached(filePath: string): OmniReport | null {
        const e = this.cache.get(filePath);
        if (!e) { return null; }
        if (Date.now() - e.timestamp > CACHE_TTL_MS) { this.cache.delete(filePath); return null; }
        return e.report;
    }

    public async analyze(filePath: string, token?: vscode.CancellationToken): Promise<OmniReport | null> {
        const cached = this.getCached(filePath);
        if (cached) { return cached; }
        const existing = this.inFlight.get(filePath);
        if (existing) { return existing; }
        const promise = this.runAnalysis(filePath, token);
        this.inFlight.set(filePath, promise);
        try { return await promise; } finally { this.inFlight.delete(filePath); }
    }

    private async runAnalysis(filePath: string, token?: vscode.CancellationToken): Promise<OmniReport | null> {
        let { pythonPath, projectRoot, threshold } = this.getConfig();
        pythonPath = await resolvePythonPath();

        // Resolve projectRoot if not explicitly configured
        if (!projectRoot) {
            const detected = await resolveProjectRoot(filePath);
            if (!detected) {
                vscode.window.showErrorMessage('Omni-Auditor: Could not determine project root. Please set omniAuditor.projectRoot in settings.');
                return null;
            }
            projectRoot = detected;
        }

        // Validate projectRoot
        if (containsTraversal(projectRoot)) {
            vscode.window.showErrorMessage(`Omni-Auditor: projectRoot contains path traversal: "${projectRoot}"`);
            return null;
        }
        if (!isInsideWorkspace(projectRoot)) {
            vscode.window.showErrorMessage(`Omni-Auditor: projectRoot must be inside the current workspace.`);
            return null;
        }
        if (!fs.existsSync(path.join(projectRoot, 'src', 'main.py'))) {
            vscode.window.showErrorMessage(`Omni-Auditor: src/main.py not found in "${projectRoot}".`);
            return null;
        }

        // Validate pythonPath
        if (!isValidPythonPath(pythonPath)) {
            vscode.window.showErrorMessage(`Omni-Auditor: pythonPath must contain "python" in its filename. Got: "${pythonPath}"`);
            return null;
        }
        if (!fs.existsSync(pythonPath) && pythonPath !== 'python' && pythonPath !== 'python3') {
            // 'python' and 'python3' are resolved by PATH; anything else must exist.
            const resolved = path.resolve(projectRoot, pythonPath);
            if (!fs.existsSync(resolved)) {
                vscode.window.showErrorMessage(`Omni-Auditor: pythonPath does not exist: "${pythonPath}"`);
                return null;
            }
            pythonPath = resolved;
        }

        return new Promise((resolve) => {
            const args = ['-m', 'src.main', filePath, '--json', '--threshold', String(threshold)];
            const proc = spawn(pythonPath, args, { cwd: projectRoot, shell: false });
            let stdout = '', stderr = '';
            token?.onCancellationRequested(() => { proc.kill(); resolve(null); });
            proc.stdout.on('data', (d: Buffer) => { stdout += d.toString(); });
            proc.stderr.on('data', (d: Buffer) => { stderr += d.toString(); });
            proc.on('error', (err: Error & { code?: string }) => {
                if (err.code === 'ENOENT') {
                    vscode.window.showErrorMessage(`Omni-Auditor: Python not found at "${pythonPath}".`);
                } else {
                    vscode.window.showErrorMessage(`Omni-Auditor: ${err.message}`);
                }
                resolve(null);
            });
            proc.on('close', (code: number | null) => {
                if (code !== 0) {
                    vscode.window.showWarningMessage(`Omni-Auditor: ${stderr.trim() || 'exit ' + code}`);
                    resolve(null); return;
                }

                // ── Primary: parse JSON from stdout ──────────────────────────
                const m = stdout.match(/(\{[\s\S]*\})/);
                const candidate = m ? m[1] : stdout;
                let report: OmniReport | null = null;
                try {
                    report = JSON.parse(candidate);
                } catch {
                    // stdout unreadable — fall through to output.json
                }

                // ── Fallback: read output.json from project root ─────────────
                if (!report) {
                    const fallbackPath = path.join(projectRoot, 'output.json');
                    if (fallbackPath && fs.existsSync(fallbackPath)) {
                        try {
                            const content = fs.readFileSync(fallbackPath, 'utf-8');
                            if (content.trim()) {
                                report = JSON.parse(content) as OmniReport;
                            }
                        } catch (fallbackErr) {
                            console.error('[Omni-Auditor] output.json fallback failed:', fallbackErr);
                        }
                    }
                }

                if (report) {
                    this.cache.set(filePath, { report, timestamp: Date.now() });
                    resolve(report);
                } else {
                    vscode.window.showErrorMessage('Omni-Auditor: JSON parse error. See console.');
                    console.error('[Omni-Auditor] stdout:', stdout);
                    resolve(null);
                }
            });
        });
    }
}
