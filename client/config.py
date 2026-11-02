import json
import os
from pathlib import Path

APP_DIR = Path(
    os.environ.get(
        "LOCALAPPDATA",
        str(Path.home()),
    )
) / "CampoBurgerPOS"

CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_API_URL = "https://campo-pos-api.onrender.com"


def cargar_configuracion():
    config = {
        "api_url": DEFAULT_API_URL,
        "api_key": "",
    }

    try:
        if CONFIG_PATH.exists():
            datos = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                config.update(datos)
    except (OSError, json.JSONDecodeError):
        pass

    api_url = str(
        os.getenv("CAMPO_API_URL")
        or config.get("api_url")
        or DEFAULT_API_URL
    ).strip()

    config["api_url"] = api_url.rstrip("/")
    config["api_key"] = str(
        os.getenv("CAMPO_API_KEY")
        or config.get("api_key")
        or ""
    ).strip()
    return config


def guardar_api_url(api_url):
    api_url = str(api_url or "").strip().rstrip("/")

    if not api_url:
        raise ValueError("La URL de la API no puede estar vacia.")

    if not (
        api_url.startswith("https://")
        or api_url.startswith("http://127.0.0.1")
        or api_url.startswith("http://localhost")
    ):
        raise ValueError(
            "En produccion la URL de la API debe usar HTTPS."
        )

    APP_DIR.mkdir(parents=True, exist_ok=True)

    config = cargar_configuracion()
    config["api_url"] = api_url

    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return config
