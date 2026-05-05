# Omni-Auditor for VS Code

Visual Studio Code extension for [Omni-Auditor](https://github.com/SFN2002/omni-auditor), a research-grade static analysis engine for Python using Spectral Graph Theory, Rényi Entropy, and Mahalanobis anomaly detection.

## Requirements

- Python 3.x
- Omni-Auditor CLI installed:
  ```bash
  pip install omni-auditor
  ```

## Features

- **Status Bar Risk Score**: Real-time composite risk score with color coding.
- **CodeLens Annotations**: Inline function health indicators (`✅ Healthy`, `🌡️ Elevated`, `🔥 Complex`).
- **Diagnostics**: Security findings mapped to the Problems panel with severity-aware icons.
- **Run on Save**: Automatic analysis when saving Python files.
- **Workspace Scan**: Analyze up to 20 Python files across your workspace.

## Extension Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `omniAuditor.cliPath` | `omni-auditor` | Path to the CLI executable |
| `omniAuditor.runOnSave` | `true` | Run analysis on file save |

## Usage

1. Open a Python file or workspace.
2. The extension activates automatically.
3. View the Risk Score in the status bar.
4. Open the Command Palette (`Ctrl+Shift+P`) and run:
   - **Omni-Auditor: Scan File**
   - **Omni-Auditor: Scan Workspace**

## Build & Package

```bash
cd vscode-extension
npm install
npm run compile
npx vsce package
```

Install the resulting `.vsix` file via the Extensions panel (**Install from VSIX**).
