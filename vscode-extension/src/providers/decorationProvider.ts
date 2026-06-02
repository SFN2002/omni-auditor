import * as vscode from 'vscode';
import type { SecurityFinding, Severity } from '../types';

const SEVERITY_CONFIG: Record<Severity, { svg: string; background: string; overviewColor: string }> = {
    CRITICAL: { svg: 'images/dot-critical.svg', background: 'rgba(244, 67, 54, 0.15)', overviewColor: 'rgba(244, 67, 54, 1)' },
    HIGH:     { svg: 'images/dot-high.svg',     background: 'rgba(255, 152, 0, 0.15)', overviewColor: 'rgba(255, 152, 0, 1)' },
    MEDIUM:   { svg: 'images/dot-medium.svg',   background: 'rgba(255, 235, 59, 0.15)', overviewColor: 'rgba(255, 235, 59, 1)' },
    LOW:      { svg: 'images/dot-low.svg',      background: 'rgba(76, 175, 80, 0.15)', overviewColor: 'rgba(76, 175, 80, 1)' },
};

export class DecorationProvider implements vscode.Disposable {
    private readonly types: Record<Severity, vscode.TextEditorDecorationType>;

    constructor(extensionUri: vscode.Uri) {
        this.types = {
            CRITICAL: this.createType('CRITICAL', extensionUri),
            HIGH: this.createType('HIGH', extensionUri),
            MEDIUM: this.createType('MEDIUM', extensionUri),
            LOW: this.createType('LOW', extensionUri),
        };
    }

    private createType(severity: Severity, extensionUri: vscode.Uri): vscode.TextEditorDecorationType {
        const cfg = SEVERITY_CONFIG[severity];
        const iconUri = vscode.Uri.joinPath(extensionUri, cfg.svg);
        return vscode.window.createTextEditorDecorationType({
            backgroundColor: cfg.background,
            overviewRulerColor: cfg.overviewColor,
            overviewRulerLane: vscode.OverviewRulerLane.Right,
            gutterIconPath: iconUri,
            isWholeLine: true,
        });
    }

    public applyDecorations(editor: vscode.TextEditor, findings: SecurityFinding[]): void {
        this.clearEditor(editor);

        const buckets: Record<Severity, vscode.DecorationOptions[]> = {
            CRITICAL: [],
            HIGH: [],
            MEDIUM: [],
            LOW: [],
        };

        for (const finding of findings) {
            const line = Math.max(0, finding.line_number - 1);
            const range = new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER);
            const hoverMessage = `[${finding.severity}] ${finding.category}: ${finding.node_path}`;
            buckets[finding.severity].push({ range, hoverMessage });
        }

        for (const sev of Object.keys(buckets) as Severity[]) {
            if (buckets[sev].length > 0) {
                editor.setDecorations(this.types[sev], buckets[sev]);
            }
        }
    }

    public clearDecorations(): void {
        for (const editor of vscode.window.visibleTextEditors) {
            this.clearEditor(editor);
        }
    }

    public clearEditor(editor: vscode.TextEditor): void {
        for (const sev of Object.keys(this.types) as Severity[]) {
            editor.setDecorations(this.types[sev], []);
        }
    }

    public dispose(): void {
        for (const t of Object.values(this.types)) {
            t.dispose();
        }
    }
}
