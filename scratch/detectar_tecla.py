"""Script para detectar qué tecla envía el botón del auricular (o cualquier tecla).
Ejecuta y pulsa el botón. Pulsa ESC para salir."""

from pynput import keyboard


def on_press(key):
    try:
        if hasattr(key, "char"):
            print(f"[TECLA NORMAL] char={key.char!r}")
        else:
            print(f"[TECLA ESPECIAL] {key}  |  name={key.name}")
    except Exception as e:
        print(f"[ERROR] {e}")


def on_release(key):
    if key == keyboard.Key.esc:
        print("\nSaliendo...")
        return False


print("=== DETECTOR DE TECLAS ===")
print("Pulsa el botón de tu auricular (o cualquier tecla) y mira la salida.")
print("Pulsa ESC para salir.\n")

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()
