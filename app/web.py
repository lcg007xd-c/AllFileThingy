from pathlib import Path


WEB_DIR = Path(__file__).with_name("web")


def load_page(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")

