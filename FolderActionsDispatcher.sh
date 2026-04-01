#!/bin/bash

# 가상환경 경로 설정
VENV_PATH="$HOME/.venvs/systools"

# 가상환경 내 Python 실행 경로
PYTHON_EXEC="$VENV_PATH/bin/python"

# Python 스크립트 경로
PYTHON_SCRIPT="$HOME/.local/bin/FolderActionsDispatcher.py"

# 가상환경 활성화
source "$VENV_PATH/bin/activate"

# Python 스크립트 실행
"$PYTHON_EXEC" "$PYTHON_SCRIPT" "$@"

# 가상환경 비활성화 (필수는 아님)
deactivate

