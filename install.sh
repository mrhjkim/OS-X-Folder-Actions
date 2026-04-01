#!/usr/bin/env bash
# install.sh — one-step installer for OS-X-Folder-Actions
# Usage: ./install.sh
set -euo pipefail

VENV="$HOME/.venvs/systools"
BIN="$HOME/.local/bin"
SCRIPTS_DIR="$HOME/Library/Scripts/Folder Action Scripts"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing OS-X-Folder-Actions..."
echo "  Repo : $REPO_DIR"
echo "  Venv : $VENV"
echo "  Bin  : $BIN"

# 0. Ensure ~/.local/bin exists and is on PATH
mkdir -p "$BIN"
if [[ ":$PATH:" != *":$BIN:"* ]]; then
    echo "Adding $BIN to PATH in ~/.zshrc..."
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
    echo "  Note: run 'source ~/.zshrc' or open a new terminal after install."
fi

# 1. Create venv if it doesn't exist
if [ ! -d "$VENV" ]; then
    echo "Creating virtualenv at $VENV..."
    python3 -m venv "$VENV"
fi

# 2. Install Python dependencies
echo "Installing Python dependencies..."
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

# 3. Copy Python scripts
echo "Copying Python scripts to $BIN..."
cp "$REPO_DIR/FolderActionsDispatcher.py" "$BIN/"
cp "$REPO_DIR/.FolderActions.py"          "$BIN/"
cp "$REPO_DIR/AuditLogger.py"             "$BIN/"
cp "$REPO_DIR/ContentExtractor.py"        "$BIN/"
cp "$REPO_DIR/AIProvider.py"              "$BIN/"
cp "$REPO_DIR/FolderActionsLog.py"        "$BIN/"

# 4. Install dispatcher shell script
echo "Installing FolderActionsDispatcher.sh..."
cp "$REPO_DIR/FolderActionsDispatcher.sh" "$BIN/"
chmod +x "$BIN/FolderActionsDispatcher.sh"

# 5. Install folder-actions CLI wrapper
echo "Installing folder-actions CLI..."
cat > "$BIN/folder-actions" <<EOF
#!/bin/bash
source "$VENV/bin/activate"
# Strip optional 'log' subcommand: 'folder-actions log --watch' == 'folder-actions --watch'
args=("\$@")
if [ "\${args[0]:-}" = "log" ]; then
  args=("\${args[@]:1}")
fi
python "$BIN/FolderActionsLog.py" "\${args[@]}"
EOF
chmod +x "$BIN/folder-actions"

# 6. Copy AppleScript to Folder Action Scripts directory
echo "Installing AppleScript..."
mkdir -p "$SCRIPTS_DIR"
cp "$REPO_DIR/Send Events To Shell Script.applescript" "$SCRIPTS_DIR/"

echo ""
echo "Done!"
echo ""
echo "Next steps:"
echo "  1. Add .FolderActions.yaml to any folder you want to watch"
echo "  2. Attach the AppleScript: right-click folder → Services → Folder Actions Setup"
echo "  3. (Optional) Install Ollama and a model for AI rules: https://ollama.ai"
echo "  4. View your audit log: folder-actions log"
echo ""
echo "Note: AI rules add up to 10s processing time per file when Ollama is running."
