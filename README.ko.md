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
   - `folder-actions dashboard` — 인터랙티브 웹 대시보드: 로그 탐색, 미매칭 파일 확인, 규칙 편집, `.FolderActions.yaml`에 바로 저장, 이미 폴더에 있는 파일에 규칙을 소급 적용

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

# 선택 사항: AI 규칙 — YAML 규칙이 안 맞을 때 내용으로 분류.
# 기본 백엔드는 로컬 Ollama(https://ollama.ai) — 파일이 맥을 벗어나지 않음.
AiRules:
  Model: llama3.2
  ConfidenceThreshold: 0.8
  TimeoutSeconds: 60      # 선택 사항, 기본값 60초(양쪽 백엔드)
  Rules:
    - Title: "세금 문서"
      Description: "세금 영수증, 청구서 또는 재무 기록"
      Actions:
        - MoveToFolder: ~/Documents/Finance/Tax/

# 감사 로그는 기본적으로 활성화 (~/.folder-actions-log/)
# 비활성화: Audit: {Enabled: false}
```

### AI 백엔드: Ollama(로컬) 또는 Gemini(API 키)

`AiRules`는 파일 내용으로 분류합니다. 기본 백엔드는 **Ollama**로 로컬에서 돌아
파일 내용이 맥을 벗어나지 않습니다. 대신 구글 **Gemini** API를 쓰려면 `Provider`를
추가하고 키 파일을 가리키게 합니다:

```yaml
AiRules:
  Provider: gemini                                   # 생략 시 ollama (로컬, 기본값)
  Model: gemini-3.5-flash                            # 최신 모델 ID는 ai.google.dev 확인
  ApiKeyFile: ~/.config/folder-actions/gemini.key
  ConfidenceThreshold: 0.8
  Rules:
    - Title: "청구서"
      Description: "인보이스, 영수증, 결제 내역"
      Actions:
        - MoveToFolder: ~/Documents/Invoices/
```

키는 저장소 밖 파일에 두며, git이 추적하는 `.FolderActions.yaml`에는 절대 넣지
않습니다:

```bash
mkdir -p ~/.config/folder-actions
printf '%s' 'YOUR_GEMINI_API_KEY' > ~/.config/folder-actions/gemini.key
chmod 600 ~/.config/folder-actions/gemini.key
```

키 조회 순서: `GEMINI_API_KEY` 환경변수 우선(터미널에서 편리), 그다음 `ApiKeyFile`.
macOS Folder Actions는 환경이 거의 없는 GUI 데몬으로 실행되므로 **실제 파일 드롭에서
동작하게 만드는 건 키 파일**입니다. 환경변수만 두면 모든 테스트는 통과하지만 정작
파일을 떨어뜨릴 때 조용히 실패합니다.

> ⚠️ **`Provider: gemini`는 파일 내용을 맥 밖으로 보냅니다.** 분류되는 모든 파일
> (PDF, docx, xlsx, txt)의 본문 앞 4096자가 구글 API로 전송됩니다. 감시 폴더에는 보통
> 청구서·계약서·급여명세서가 들어옵니다. 민감한 폴더라면 로컬 Ollama 백엔드를 쓰세요.

### SemanticRules: 무료 로컬 분류 (토큰 없음)

`AiRules`는 파일마다 토큰(또는 Ollama의 CPU)을 씁니다. **`SemanticRules`**는 그 *앞에* 도는
무료 계층입니다: 로컬 ONNX 모델(`fastembed`, API 키 없음, 첫 다운로드 후 오프라인)로 문서를
임베딩해서 예시 문구가 가장 가까운 카테고리를 고릅니다. 확신하는 건 **$0**로 이동하고, 정말
애매한 것만 유료 LLM으로 넘어갑니다.

```yaml
SemanticRules:
  Model: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 다국어, fastembed 0.8.0 확인
  SimilarityThreshold: 0.5           # 미만이면 AiRules로 폴백
  EmbedSource: content               # content | filename | both
  Rules:
    - Title: "청구서"
      Utterances:                    # 예시 문구 몇 개 = 학습 전체
        - "세금계산서 공급가액 부가세 청구 금액"
        - "영수증 결제 내역 카드 승인"
      Actions:
        - MoveToFolder: ~/Documents/Invoices
    - Title: "설계문서"
      EmbedSource: filename          # 규칙별 override
      Utterances:
        - "설계문서 상세 설계 아키텍처"
      Actions:
        - MoveToFolder: ~/Documents/Design
```

파이프라인: `Rules`(파일명, 무료) → `SemanticRules`(벡터, 무료) → `AiRules`(LLM, 유료 폴백).

**`EmbedSource` — 무엇을 임베딩할지** (실제 문서로 측정):
- `content`(기본값) 는 **내용(주제)**으로 분류 — 내용이 다른 카테고리에 강함(청구서/계약서/이력서).
- `filename` 은 문서 **종류/형식**으로 분류. 종류가 파일명에 있을 때("주간업무보고", "설계문서").
  같은 주제·다른 형식이라 내용으로는 안 나뉘는 카테고리에 씁니다.
- `both` 는 파일명 + 내용 연결 (남용 금지 — 내용이 파일명 신호를 희석할 수 있음).

메모:
- `Utterances`는 짧은 예시 문구(카테고리당 3~6개), 문서 전체가 아닙니다. 라벨도 학습도 없습니다.
- 파일명에 정확한 키워드가 항상 들어간다면("주간업무") 일반 `Rules`의 `FileNameContains`가
  더 정확·무료입니다. `SemanticRules`는 키워드가 변형될 때(유사어/오타) 쓰세요.
- 첫 분류 시 임베딩 모델(~240MB)이 `~/.cache/folder-actions/fastembed`로 받아집니다. 이후 오프라인.
- 자세한 샘플: `examples/semantic.FolderActions.yaml`.

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

대시보드는 실시간 감사 로그를 읽어 어떤 파일이 매칭됐는지(또는 안 됐는지) 보여주고, 규칙을 직접 편집하여 한 번의 클릭으로 `.FolderActions.yaml`에 저장할 수 있습니다. 또한 **소급 적용**도 지원합니다 — 새 규칙을 만들거나 기존 규칙을 변경한 경우, 규칙 카드를 펼쳐 '미리보기'를 클릭하면 아직 처리되지 않은 파일을 확인하고, '실행'을 클릭하면 해당 파일에 규칙을 적용할 수 있습니다.

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
