"""Publish independently qualified managed source to its dedicated Fund Worker."""
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('native_publisher', Path(__file__).with_name('deploy.py'))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
if __name__ == '__main__':
    publisher.main(managed=True)
