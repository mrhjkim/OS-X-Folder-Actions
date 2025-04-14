OS-X-Folder-Actions
===================
다음에서 발견: [http://j4zzcat.wordpress.com/2010/01/06/folder-actions-unix-style](http://j4zzcat.wordpress.com/2010/01/06/folder-actions-unix-style).

## 폴더 액션, UNIX 스타일

Mac OS X에는 폴더 액션(Folder Actions)이라는 멋진 기능이 있습니다. 기본적으로 이 기능은 AppleScript 스크립트를 폴더에 연결하고, 해당 폴더에 항목이 추가되거나 제거될 때마다 스크립트를 실행할 수 있게 합니다. 간단한 예제를 보려면 [여기](https://support.apple.com/guide/script-editor/welcome/mac)에서 확인하세요.

이 스크립트를 Python으로 작성하려면 어떻게 해야 할까요? 여기에 간단하고 범용적인 솔루션이 있습니다.

이 솔루션은 세 부분으로 구성됩니다:

1. **Send Events To Shell Script.scpt**라는 AppleScript (바이너리 파일, 내장된 AppleScript Editor를 사용하여 보기/편집 가능)

2. **FolderActionsDispatcher.sh/FolderActionsDispatcher.py**라는 스크립트

3. 대상 디렉토리의 **.FolderActions.yaml** 규칙을 처리하는 Python 스크립트 **.FolderActions.py**

**Send Events To Shell Script.scpt** 스크립트를 폴더에 연결하면, 이 스크립트는 관찰자로 작동하며 Opening, Closing, Adding 및 Removing 이벤트를 **/usr/local/bin/FolderActionsDispatcher.sh/FolderActionsDispatcher.py** 스크립트로 전달합니다. 이벤트 페이로드에는 이벤트 유형, 목적을 수행하는 데 필요한 데이터(예: Adding 이벤트의 경우 추가된 항목 목록), 그리고 이벤트 대상 폴더의 이름이 포함됩니다. **FolderActionsDispatcher.py**는 이벤트를 파싱한 후, **.FolderActions.py**라는 콜백 스크립트를 호출하려고 시도합니다. 여러분은 **.FolderActions.yaml** 구성 파일을 작성하고 해당 폴더에 배치하기만 하면 됩니다.

## 설치

다음은 예제입니다. ~/Downloads에 배치된 모든 파일을 자동으로 특정 디렉토리로 복사하고 싶다고 가정해봅시다. 다음 단계를 수행합니다:

1. 한 번만 설정:
 
   1. 이 저장소를 클론합니다.
   2. **Send Events To Shell Script.scpt**를 **~/Library/Scripts/Folder Action Scripts**로 복사합니다.
   3. **FolderActionsDispatcher.sh**와 **FolderActionsDispatcher.py**를 **/usr/local/bin**으로 복사합니다.
   4. 다음 명령어로 실행 권한을 부여합니다: _$ chmod a+x /usr/local/bin/FolderActionsDispatcher.sh_.
   5. Python 가상 환경을 생성합니다: _$ python3 -m venv ~/.venvs/systools_.
   6. **pyyaml**을 설치합니다: _$ pip install pyyaml_.
   7. **.FolderActions.py**를 **/usr/local/bin**으로 복사합니다.
   8. 대상 디렉토리에 **.FolderActions.yaml** 파일을 만듭니다.

2. **~/Downloads/.FolderActions.yaml** 파일을 생성합니다. **.FolderActions.yaml** 파일은 시작점으로 적합합니다.

3. ~/Downloads에 대해 폴더 액션을 활성화합니다. Finder 애플리케이션에서 ~/Downloads 폴더를 선택하고, 컨텍스트 메뉴를 열어 ‘Folder Actions Setup…‘을 선택합니다. 대화 상자에서 ‘Send Events To Shell Script.scpt‘ 액션을 선택하고 ‘Attach‘ 버튼을 클릭합니다.

이제 끝났습니다 :-) 테스트하려면 ~/Downloads에 파일을 배치하고 규칙에 따라 파일이 특정 디렉토리로 복사되는지 확인하세요.