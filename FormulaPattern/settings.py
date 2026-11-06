"""Remember the last-used dialog values between runs.

Stored as a small JSON file in the user's home folder so it survives
add-in updates. Selections are never stored, only values.
"""
import json
import os

SETTINGS_PATH = os.path.join(os.path.expanduser('~'), '.fusion_formula_pattern.json')


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(values: dict) -> None:
    try:
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
            json.dump(values, f, indent=2, sort_keys=True)
    except Exception:
        pass
