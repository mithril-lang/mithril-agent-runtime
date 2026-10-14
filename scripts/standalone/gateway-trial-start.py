"""Start the isolated API-only trial; the hosting platform owns supervision."""
import json
import os
import re
from pathlib import Path

def configure(home, initial_model='qwen/qwen3.8-27b'):
    if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}', initial_model):
        raise ValueError('Invalid initial model')
    config = home / 'config.yaml'
    if not config.exists():
        config.write_text(json.dumps({
            'providers': {'mithril-inference': {
                'name': 'Mithril Inference', 'base_url': 'https://api.mithril.fund/v1',
                'key_env': 'CUSTOM_PROVIDER_MITHRIL_KEY', 'transport': 'chat_completions',
            }},
            'model': {'api_mode': 'chat_completions', 'base_url': 'https://api.mithril.fund/v1',
                'default': initial_model, 'key_env': 'CUSTOM_PROVIDER_MITHRIL_KEY',
                'provider': 'mithril-inference'},
        }, indent=2) + '\n')
        config.chmod(0o600)


if __name__ == '__main__':
    assert os.getuid() == 1000, 'The cloud trial must run as UID 1000'
    configure(Path('/opt/data'), os.environ.get('MITHRIL_NATIVE_INITIAL_MODEL', 'qwen/qwen3.8-27b'))
    os.execv('/opt/hermes/.venv/bin/hermes', [
        'hermes', 'gateway', 'run', '--external-supervisor',
    ])
