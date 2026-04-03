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

4. **folder-actions log / dashboard** — 두 가지 방법으로 기록을 확인할 수 있습니다:
   - `folder-actions log` — JSONL 감사 로그 조회 CLI (`--file`, `--rule`, `--since`, `--watch`)
   - `folder-actions dashboard` — 인터랙티브 웹 대시보드: 로그 탐색, 미매칭 파일 확인, 규칙 편집, `.FolderActions.yaml`에 바로 저장

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
  TimeoutSeconds: 60      # 선택 사항, 기본값 60초 — 대형 모델 사용 시 늘릴 것
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

5. 또는 시각적 대시보드를 열어 로그를 탐색하고 규칙을 편집합니다:

```bash
folder-actions dashboard        # 브라우저에서 http://localhost:7373 열기
folder-actions dashboard --port 8080
```

대시보드는 실시간 감사 로그를 읽어 어떤 파일이 매칭됐는지(또는 안 됐는지) 보여주고, 규칙을 직접 편집하여 한 번의 클릭으로 `.FolderActions.yaml`에 저장할 수 있습니다.

## AiAgent 액션

일반 `Rules` 아래에 `AiAgent:` 를 추가하면 파일 이동만 하는 대신, 매칭된 파일에 대해
AI CLI 명령을 실행할 수 있습니다.

```yaml
Rules:
  - Title: "PDF 요약"
    Criteria:
      - FileExtension: pdf
    Actions:
      - AiAgent:
          Model: claude
          PromptFile: ~/.config/folder-actions/summarize-pdf.txt
          AllowDangerousPermissions: true   # opt-in: Claude가 확인 없이 동작하도록 허용
      - MoveToFolder: ~/Documents/PDFs/
```

프롬프트 템플릿에서 사용할 수 있는 변수:
- `{filepath}` 전체 파일 경로
- `{filename}` 확장자 없는 파일명
- `{basename}` 확장자 포함 파일명
- `{folder}` 파일이 있는 폴더 경로
- `{ext}` 점 없는 확장자

메모:
- 이번 릴리스에서 검증된 provider: `claude`, `codex`
- `gemini` 는 설정 이름은 예약되어 있지만 아직 검증되지 않아 명시적 오류를 반환합니다
- 액션은 순차적으로 실행되므로 `MoveToFolder` 다음 `AiAgent` 는 이동된 경로 기준으로 실행됩니다
- 대시보드는 YAML 저장 시 `AiAgent` 액션을 보존합니다
- AiAgent 액션은 동기식으로 실행됩니다 — AI 명령이 완료될 때까지 핸들러가 블로킹됩니다 (최대 `TimeoutSeconds`, 기본값 120초). 파일이 자주 드롭되는 폴더에서는 `TimeoutSeconds`를 낮게 설정하세요.

**보안 참고:** 기본적으로 `AllowDangerousPermissions`는 `false`입니다 — AI 에이전트는 파괴적인 작업 전에 확인을 요청합니다. 프롬프트 템플릿과 드롭되는 파일을 완전히 신뢰하는 자동화 워크플로에서만 `true`로 설정하세요.
