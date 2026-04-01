OS-X-Folder-Actions
===================
다음에서 발견: [http://j4zzcat.wordpress.com/2010/01/06/folder-actions-unix-style](http://j4zzcat.wordpress.com/2010/01/06/folder-actions-unix-style).

## 폴더 액션, UNIX 스타일

Mac OS X에는 폴더 액션(Folder Actions)이라는 멋진 기능이 있습니다. 기본적으로 이 기능은 AppleScript 스크립트를 폴더에 연결하고, 해당 폴더에 항목이 추가되거나 제거될 때마다 스크립트를 실행할 수 있게 합니다. 간단한 예제를 보려면 [여기](https://support.apple.com/guide/script-editor/welcome/mac)에서 확인하세요.

이 스크립트를 Python으로 작성하려면 어떻게 해야 할까요? 여기에 간단하고 범용적인 솔루션이 있습니다.

이 솔루션은 네 가지 주요 구성 요소로 이루어져 있습니다:

1. **Send Events To Shell Script.applescript** — 폴더에 연결되어 Folder Action 이벤트(열기, 닫기, 추가, 제거)를 디스패처로 전달합니다.

2. **FolderActionsDispatcher.sh / FolderActionsDispatcher.py** — AppleScript로부터 이벤트를 수신하여 규칙 엔진으로 전달합니다.

3. **.FolderActions.py** — 3단계 규칙 엔진:
   - **Stage 1 (YAML 규칙):** 파일명/확장자 기반의 빠르고 결정론적인 매칭
   - **Stage 2 (AI 규칙):** YAML 규칙이 매칭되지 않을 때 [Ollama](https://ollama.ai) 로컬 LLM이 파일 내용으로 분류
   - **Stage 3 (Fallthrough):** 명시적 미매칭 로그 기록

4. **folder-actions log** — JSONL 감사 로그 조회 CLI (`--file`, `--rule`, `--since`, `--watch`).

감시할 폴더에 `.FolderActions.yaml` 파일을 작성하고 배치하기만 하면 됩니다.

## 설치

다음은 예제입니다. ~/Downloads에 배치된 모든 파일을 자동으로 특정 디렉토리로 복사하고 싶다고 가정해봅시다. 다음 단계를 수행합니다:

1. 한 번만 설정:
 
   1. 이 저장소를 클론합니다.
   2. **`./install.sh`** 를 실행합니다 — 가상 환경 생성, 의존성 설치, 스크립트를 `~/.local/bin`에 복사, `folder-actions` CLI 설정을 자동으로 수행합니다.
   3. 새 터미널을 열거나 `source ~/.zshrc`를 실행하여 `~/.local/bin`이 PATH에 포함되도록 합니다.
   4. AppleScript 연결: Finder에서 감시할 폴더를 우클릭 → **Folder Actions Setup…** → **Send Events To Shell Script.applescript** 선택.

2. `~/Downloads/.FolderActions.yaml` 파일을 생성합니다:

```yaml
Rules:
  - Title: "PDF 문서"
    Criteria:
      - FileExtension: pdf
    Actions:
      - MoveToFolder: ~/Documents/PDFs/

  - Title: "주간업무 보고서"
    Criteria:
      - AllCriteria:
          - FileExtension: xlsx
          - FileNameContains: "주간업무"
    Actions:
      - MoveToFolder: ~/Documents/Reports/

# 선택 사항: AI 규칙 (Ollama 필요 — https://ollama.ai)
AiRules:
  Model: llama3.2
  ConfidenceThreshold: 0.8
  Rules:
    - Title: "세금 문서"
      Description: "세금 영수증, 청구서 또는 재무 기록"
      Actions:
        - MoveToFolder: ~/Documents/Finance/Tax/

# 감사 로그는 기본적으로 활성화 (~/.folder-actions-log/)
# 비활성화: Audit: {Enabled: false}
```

3. `~/Downloads`에 파일을 놓으면 자동으로 이동됩니다.

4. 감사 로그 조회:

```bash
folder-actions log              # 최근 20개 항목
folder-actions log --watch      # 실시간 모니터링
folder-actions log --file invoice --since 2026-01-01
```