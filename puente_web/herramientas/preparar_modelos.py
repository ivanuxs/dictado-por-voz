#!/usr/bin/env python3
"""Descarga los modelos Opus-MT que usa el puente para traducir.

Los modelos NO van en el repositorio (son cientos de MB): este script los deja
en `puente_web/modelos/`, que está excluido de Git. El puente los carga con
`transformers` + `torch` (CPU) y los usa tal cual vienen de Hugging Face.

⚠️ No se convierten a CTranslate2: probado el 2026-10-31, la conversión de
estos modelos Marian repetía la frase tres veces (int8 y fp32, beam 1/2/5, con
y sin `end_token`, transformers 4 y 5). Con el modelo original, `generate`
devuelve una frase correcta.

De dónde salen los pares:

  Directos (los que existen en Helsinki-NLP):
    es-en, en-es, es-it, it-es, en-zh, zh-en
  Lenguas romances (el portugués va por aquí, no hay es-pt directo):
    en-roa (inglés -> portugués, con el token >>por<<)
    roa-en (portugués -> inglés, con el token >>por<<)

Lo que no existe se resuelve con PIVOTE por inglés en `traduccion.py`:
  es->zh = es->en->zh        zh->es = zh->en->es
  es->pt = es->en->pt        pt->es = pt->en->es

Uso (con un venv que tenga torch + transformers; ver puente_web/README.md):

    python3 preparar_modelos.py                # todos
    python3 preparar_modelos.py --modelos es-en zh-en
    python3 preparar_modelos.py --sin-probar   # solo descargar

Cada modelo ocupa ~300 MB en disco.
"""

import argparse
import shutil
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DESTINO_POR_DEFECTO = AQUI.parent / "modelos"

# nombre local -> repositorio de Hugging Face
MODELOS = {
    "es-en": "Helsinki-NLP/opus-mt-es-en",
    "en-es": "Helsinki-NLP/opus-mt-en-es",
    "es-it": "Helsinki-NLP/opus-mt-es-it",
    "it-es": "Helsinki-NLP/opus-mt-it-es",
    "en-zh": "Helsinki-NLP/opus-mt-en-zh",
    "zh-en": "Helsinki-NLP/opus-mt-zh-en",
    "en-pt": "Helsinki-NLP/opus-mt-tc-big-en-pt",
    "roa-en": "Helsinki-NLP/opus-mt-ROMANCE-en",
}

# Frase de prueba y token de idioma (solo las lenguas romances lo necesitan).
PRUEBAS = {
    "es-en": ("Hola, ¿cómo estás?", None),
    "en-es": ("Hello, how are you?", None),
    "es-it": ("Hola, ¿cómo estás?", None),
    "it-es": ("Ciao, come stai?", None),
    "en-zh": ("Hello, how are you?", None),
    "zh-en": ("你好，你好吗？", None),
    "en-pt": ("I want a coffee with milk.", None),
    "roa-en": ("Olá, como você está?", ">>por<<"),
}

# Ficheros que deja `save_pretrained` con los pesos (uno u otro).
PESOS = ("model.safetensors", "pytorch_model.bin")


def descargar(nombre: str, repo: str, destino: Path, forzar: bool) -> Path:
    salida = destino / nombre
    ya = (salida / "config.json").exists() and any(
        (salida / p).exists() for p in PESOS
    )
    if ya and not forzar:
        print(f"  {nombre}: ya está ({salida})")
        return salida

    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    print(f"  {nombre}: descargando {repo}…", flush=True)
    tokenizador = AutoTokenizer.from_pretrained(repo)
    modelo = AutoModelForSeq2SeqLM.from_pretrained(repo)

    if salida.exists():
        shutil.rmtree(salida)
    salida.mkdir(parents=True)
    tokenizador.save_pretrained(str(salida))
    modelo.save_pretrained(str(salida))
    return salida


def probar(nombre: str, carpeta: Path) -> bool:
    """Traduce una frase con transformers, como hará el puente."""
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    texto, prefijo = PRUEBAS[nombre]
    tokenizador = AutoTokenizer.from_pretrained(str(carpeta))
    modelo = AutoModelForSeq2SeqLM.from_pretrained(str(carpeta))
    modelo.eval()

    entrada = f"{prefijo} {texto}" if prefijo else texto
    lotes = tokenizador([entrada], return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        salida = modelo.generate(**lotes, max_new_tokens=256, num_beams=2)
    frases = tokenizador.batch_decode(salida, skip_special_tokens=True)
    print(f"  {nombre}: «{texto}» -> «{frases[0].strip()}»")
    return True


def main() -> int:
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument(
        "--destino", type=Path, default=DESTINO_POR_DEFECTO,
        help=f"carpeta de salida (por defecto {DESTINO_POR_DEFECTO})"
    )
    analizador.add_argument(
        "--modelos", nargs="*", choices=sorted(MODELOS),
        help="solo estos modelos (por defecto, todos)"
    )
    analizador.add_argument("--forzar", action="store_true", help="rehacer los que ya existen")
    analizador.add_argument("--sin-probar", action="store_true", help="no traducir la frase de prueba")
    args = analizador.parse_args()

    args.destino.mkdir(parents=True, exist_ok=True)
    elegidos = args.modelos or list(MODELOS)

    print(f"Destino: {args.destino}")
    print(f"Modelos: {', '.join(elegidos)}")
    for nombre in elegidos:
        carpeta = descargar(nombre, MODELOS[nombre], args.destino, args.forzar)
        if not args.sin_probar:
            probar(nombre, carpeta)

    total = sum(f.stat().st_size for f in args.destino.rglob("*") if f.is_file())
    print(f"\nTamaño total: {total / 1024 / 1024:.0f} MB en {args.destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
