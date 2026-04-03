import os
import shutil
import subprocess


DEFAULT_TIMEOUT_SECONDS = 120
_UNVERIFIED_MODELS = {"gemini"}
_SUPPORTED_TEMPLATE_VARS = {"filepath", "filename", "basename", "folder", "ext"}
_COMMON_EXECUTABLE_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/bin"),
]


def render_prompt_template(prompt_file: str, filepath: str) -> str:
    """Load prompt template and render supported variables from filepath."""
    prompt_path = os.path.expanduser(prompt_file)
    with open(prompt_path, encoding="utf-8") as f:
        template = f.read()

    folder = os.path.dirname(filepath)
    basename = os.path.basename(filepath)
    filename, ext = os.path.splitext(basename)
    ext = ext.lstrip(".")

    try:
        return template.format(
            filepath=filepath,
            filename=filename,
            basename=basename,
            folder=folder,
            ext=ext,
        )
    except KeyError as e:
        missing = str(e.args[0])
        raise ValueError(
            f"Unknown variable '{missing}' in prompt file {prompt_path}. "
            "Escape literal braces as {{ and }}"
        ) from e
    except ValueError as e:
        raise ValueError(
            f"Invalid template in {prompt_path}: {e}. Escape literal braces as {{{{ and }}}}"
        ) from e


def build_agent_command(model: str, prompt: str, dangerous_permissions: bool = False) -> list[str]:
    """Return the verified command for a supported AI agent model."""
    normalized = str(model).strip().lower()
    if normalized == "claude":
        cmd = ["claude", "-p", prompt]
        if dangerous_permissions:
            cmd.append("--dangerously-skip-permissions")
        return cmd
    if normalized == "codex":
        return ["codex", "exec", prompt, "-s", "workspace-write", "--skip-git-repo-check"]
    if normalized in _UNVERIFIED_MODELS:
        raise ValueError(
            f"AI agent model '{normalized}' is configured but not yet verified on this system"
        )
    raise ValueError(f"Unknown AI agent model: {model}. Supported: claude, codex")


def resolve_executable(executable: str) -> str | None:
    """Find an executable even when macOS Folder Actions starts with a minimal PATH."""
    resolved = shutil.which(executable)
    if resolved:
        return resolved

    for directory in _COMMON_EXECUTABLE_DIRS:
        candidate = os.path.join(directory, executable)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return None


def build_agent_env() -> dict[str, str]:
    """Augment PATH so GUI-launched Folder Actions can find CLI runtimes like node."""
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    path_parts = [part for part in current_path.split(os.pathsep) if part]
    for directory in reversed(_COMMON_EXECUTABLE_DIRS):
        if directory not in path_parts:
            path_parts.insert(0, directory)
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def run_ai_agent(model: str, prompt_file: str, filepath: str,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS,
                 dangerous_permissions: bool = False) -> tuple[bool, str]:
    """Render prompt and execute the selected AI agent in the file's folder."""
    normalized = str(model).strip().lower()
    try:
        prompt = render_prompt_template(prompt_file, filepath)
        cmd = build_agent_command(normalized, prompt, dangerous_permissions)
    except (OSError, ValueError) as e:
        return False, str(e)

    executable = cmd[0]
    resolved_executable = resolve_executable(executable)
    if resolved_executable is None:
        return False, f"{executable} CLI not found — is it installed?"
    cmd[0] = resolved_executable

    try:
        result = subprocess.run(
            cmd,
            cwd=os.path.dirname(filepath),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=build_agent_env(),
        )
    except FileNotFoundError:
        return False, f"{executable} CLI not found — is it installed?"
    except subprocess.TimeoutExpired as e:
        if e.process:
            e.process.kill()
            e.process.communicate()
        return False, f"Timed out after {timeout}s"
    except OSError as e:
        return False, f"{executable} failed: {e}"

    if result.returncode != 0:
        return False, result.stderr or result.stdout or f"{executable} exited with {result.returncode}"
    return True, result.stdout
