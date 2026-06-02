import * as vscode from 'vscode';
import type { OmniReport } from '../types';

export class RiskDashboardPanel {
    public static readonly viewType = 'omni-auditor.dashboard';
    private panel?: vscode.WebviewPanel;
    private disposables: vscode.Disposable[] = [];

    constructor(private readonly context: vscode.ExtensionContext) {}

    public show(report: OmniReport): void {
        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.One);
            this.panel.webview.postMessage({ type: 'update', report });
            return;
        }
        this.panel = vscode.window.createWebviewPanel(
            RiskDashboardPanel.viewType,
            'Omni-Auditor Risk Dashboard',
            vscode.ViewColumn.One,
            { enableScripts: true, retainContextWhenHidden: true }
        );
        this.panel.webview.html = this.getHtml(report);
        this.panel.onDidDispose(() => { this.panel = undefined; this.disposables.forEach(d => d.dispose()); this.disposables = []; }, null, this.disposables);
    }

    public update(report: OmniReport): void {
        this.panel?.webview.postMessage({ type: 'update', report });
    }

    private getHtml(report: OmniReport): string {
        const csp = this.panel!.webview.cspSource;
        const data = JSON.stringify(report);
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src ${csp} 'unsafe-inline' https://cdn.jsdelivr.net; style-src ${csp} 'unsafe-inline'; connect-src ${csp};">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Omni-Auditor Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0b0c15;color:#e2e4f0;margin:0;padding:24px;}
header{display:flex;align-items:center;gap:12px;margin-bottom:24px;}
header h1{margin:0;font-size:1.4rem;}
.kpi{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px;}
.card{background:#161827;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;text-align:center;}
.card .value{font-size:2rem;font-weight:800;}
.card .label{font-size:.75rem;color:#7a7d9c;text-transform:uppercase;letter-spacing:1px;margin-top:4px;}
.tier-CRITICAL{color:#ef4444;} .tier-HIGH{color:#f97316;} .tier-MEDIUM{color:#facc15;} .tier-LOW{color:#22c55e;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px;}
.chart-card{background:#161827;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px;}
.chart-title{font-size:.85rem;color:#7a7d9c;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px;}
table{width:100%;border-collapse:collapse;font-size:.85rem;}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid rgba(255,255,255,0.06);}
th{color:#7a7d9c;text-transform:uppercase;font-size:.7rem;letter-spacing:1px;}
.sev-CRITICAL{color:#ef4444;font-weight:700;} .sev-HIGH{color:#f97316;font-weight:600;} .sev-MEDIUM{color:#facc15;} .sev-LOW{color:#22c55e;}
.gauge-wrap{position:relative;width:160px;height:80px;margin:0 auto;}
.gauge-wrap canvas{display:block;}
.gauge-value{position:absolute;bottom:0;left:50%;transform:translateX(-50%);font-size:1.6rem;font-weight:800;}
</style>
</head>
<body>
<header><span style="font-size:1.6rem">🛡️</span><h1>Omni-Auditor Risk Dashboard</h1></header>
<div class="kpi">
  <div class="card">
    <div class="gauge-wrap"><canvas id="gauge"></canvas><div class="gauge-value" id="gaugeVal">0%</div></div>
    <div class="label">Risk Score</div>
  </div>
  <div class="card">
    <div class="value" id="tierVal">--</div>
    <div class="label">Risk Tier</div>
  </div>
  <div class="card">
    <div class="value" id="findVal">0</div>
    <div class="label">Security Findings</div>
  </div>
</div>
<div class="grid">
  <div class="chart-card"><div class="chart-title">Fusion Weights</div><canvas id="fusionChart"></canvas></div>
  <div class="chart-card"><div class="chart-title">Security Severity Distribution</div><canvas id="severityChart"></canvas></div>
</div>
<div class="chart-card" style="margin-bottom:24px;">
  <div class="chart-title">Per-Function 14-D Spectral Feature Vectors</div>
  <canvas id="vectorChart"></canvas>
</div>
<div class="chart-card">
  <div class="chart-title">Security Findings</div>
  <div style="overflow:auto;max-height:320px;">
    <table><thead><tr><th>Severity</th><th>Category</th><th>Line</th><th>Node</th><th>Confidence</th></tr></thead><tbody id="findingsBody"></tbody></table>
  </div>
</div>
<script>
const report = ${data};
let fusionChart, severityChart, vectorChart, gaugeChart;
window.activeCharts = [];

function destroyCharts() {
  window.activeCharts.forEach(c => c.destroy());
  window.activeCharts = [];
}

function init() {
  destroyCharts();
  renderGauge(report.unified_risk_score);
  document.getElementById('tierVal').textContent = report.risk_tier;
  document.getElementById('tierVal').className = 'value tier-' + report.risk_tier;
  document.getElementById('findVal').textContent = report.security_findings.length;
  renderFusion();
  renderSeverity();
  renderVectors();
  renderTable();
}

function renderGauge(score) {
  const pct = Math.round(score * 100);
  document.getElementById('gaugeVal').textContent = pct + '%';
  const ctx = document.getElementById('gauge').getContext('2d');
  gaugeChart = new Chart(ctx, {
    type: 'doughnut',
    data: { labels: ['Risk','Remain'], datasets: [{ data: [pct, 100-pct], backgroundColor: [colorForTier(report.risk_tier),'#1e2035'], borderWidth:0 }] },
    options: { circumference: 180, rotation: 270, cutout: '75%', plugins: { legend: { display: false }, tooltip: { enabled: false } }, animation: false }
  });
  window.activeCharts.push(gaugeChart);
}

function colorForTier(t) {
  return { CRITICAL:'#ef4444', HIGH:'#f97316', MEDIUM:'#facc15', LOW:'#22c55e' }[t] || '#94a3b8';
}

function renderFusion() {
  const ctx = document.getElementById('fusionChart').getContext('2d');
  fusionChart = new Chart(ctx, {
    type: 'bar',
    data: { labels: ['Analyzer','Validator','Security'], datasets: [{ data: report.fusion_weights, backgroundColor: ['#38bdf8','#fbbf24','#c084fc'], borderWidth:0 }] },
    options: { indexAxis:'y', scales: { x:{max:1,grid:{color:'rgba(255,255,255,0.06)'},ticks:{color:'#7a7d9c'}}, y:{grid:{display:false},ticks:{color:'#e2e4f0'}} }, plugins: { legend:{display:false} } }
  });
  window.activeCharts.push(fusionChart);
}

function renderSeverity() {
  const counts = { CRITICAL:0, HIGH:0, MEDIUM:0, LOW:0 };
  report.security_findings.forEach(f => counts[f.severity]++);
  const ctx = document.getElementById('severityChart').getContext('2d');
  severityChart = new Chart(ctx, {
    type: 'bar',
    data: { labels: Object.keys(counts), datasets: [{ data: Object.values(counts), backgroundColor: ['#ef4444','#f97316','#facc15','#22c55e'], borderWidth:0 }] },
    options: { scales: { y:{grid:{color:'rgba(255,255,255,0.06)'},ticks:{color:'#7a7d9c'}}, x:{grid:{display:false},ticks:{color:'#e2e4f0'}} }, plugins: { legend:{display:false} } }
  });
  window.activeCharts.push(severityChart);
}

function renderVectors() {
  const labels = report.per_function_metrics.map(m => m.function_key.split('@')[0]);
  const dims = report.per_function_metrics[0]?.raw_feature_vector.length || 0;
  const colors = ['#38bdf8','#fbbf24','#c084fc','#4ade80','#f472b6','#60a5fa','#fb923c','#a78bfa','#34d399','#e879f9','#2dd4bf','#f87171','#818cf8','#a3e635'];
  const datasets = [];
  for (let d = 0; d < dims; d++) {
    datasets.push({ label: 'Dim ' + d, data: report.per_function_metrics.map(m => m.raw_feature_vector[d]), backgroundColor: colors[d % colors.length], borderWidth:0 });
  }
  const ctx = document.getElementById('vectorChart').getContext('2d');
  vectorChart = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    options: { scales: { x:{stacked:true,grid:{display:false},ticks:{color:'#e2e4f0'}}, y:{stacked:true,grid:{color:'rgba(255,255,255,0.06)'},ticks:{color:'#7a7d9c'}} }, plugins: { legend:{display:false} } }
  });
  window.activeCharts.push(vectorChart);
}

function renderTable() {
  const tbody = document.getElementById('findingsBody');
  tbody.innerHTML = '';
  report.security_findings.forEach(f => {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td class="sev-'+f.severity+'">'+f.severity+'</td><td>'+f.category+'</td><td>'+f.line_number+'</td><td>'+f.node_path+'</td><td>'+(f.confidence_score*100).toFixed(0)+'%</td>';
    tbody.appendChild(tr);
  });
}

window.addEventListener('message', event => {
  const message = event.data;
  if (message.type === 'update') {
    Object.assign(report, message.report);
    init();
  }
});

init();
</script>
</body>
</html>`;
    }
}
