import os
import json
import tempfile
from typing import List, Dict, Any

class RecentStore:
    def __init__(self, path: str = None):
        if path is None:
            state_dir = os.environ.get("PDFTOOL_STATE_DIR")
            if state_dir:
                self._state_dir = state_dir
            else:
                config_home = os.environ.get("XDG_CONFIG_HOME")
                if config_home:
                    self._state_dir = os.path.join(config_home, "pdf-tool")
                else:
                    self._state_dir = os.path.join(os.path.expanduser("~"), ".config", "pdf-tool")
            os.makedirs(self._state_dir, exist_ok=True)
            self._path = os.path.join(self._state_dir, "state.json")
        else:
            self._path = path
            self._state_dir = os.path.dirname(path) # This assumes path is a file within a directory.
            os.makedirs(self._state_dir, exist_ok=True)

        self._data: Dict[str, Any] = {"recent_files": [], "settings": {}}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                # Ensure structure is valid, recover if not
                if not isinstance(self._data, dict):
                    self._data = {"recent_files": [], "settings": {}}
                if "recent_files" not in self._data or not isinstance(self._data["recent_files"], list):
                    self._data["recent_files"] = []
                if "settings" not in self._data or not isinstance(self._data["settings"], dict):
                    self._data["settings"] = {}
        except (IOError, json.JSONDecodeError):
            self._data = {"recent_files": [], "settings": {}}
        finally:
            # Prune missing files even on load errors, to ensure clean state
            self._prune_missing_files()

    def _save(self):
        try:
            # Ensure the directory exists before attempting to create temp file
            os.makedirs(self._state_dir, exist_ok=True)
            temp_fd, temp_path = tempfile.mkstemp(dir=self._state_dir, prefix="state-")
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(temp_path, self._path)
        except OSError:
            # Swallow permission errors or other OS errors during save
            pass
        except Exception as e:
            # Catch other potential errors during save, e.g., if self._state_dir is not writable
            pass

    def _prune_missing_files(self):
        self._data["recent_files"] = [path for path in self._data["recent_files"] if os.path.exists(path)]
        # No need to call _save here, as _load and add_recent (the callers of prune) will trigger a save if needed.
        # This prevents redundant saves during _load.

    def add_recent(self, path: str):
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return

        recent_files = self._data["recent_files"]
        if abs_path in recent_files:
            recent_files.remove(abs_path)
        recent_files.insert(0, abs_path)
        self._data["recent_files"] = recent_files[:12]
        self._save()

    def recent(self) -> List[str]:
        # Prune on demand just before returning
        self._prune_missing_files()
        # _save is not needed here after _prune_missing_files because add_recent will save if it modifies the list.
        # However, _prune_missing_files does modify the list, so it needs to save itself to persist the pruned list.
        # Let's adjust _prune_missing_files to save if it makes changes.
        original_len = len(self._data["recent_files"])
        self._data["recent_files"] = [path for path in self._data["recent_files"] if os.path.exists(path)]
        if len(self._data["recent_files"]) != original_len:
            self._save()
        return list(self._data["recent_files"]) # Return a copy to prevent external modification

    def clear_recent(self):
        self._data["recent_files"] = []
        self._save()

    def set_setting(self, key: str, value: Any):
        self._data["settings"][key] = value
        self._save()

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._data["settings"].get(key, default)
