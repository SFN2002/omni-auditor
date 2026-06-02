# Omni-Auditor for VS Code

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)](https://github.com/yourname/omni-auditor)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/yourname/omni-auditor/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Visual Studio Code extension for [Omni-Auditor](https://github.com/yourname/omni-auditor), a research-grade static analysis engine for Python using Spectral Graph Theory, Rényi Entropy, and Mahalanobis anomaly detection.

![Omni-Auditor Demo](images/demo-placeholder.png)

> **Note:** Replace `images/demo-placeholder.png` with a recorded GIF or screenshot of the extension in action.

## Features

- **Diagnostics**: Security and complexity findings mapped to the VS Code **Problems** panel with severity-aware icons.
- **Hover Information**: Risk scores, entropy metrics, and anomaly details shown on hover for Python symbols.
- **Risk Dashboard**: Interactive webview panel with file-level risk summaries and visualizations.
- **Status Bar**: Real-time composite risk score with color coding (green / amber / red).
- **Auto-analyze on Save**: Optional automatic analysis when saving Python files.
- **Workspace Scan**: Analyze up to 20 Python files across your workspace.

## Installation

### From VSIX (Local)

1. Download `omni-auditor-0.1.0.vsix`.
2. Open VS Code and go to the **Extensions** panel (`Ctrl+Shift+X`).
3. Click the **...** menu (top-right) and select **Install from VSIX...**.
4. Choose the downloaded `.vsix` file.

### From VS Code Marketplace (Coming Soon)

Search for **"Omni-Auditor"** in the Extensions panel and click **Install**.

## Configuration

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `omniAuditor.pythonPath` | `string` | `python` | Path to the Python 3.10+ executable |
| `omniAuditor.projectRoot` | `string` | `C:\\smart-auditor` | Absolute path to the Omni-Auditor project root (directory containing `src/main.py`) |
| `omniAuditor.threshold` | `number` | `0.7` | CRITICAL risk tier threshold (0.0 – 1.0) |
| `omniAuditor.autoAnalyzeOnSave` | `boolean` | `true` | Automatically run analysis when saving a Python file |

## Commands

| Command | Title | Description |
|---------|-------|-------------|
| `omni-auditor.analyzeCurrentFile` | **Omni-Auditor: Analyze Current File** | Runs Omni-Auditor on the active Python file and updates diagnostics / status bar. |
| `omni-auditor.showRiskDashboard` | **Omni-Auditor: Show Risk Dashboard** | Opens the interactive Risk Dashboard webview for the most recently analyzed file. |

## Known Issues

- **Windows-only default path**: The default `projectRoot` points to `C:\\smart-auditor`. Linux/macOS users should update this setting after installation.
- **Large workspaces**: Scanning more than 20 Python files may require increasing VS Code's extension host timeout.
- **Python environment**: The extension relies on an external Python environment with Omni-Auditor installed (`pip install omni-auditor`).

## Contributing

Contributions are welcome! Please see the [Contributing Guide](https://github.com/yourname/omni-auditor/blob/main/CONTRIBUTING.md) for details.

## License

This extension is licensed under the [MIT License](https://github.com/yourname/omni-auditor/blob/main/LICENSE).
