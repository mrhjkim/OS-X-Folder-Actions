import os
import sys
import logging
import importlib.util

# Constants
CALLBACK_FILE = ".FolderActions.py"

# Logging setup
#logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
LOG_FILE = os.path.expanduser("~/Desktop/FolderActions.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a"),  # 로그 파일 기록
        logging.StreamHandler()  # 터미널에도 출력
    ]
)

def load_callback_module(callback_file):
    """Dynamically load the callback module from the target folder."""
    if not os.path.exists(callback_file):
        return None
    
    spec = importlib.util.spec_from_file_location("callbacks", callback_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def main():
    if len(sys.argv) < 3:
        logging.error("Usage: python script.py <event> <target_folder> [items...]")
        sys.exit(1)
    
    event = sys.argv[1]
    target_folder = sys.argv[2].rstrip('/')
    target_callback_file = os.path.join(target_folder, CALLBACK_FILE)
    
    # Load the callback module
    callbacks = load_callback_module(target_callback_file)
    if callbacks is None:
        target_callback_file = os.path.join("/usr/local/bin", CALLBACK_FILE)
        callbacks = load_callback_module(target_callback_file)
        if callbacks is None:
            logging.error("No callback module found, exiting")
            sys.exit(1)
    
    # Handle events
    if event == "opening":
        logging.info(f"Calling {target_callback_file}: folder_opened(folder: {target_folder})")
        if hasattr(callbacks, "folder_opened"):
            callbacks.folder_opened(target_folder)
    
    elif event == "closing":
        logging.info(f"Calling {target_callback_file}: folder_closed(folder: {target_folder})")
        if hasattr(callbacks, "folder_closed"):
            callbacks.folder_closed(target_folder)
    
    elif event == "adding":
        for item in sys.argv[3:]:
            logging.info(f"Calling {target_callback_file}: item_added_to_folder(folder: {target_folder}, item: {item})")
            if hasattr(callbacks, "item_added_to_folder"):
                callbacks.item_added_to_folder(target_folder, os.path.basename(item))
    
    elif event == "removing":
        for item in sys.argv[3:]:
            logging.info(f"Calling {target_callback_file}: item_removed_from_folder(folder: {target_folder}, item: {item})")
            if hasattr(callbacks, "item_removed_from_folder"):
                callbacks.item_removed_from_folder(target_folder, os.path.basename(item))
    
    else:
        logging.warning("Got unknown event, ignoring")

if __name__ == "__main__":
    main()
