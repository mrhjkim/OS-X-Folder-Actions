import os
import shutil
import logging
import subprocess
import unicodedata
import yaml

# 로그 파일 설정
LOG_FILE = os.path.expanduser("~/Desktop/FolderActions.log")
CONFIG_FILE = ".FolderActions.conf"

# 로깅 설정
#logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s - %(message)s')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a"),  # 로그 파일 기록
        logging.StreamHandler()  # 터미널에도 출력
    ]
)

def log(message):
    logging.info(message)
    """macOS 알림 센터에 메시지 표시"""
    subprocess.run(["osascript", "-e", f'display notification "{message}" with title "Folder Actions"'])

def folder_opened(folder):
    log(f"Folder {folder} opened")

def folder_closed(folder):
    log(f"Folder {folder} closed")

def item_added_to_folder(folder, item):
    log(f"Item {item} added to folder {folder}")
    apply_rule_by_yaml_config(folder, item)

def item_removed_from_folder(folder, item):
    log(f"Item {item} removed from folder {folder}")

def move_file_by_config(folder, item):
    item_path = os.path.join(folder, item)
    config_path = os.path.join(folder, CONFIG_FILE)
    
    # 파일이 존재하는지 확인
    if not os.path.isfile(item_path):
        log(f"File not found: {item_path}")
        return False
    
    # 설정 파일이 존재하는지 확인
    if not os.path.isfile(config_path):
        log(f"Config file not found: {config_path}")
        return False
    
    with open(config_path, "r", encoding="utf-8") as config:
        for line in config:
            try:
                keyword, target_folder = map(str.strip, line.split(":", 1))
            except ValueError:
                continue  # 잘못된 형식의 라인은 무시
            
            # NFC 변환 적용 (Mac의 NFD 문제 해결)
            item_nfc = unicodedata.normalize("NFC", item)
            keyword_nfc = unicodedata.normalize("NFC", keyword)
            
            if keyword_nfc in item_nfc:
                target_folder = os.path.expanduser(target_folder)  # ~ 확장
                
                # 대상 폴더가 존재하는지 확인하고 없으면 생성
                if not os.path.isdir(target_folder):
                    log(f"Target folder not found: {target_folder}. Creating folder.")
                    os.makedirs(target_folder, exist_ok=True)
                
                # 파일 이동
                target_path = os.path.join(target_folder, item)
                shutil.move(item_path, target_path)
                log(f"Moved {item_path} to {target_path}")
                return True
    
    log(f"No matching rule for {item} in {folder}")
    return False

def apply_rule_by_yaml_config(folder, item):
    item_path = os.path.join(folder, item)
    config_path = os.path.join(folder, ".FolderActions.yaml")
    
    # 파일이 존재하는지 확인
    if not os.path.isfile(item_path) and not os.path.isdir(item_path):
        log(f"File not found: {item_path}")
        return False
    
    # 설정 파일이 존재하는지 확인
    if not os.path.isfile(config_path):
        log(f"Config file not found: {config_path}")
        return False
    
    with open(config_path, "r", encoding="utf-8") as config_file:
        try:
            config = yaml.safe_load(config_file)
        except yaml.YAMLError as exc:
            log(f"Error parsing YAML config: {exc}")
            return False
    # NFC 변환 적용 (Mac의 NFD 문제 해결)
    item_nfc = unicodedata.normalize("NFC", item)

    for rule in config.get("Rules", []):
        criteria = rule.get("Criteria", [])
        actions = rule.get("Actions", [])
        
        if not criteria or not actions:
            continue  # 잘못된 형식의 라인은 무시
        
        if all(match_criteria(item_nfc, criterion) for criterion in criteria):
            for action in actions:
                if "MoveToFolder" in action:
                    target_folder = os.path.expanduser(action["MoveToFolder"])
                    
                    # 대상 폴더가 존재하는지 확인하고 없으면 생성
                    if not os.path.isdir(target_folder):
                        log(f"Target folder not found: {target_folder}. Creating folder.")
                        os.makedirs(target_folder, exist_ok=True)
                    
                    # 파일 이동
                    target_path = os.path.join(target_folder, item)
                    shutil.move(item_path, target_path)
                    log(f"Moved {item_path} to {target_path}")
                elif "RunShellScript" in action:
                    script_or_command = action["RunShellScript"]
                    
                    # 명령어 실행
                    try:
                        env = os.environ.copy()
                        env["FILENAME"] = item_path  # 환경변수에 파일 경로 추가
                        result = subprocess.run(script_or_command, shell=True, check=True, capture_output=True, env=env, cwd=folder)
                        if result.returncode == 0:
                            log(f"Successfully executed shell command: {script_or_command} with FILENAME={item_path}")
                        else:
                            log(f"Shell command {script_or_command} exited with return code {result.returncode}")
                    except subprocess.CalledProcessError as e:
                        log(f"Error executing shell command: {script_or_command}: {e}\nstdout: {e.stdout.decode()}\nstderr: {e.stderr.decode()}")
                        continue
            return True
    log(f"No matching rule for {item} in {folder}")
    return False

def match_criteria(item, criterion):
    item_name, item_extension = os.path.splitext(item)
    item_extension = item_extension.lstrip('.')
    
    for key, value in criterion.items():
        if key == "AllCriteria":
            if not all(match_criteria(item, sub_criterion) for sub_criterion in value):
                return False
        elif key == "AnyCriteria":
            if not any(match_criteria(item, sub_criterion) for sub_criterion in value):
                return False
        elif key == "FileExtension" and item_extension != value:
            return False
        elif key == "FileNameContains" and value not in item_name:
            return False
        else:
            continue
    
    return True
