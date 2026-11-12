"""Fase 5A (RNF-03 / RNF-08): la extensión no carga recursos externos y no deja texto en logs."""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "extension"
SOURCES = [p for p in EXT.rglob("*") if p.suffix in {".js", ".css", ".html", ".json"} and "test" not in p.parts]

# Únicos orígenes permitidos: las 3 plataformas, el backend local y el namespace de SVG (no es una petición).
ALLOWED = (
    "https://chatgpt.com", "https://claude.ai", "https://gemini.google.com",
    "http://localhost:8000", "http://localhost:5500", "http://127.0.0.1:5500",
    "http://www.w3.org/2000/svg",
)
URL_RE = re.compile(r"https?://[^\s\"'`)<>]+")


def test_no_external_urls_in_extension():
    bad = []
    for path in SOURCES:
        for url in URL_RE.findall(path.read_text(encoding="utf-8")):
            if not url.startswith(ALLOWED):
                bad.append(f"{path.relative_to(REPO)}: {url}")
    assert not bad, "recursos externos en la extensión:\n" + "\n".join(bad)


def test_no_remote_fonts_or_imports():
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "@import" not in text and "fonts.googleapis" not in text, path
        assert not re.search(r"<script[^>]+src=[\"']https?:", text), path


def test_manifest_lists_ui_layer_and_icons():
    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    for cs in m["content_scripts"]:
        assert cs["js"] == ["patterns.js", "ui.js", "content.js"]
    for icon in m["icons"].values():
        assert (EXT / icon).is_file()


def test_console_logs_do_not_print_text_variables():
    """Ningún console.* de la extensión interpola el texto del prompt ni valores detectados."""
    # ${text.length} está permitido (es un número); ${text} o ${ctx.text} no.
    forbidden = re.compile(
        r"console\.\w+\([^;]*\$\{\s*(text|value|values|pasted|raw|t\.text|ctx\.text|payload\.text|term|terms)\s*\}")
    for path in SOURCES:
        if path.suffix == ".js":
            assert not forbidden.search(path.read_text(encoding="utf-8")), path
