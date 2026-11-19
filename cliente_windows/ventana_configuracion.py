"""Ventana de configuración del cliente de dictado (Tkinter).

Se abre desde el icono de la bandeja («Configuración…»). Lee y escribe
`comandos.json` (o `comandos_windows.json`) y, al guardar, avisa al cliente para
que relea la configuración sin reiniciar.

Cuatro pestañas:

- **Palabras**: la palabra clave, las frases de cierre y las listas de verbos y
  frases de comando (antes vivían solo en el código).
- **Skills**: el catálogo de lo que sabe hacer el cliente, con un botón para
  probarlo sin hablar.
- **Programas**: el catálogo `nombre → ruta` de `abre` y `cerrar`.
- **Voz y activación**: teclas, modo, salida, escucha y servidor.

⚠️ Solo la ventana escribe el JSON; el cliente únicamente lo lee.
"""
import argparse
import json
import os
import sys
import threading
import unicodedata

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import perfil as perfil_cuenta
import sesion
from diccionario_desktop import DiccionarioEscritorio

try:  # compartido con los dos clientes
    import skills as modulo_skills
except Exception:  # pragma: no cover - solo si falta el archivo
    modulo_skills = None

# Puente en esta misma computadora: es lo que se propone cuando el JSON no trae
# dirección. Se puede cambiar por la de otra máquina o por la de nginx.
PUENTE_POR_DEFECTO = "http://127.0.0.1:8000"

ACCIONES = [
    "abre",
    "cerrar",
    "subir_volumen",
    "bajar_volumen",
    "silenciar",
    "reproducir",
    "siguiente",
    "anterior",
    "captura",
    "bloquear",
    "apagar",
    "reiniciar",
    "dictar",
]


def normalizar(texto):
    """Así es como el cliente compara las frases: sin tildes ni mayúsculas."""
    plano = unicodedata.normalize("NFKD", (texto or "").lower())
    plano = plano.encode("ascii", "ignore").decode("ascii")
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in plano)
    return " ".join(limpio.split())


def _ruta_por_defecto():
    """El JSON que hay junto a este archivo."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    for nombre in ("comandos.json", "comandos_windows.json"):
        ruta = os.path.join(base, nombre)
        if os.path.exists(ruta):
            return ruta
    return os.path.join(base, "comandos.json")


class DialogoFrase(tk.Toplevel):
    """Pide una frase (o verbo) y su acción. Modal y sencillo."""

    def __init__(self, padre, titulo, frase="", accion="", acciones=None):
        super().__init__(padre)
        self.title(titulo)
        self.resizable(False, False)
        self.transient(padre)
        self.resultado = None

        cuerpo = ttk.Frame(self, padding=12)
        cuerpo.grid(row=0, column=0, sticky="nsew")

        ttk.Label(cuerpo, text="Frase o palabra:").grid(row=0, column=0, sticky="w")
        self.var_frase = tk.StringVar(value=frase)
        entrada = ttk.Entry(cuerpo, textvariable=self.var_frase, width=32)
        entrada.grid(row=1, column=0, pady=(2, 10), sticky="we")

        ttk.Label(cuerpo, text="Acción:").grid(row=2, column=0, sticky="w")
        self.var_accion = tk.StringVar(value=accion or (acciones or [""])[0])
        combo = ttk.Combobox(
            cuerpo, textvariable=self.var_accion, values=acciones or [],
            state="readonly", width=30,
        )
        combo.grid(row=3, column=0, pady=(2, 12), sticky="we")

        botones = ttk.Frame(cuerpo)
        botones.grid(row=4, column=0, sticky="e")
        ttk.Button(botones, text="Cancelar", command=self.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(botones, text="Aceptar", command=self._aceptar).grid(
            row=0, column=1
        )

        entrada.focus_set()
        self.bind("<Return>", lambda _e: self._aceptar())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _aceptar(self):
        frase = self.var_frase.get().strip()
        accion = self.var_accion.get().strip()
        if not frase or not accion:
            messagebox.showwarning(
                "Faltan datos", "Hacen falta la frase y la acción.", parent=self
            )
            return
        self.resultado = (frase, accion)
        self.destroy()


class VentanaConfiguracion:
    def __init__(self, raiz, ruta_json):
        self.raiz = raiz
        self.ruta_json = ruta_json
        self.datos = self._cargar()
        self.cambios = False

        raiz.title("SkipKey — Configuración")
        raiz.geometry("820x560")
        raiz.minsize(720, 480)

        marco = ttk.Frame(raiz, padding=10)
        marco.pack(fill="both", expand=True)

        self.pestanas = ttk.Notebook(marco)
        self.pestanas.pack(fill="both", expand=True)

        self._pestana_palabras()
        self._pestana_diccionario()
        self._pestana_skills()
        self._pestana_programas()
        self._pestana_web()
        self._pestana_cuenta()
        self._pestana_voz()

        pie = ttk.Frame(marco)
        pie.pack(fill="x", pady=(10, 0))
        self.estado = ttk.Label(pie, text=self.ruta_json, foreground="#555")
        self.estado.pack(side="left")
        ttk.Button(pie, text="Cerrar", command=self._cerrar).pack(side="right")
        ttk.Button(pie, text="Guardar", command=self._guardar).pack(
            side="right", padx=(0, 8)
        )

        raiz.protocol("WM_DELETE_WINDOW", self._cerrar)

    # --- datos -----------------------------------------------------------

    def _cargar(self):
        try:
            with open(self.ruta_json, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            messagebox.showerror(
                "Configuración",
                f"No se pudo leer:\n{self.ruta_json}\n\n{e}",
            )
            return {}

    def _programas(self, accion):
        tabla = self.datos.get(accion)
        return tabla if isinstance(tabla, dict) else {}

    # --- pestaña Palabras ------------------------------------------------

    def _pestana_palabras(self):
        marco = ttk.Frame(self.pestanas, padding=12)
        self.pestanas.add(marco, text="Palabras")

        arriba = ttk.Frame(marco)
        arriba.pack(fill="x")
        ttk.Label(arriba, text="Palabra clave:").pack(side="left")
        self.var_clave = tk.StringVar(value=str(self.datos.get("clave", "computadora")))
        ttk.Entry(arriba, textvariable=self.var_clave, width=24).pack(
            side="left", padx=(6, 0)
        )
        ttk.Label(
            arriba,
            text="   (abre el modo comando; para dictar se dice «iniciar dictado»)",
            foreground="#666",
        ).pack(side="left")

        columnas = ttk.Frame(marco)
        columnas.pack(fill="both", expand=True, pady=(12, 0))
        columnas.columnconfigure(0, weight=1)
        columnas.columnconfigure(1, weight=1)

        # Frases de cierre
        izq = ttk.LabelFrame(columnas, text="Frases de cierre del dictado", padding=8)
        izq.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.lista_cierre = tk.Listbox(izq, height=6, exportselection=False)
        self.lista_cierre.pack(fill="both", expand=True)
        for frase in self._frases_cierre():
            self.lista_cierre.insert("end", frase)
        fila = ttk.Frame(izq)
        fila.pack(fill="x", pady=(6, 0))
        self.var_cierre = tk.StringVar()
        ttk.Entry(fila, textvariable=self.var_cierre).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(fila, text="Añadir", width=8, command=self._anadir_cierre).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(fila, text="Quitar", width=8, command=self._quitar_cierre).pack(
            side="left", padx=(4, 0)
        )

        # Verbos (abre / cerrar)
        der = ttk.LabelFrame(columnas, text="Verbos de programa", padding=8)
        der.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.tabla_verbos = self._tabla(der, ("verbo", "accion"), ("Verbo", "Acción"))
        botones = ttk.Frame(der)
        botones.pack(fill="x", pady=(6, 0))
        ttk.Button(botones, text="Añadir", command=self._anadir_verbo).pack(side="left")
        ttk.Button(botones, text="Editar", command=self._editar_verbo).pack(
            side="left", padx=4
        )
        ttk.Button(botones, text="Quitar", command=self._quitar_verbo).pack(side="left")

        # Frases de comando
        bajo = ttk.LabelFrame(marco, text="Frases de comando", padding=8)
        bajo.pack(fill="both", expand=True, pady=(12, 0))
        self.tabla_frases = self._tabla(bajo, ("frase", "accion"), ("Frase", "Acción"))
        botones = ttk.Frame(bajo)
        botones.pack(fill="x", pady=(6, 0))
        ttk.Button(botones, text="Añadir", command=self._anadir_frase).pack(side="left")
        ttk.Button(botones, text="Editar", command=self._editar_frase).pack(
            side="left", padx=4
        )
        ttk.Button(botones, text="Quitar", command=self._quitar_frase).pack(side="left")

        self._rellenar_verbos()
        self._rellenar_frases()

    def _frases_cierre(self):
        frases = self.datos.get("frase_fin")
        if isinstance(frases, str):
            frases = [frases]
        return [str(f) for f in (frases or [])]

    def _anadir_cierre(self):
        frase = self.var_cierre.get().strip()
        if not frase:
            return
        if frase in self.lista_cierre.get(0, "end"):
            return
        self.lista_cierre.insert("end", frase)
        self.var_cierre.set("")

    def _quitar_cierre(self):
        for indice in reversed(self.lista_cierre.curselection()):
            self.lista_cierre.delete(indice)

    def _tabla(self, padre, claves, cabeceras):
        """Treeview de solo lectura con dos columnas y scroll."""
        contenedor = ttk.Frame(padre)
        contenedor.pack(fill="both", expand=True)
        tabla = ttk.Treeview(
            contenedor, columns=claves, show="headings", height=7,
            selectmode="browse",
        )
        for clave, cabecera in zip(claves, cabeceras):
            tabla.heading(clave, text=cabecera)
            tabla.column(clave, width=200, anchor="w")
        barra = ttk.Scrollbar(contenedor, orient="vertical", command=tabla.yview)
        tabla.configure(yscrollcommand=barra.set)
        tabla.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        return tabla

    def _filas(self, tabla):
        return [tabla.item(i, "values") for i in tabla.get_children()]

    def _rellenar_verbos(self):
        self.tabla_verbos.delete(*self.tabla_verbos.get_children())
        verbos = self.datos.get("verbos")
        if not isinstance(verbos, dict) or not verbos:
            from marcas import VERBOS_POR_DEFECTO as verbos
        for verbo, accion in verbos.items():
            self.tabla_verbos.insert("", "end", values=(verbo, accion))

    def _rellenar_frases(self):
        self.tabla_frases.delete(*self.tabla_frases.get_children())
        frases = self.datos.get("frases")
        if not isinstance(frases, dict) or not frases:
            from marcas import FRASES_POR_DEFECTO as frases
        for frase, accion in frases.items():
            self.tabla_frases.insert("", "end", values=(frase, accion))

    def _anadir_verbo(self):
        dialogo = DialogoFrase(
            self.raiz, "Nuevo verbo", acciones=["abre", "cerrar"]
        )
        if dialogo.resultado:
            self.tabla_verbos.insert("", "end", values=dialogo.resultado)

    def _editar_verbo(self):
        seleccion = self.tabla_verbos.selection()
        if not seleccion:
            return
        valores = self.tabla_verbos.item(seleccion[0], "values")
        dialogo = DialogoFrase(
            self.raiz, "Editar verbo", valores[0], valores[1],
            acciones=["abre", "cerrar"],
        )
        if dialogo.resultado:
            self.tabla_verbos.item(seleccion[0], values=dialogo.resultado)

    def _quitar_verbo(self):
        for item in self.tabla_verbos.selection():
            self.tabla_verbos.delete(item)

    def _anadir_frase(self):
        dialogo = DialogoFrase(self.raiz, "Nueva frase de comando", acciones=ACCIONES)
        if dialogo.resultado:
            self.tabla_frases.insert("", "end", values=dialogo.resultado)

    def _editar_frase(self):
        seleccion = self.tabla_frases.selection()
        if not seleccion:
            return
        valores = self.tabla_frases.item(seleccion[0], "values")
        dialogo = DialogoFrase(
            self.raiz, "Editar frase de comando", valores[0], valores[1],
            acciones=ACCIONES,
        )
        if dialogo.resultado:
            self.tabla_frases.item(seleccion[0], values=dialogo.resultado)

    def _quitar_frase(self):
        for item in self.tabla_frases.selection():
            self.tabla_frases.delete(item)

    # --- pestaña Skills --------------------------------------------------

    def _pestana_skills(self):
        marco = ttk.Frame(self.pestanas, padding=12)
        self.pestanas.add(marco, text="Skills")

        ttk.Label(
            marco,
            text=(
                "Lo que el cliente sabe hacer. La lista sale de los verbos, las "
                "frases y el catálogo de programas, así que se actualiza sola."
            ),
            foreground="#444",
            wraplength=740,
            justify="left",
        ).pack(fill="x", pady=(0, 8))

        self.tabla_skills = self._tabla(
            marco,
            ("titulo", "como", "requiere", "estado"),
            ("Skill", "Cómo se dice", "Necesita", "Estado"),
        )
        self.tabla_skills.column("titulo", width=210)
        self.tabla_skills.column("como", width=190)
        self.tabla_skills.column("requiere", width=180)
        self.tabla_skills.column("estado", width=150)

        pie = ttk.Frame(marco)
        pie.pack(fill="x", pady=(8, 0))
        ttk.Label(pie, text="Programa:").pack(side="left")
        self.var_programa_skill = tk.StringVar()
        self.combo_programa_skill = ttk.Combobox(
            pie, textvariable=self.var_programa_skill, state="readonly", width=22
        )
        self.combo_programa_skill.pack(side="left", padx=(6, 12))
        self.boton_probar = ttk.Button(pie, text="Probar", command=self._probar_skill)
        self.boton_probar.pack(side="left")
        self.tabla_skills.bind("<<TreeviewSelect>>", self._al_elegir_skill)

        self._refrescar_skills()

    def _refrescar_skills(self):
        self.tabla_skills.delete(*self.tabla_skills.get_children())
        if modulo_skills is None:
            self.tabla_skills.insert(
                "", "end",
                values=("Falta skills.py", "—", "—", "no disponible"),
            )
            return
        try:
            from marcas import FRASES, VERBOS
        except Exception:
            FRASES, VERBOS = {}, {}
        filas = modulo_skills.listar(
            VERBOS,
            FRASES,
            self._programas("abre") or self._programas("cerrar"),
            self._programas("web"),
        )
        for fila in filas:
            self.tabla_skills.insert(
                "", "end",
                iid=fila["accion"],
                values=(
                    fila["titulo"], fila["como"], fila["requiere"], fila["estado"],
                ),
            )
        programas = sorted(self._programas("abre").keys())
        self.combo_programa_skill.configure(values=programas)
        if programas and not self.var_programa_skill.get():
            self.var_programa_skill.set(programas[0])

    def _al_elegir_skill(self, _evento=None):
        seleccion = self.tabla_skills.selection()
        if not seleccion:
            return
        accion = seleccion[0]
        if accion == "web":
            self.combo_programa_skill.configure(
                values=sorted(self._programas("web").keys()), state="readonly"
            )
        elif accion in ("abre", "cerrar"):
            self.combo_programa_skill.configure(
                values=sorted(self._programas("abre").keys()), state="readonly"
            )
        else:
            self.combo_programa_skill.configure(state="disabled")
        if self.combo_programa_skill.cget("state") == "readonly":
            opciones = self.combo_programa_skill.cget("values")
            if opciones and self.var_programa_skill.get() not in opciones:
                self.var_programa_skill.set(opciones[0])

    def _probar_skill(self):
        """Ejecuta la skill elegida sin pasar por la voz."""
        seleccion = self.tabla_skills.selection()
        if not seleccion:
            messagebox.showinfo(
                "Probar", "Elige una skill de la lista.", parent=self.raiz
            )
            return
        accion = seleccion[0]
        estado = self.tabla_skills.item(seleccion[0], "values")[3]
        if estado.startswith("deshabilitado"):
            messagebox.showwarning(
                "Probar",
                "Esa acción está deshabilitada por seguridad.",
                parent=self.raiz,
            )
            return
        argumento = ""
        if accion in ("abre", "cerrar", "web"):
            argumento = self.var_programa_skill.get().strip()
            if not argumento:
                messagebox.showinfo(
                    "Probar",
                    (
                        "Elige primero un sitio del catálogo."
                        if accion == "web"
                        else "Elige primero un programa del catálogo."
                    ),
                    parent=self.raiz,
                )
                return
        if accion in ("dictar", "consulta", "respuesta_local"):
            messagebox.showinfo(
                "Probar",
                "Esa skill necesita el micrófono o el asistente: pruébala "
                "diciéndola por voz.",
                parent=self.raiz,
            )
            return

        self._ejecutar(accion, argumento)

    # --- pestaña Programas ------------------------------------------------

    def _pestana_programas(self):
        marco = ttk.Frame(self.pestanas, padding=12)
        self.pestanas.add(marco, text="Programas")

        ttk.Label(
            marco,
            text=(
                "El catálogo que usa «abre» y «cerrar»: el nombre que se dice y la "
                "ruta o el comando que se ejecuta."
            ),
            foreground="#444",
            wraplength=740,
            justify="left",
        ).pack(fill="x", pady=(0, 8))

        self.tabla_programas = self._tabla(
            marco, ("accion", "nombre", "destino"), ("Acción", "Nombre", "Programa")
        )
        self.tabla_programas.column("accion", width=90)
        self.tabla_programas.column("nombre", width=170)
        self.tabla_programas.column("destino", width=380)

        pie = ttk.Frame(marco)
        pie.pack(fill="x", pady=(8, 0))
        ttk.Button(pie, text="Añadir", command=self._anadir_programa).pack(side="left")
        ttk.Button(pie, text="Editar", command=self._editar_programa).pack(
            side="left", padx=4
        )
        ttk.Button(pie, text="Quitar", command=self._quitar_programa).pack(side="left")
        ttk.Button(pie, text="Probar", command=self._probar_programa).pack(
            side="right"
        )

        self._rellenar_programas()

    def _rellenar_programas(self):
        self.tabla_programas.delete(*self.tabla_programas.get_children())
        for accion in ("abre", "cerrar"):
            for nombre, destino in self._programas(accion).items():
                self.tabla_programas.insert(
                    "", "end", values=(accion, nombre, destino)
                )

    def _nuevo_programa(self, accion="abre", nombre="", destino=""):
        dialogo = tk.Toplevel(self.raiz)
        dialogo.title("Programa")
        dialogo.transient(self.raiz)
        dialogo.resizable(False, False)
        cuerpo = ttk.Frame(dialogo, padding=12)
        cuerpo.pack(fill="both", expand=True)

        ttk.Label(cuerpo, text="Acción:").grid(row=0, column=0, sticky="w")
        var_accion = tk.StringVar(value=accion)
        ttk.Combobox(
            cuerpo, textvariable=var_accion, values=["abre", "cerrar"],
            state="readonly", width=12,
        ).grid(row=0, column=1, sticky="w", pady=2)

        ttk.Label(cuerpo, text="Nombre:").grid(row=1, column=0, sticky="w")
        var_nombre = tk.StringVar(value=nombre)
        ttk.Entry(cuerpo, textvariable=var_nombre, width=32).grid(
            row=1, column=1, sticky="we", pady=2
        )

        ttk.Label(cuerpo, text="Programa:").grid(row=2, column=0, sticky="w")
        var_destino = tk.StringVar(value=destino)
        ttk.Entry(cuerpo, textvariable=var_destino, width=32).grid(
            row=2, column=1, sticky="we", pady=2
        )
        ttk.Button(
            cuerpo, text="Examinar…",
            command=lambda: var_destino.set(
                filedialog.askopenfilename(parent=dialogo) or var_destino.get()
            ),
        ).grid(row=3, column=1, sticky="e", pady=(0, 10))

        resultado = {}

        def aceptar():
            if not var_nombre.get().strip() or not var_destino.get().strip():
                messagebox.showwarning(
                    "Faltan datos", "Hacen falta el nombre y el programa.",
                    parent=dialogo,
                )
                return
            resultado["valor"] = (
                var_accion.get(), normalizar(var_nombre.get()),
                var_destino.get().strip(),
            )
            dialogo.destroy()

        barra = ttk.Frame(cuerpo)
        barra.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(barra, text="Cancelar", command=dialogo.destroy).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(barra, text="Aceptar", command=aceptar).pack(side="left")
        dialogo.grab_set()
        self.raiz.wait_window(dialogo)
        return resultado.get("valor")

    def _anadir_programa(self):
        valores = self._nuevo_programa()
        if valores:
            self.tabla_programas.insert("", "end", values=valores)

    def _editar_programa(self):
        seleccion = self.tabla_programas.selection()
        if not seleccion:
            return
        valores = self.tabla_programas.item(seleccion[0], "values")
        nuevos = self._nuevo_programa(*valores)
        if nuevos:
            self.tabla_programas.item(seleccion[0], values=nuevos)

    def _quitar_programa(self):
        for item in self.tabla_programas.selection():
            self.tabla_programas.delete(item)

    def _probar_programa(self):
        seleccion = self.tabla_programas.selection()
        if not seleccion:
            return
        accion, nombre, _destino = self.tabla_programas.item(
            seleccion[0], "values"
        )[:3]
        self._ejecutar(accion, nombre)

    def _ejecutar(self, accion, argumento=""):
        """Ejecuta una acción aquí mismo, sin pasar por la voz."""
        self.estado.configure(text=f"Probando «{accion} {argumento}»…")
        self.raiz.update_idletasks()
        try:
            import cliente_voz  # import perezoso: pesa y solo hace falta aquí

            resultado = cliente_voz.ejecutar_comando(accion, argumento, avisar=False)
        except Exception as e:
            self.estado.configure(text=f"No se pudo probar: {e}")
            return
        self.estado.configure(
            text=(
                f"«{accion} {argumento}»: "
                + ("ejecutado" if resultado else "no se pudo ejecutar")
            )
        )

    # --- pestaña Web ------------------------------------------------------

    def _pestana_web(self):
        marco = ttk.Frame(self.pestanas, padding=12)
        self.pestanas.add(marco, text="Web")

        ttk.Label(
            marco,
            text=(
                "Sitios que se abren en el navegador: «ingresa a la página del "
                "seace», «abre youtube». El nombre es lo que se dice y la "
                "dirección tiene que empezar por http:// o https://."
            ),
            foreground="#444",
            wraplength=740,
            justify="left",
        ).pack(fill="x", pady=(0, 8))

        self.tabla_web = self._tabla(
            marco, ("nombre", "url"), ("Nombre", "Dirección")
        )
        self.tabla_web.column("nombre", width=170)
        self.tabla_web.column("url", width=570)

        pie = ttk.Frame(marco)
        pie.pack(fill="x", pady=(8, 0))
        ttk.Button(pie, text="Añadir", command=self._anadir_sitio).pack(side="left")
        ttk.Button(pie, text="Editar", command=self._editar_sitio).pack(
            side="left", padx=4
        )
        ttk.Button(pie, text="Quitar", command=self._quitar_sitio).pack(side="left")
        ttk.Button(pie, text="Probar", command=self._probar_sitio).pack(
            side="right"
        )

        self._rellenar_web()

    def _rellenar_web(self):
        self.tabla_web.delete(*self.tabla_web.get_children())
        for nombre, url in self._programas("web").items():
            self.tabla_web.insert("", "end", values=(nombre, url))

    def _nuevo_sitio(self, nombre="", url=""):
        dialogo = tk.Toplevel(self.raiz)
        dialogo.title("Sitio web")
        dialogo.transient(self.raiz)
        dialogo.resizable(False, False)
        cuerpo = ttk.Frame(dialogo, padding=12)
        cuerpo.pack(fill="both", expand=True)

        ttk.Label(cuerpo, text="Nombre (lo que se dice):").grid(
            row=0, column=0, sticky="w"
        )
        var_nombre = tk.StringVar(value=nombre)
        ttk.Entry(cuerpo, textvariable=var_nombre, width=46).grid(
            row=0, column=1, sticky="we", pady=2, padx=(8, 0)
        )

        ttk.Label(cuerpo, text="Dirección:").grid(row=1, column=0, sticky="w")
        var_url = tk.StringVar(value=url)
        ttk.Entry(cuerpo, textvariable=var_url, width=46).grid(
            row=1, column=1, sticky="we", pady=2, padx=(8, 0)
        )

        resultado = {}

        def aceptar():
            valor_url = var_url.get().strip()
            if not var_nombre.get().strip() or not valor_url:
                messagebox.showwarning(
                    "Faltan datos", "Hacen falta el nombre y la dirección.",
                    parent=dialogo,
                )
                return
            if not valor_url.lower().startswith(("http://", "https://")):
                messagebox.showwarning(
                    "Dirección no válida",
                    "La dirección tiene que empezar por http:// o https://",
                    parent=dialogo,
                )
                return
            resultado["valor"] = (normalizar(var_nombre.get()), valor_url)
            dialogo.destroy()

        barra = ttk.Frame(cuerpo)
        barra.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(barra, text="Cancelar", command=dialogo.destroy).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(barra, text="Aceptar", command=aceptar).pack(side="left")
        dialogo.grab_set()
        self.raiz.wait_window(dialogo)
        return resultado.get("valor")

    def _anadir_sitio(self):
        valores = self._nuevo_sitio()
        if valores:
            self.tabla_web.insert("", "end", values=valores)

    def _editar_sitio(self):
        seleccion = self.tabla_web.selection()
        if not seleccion:
            return
        valores = self.tabla_web.item(seleccion[0], "values")
        nuevos = self._nuevo_sitio(*valores)
        if nuevos:
            self.tabla_web.item(seleccion[0], values=nuevos)

    def _quitar_sitio(self):
        for item in self.tabla_web.selection():
            self.tabla_web.delete(item)

    def _probar_sitio(self):
        seleccion = self.tabla_web.selection()
        if not seleccion:
            return
        _nombre, url = self.tabla_web.item(seleccion[0], "values")[:2]
        self._ejecutar("web", url)

    # --- pestaña Voz ------------------------------------------------------

    def _pestana_voz(self):
        marco = ttk.Frame(self.pestanas, padding=12)
        self.pestanas.add(marco, text="Voz y activación")

        fila = 0

        def etiqueta(texto):
            nonlocal fila
            ttk.Label(marco, text=texto).grid(
                row=fila, column=0, sticky="w", pady=3
            )
            fila += 1

        def entrada(clave, ancho=34, tipo="lista"):
            nonlocal fila
            valor = self.datos.get(clave, "")
            if isinstance(valor, list):
                valor = ", ".join(str(v) for v in valor)
            var = tk.StringVar(value=str(valor))
            campo = ttk.Entry(marco, textvariable=var, width=ancho)
            campo.grid(row=fila - 1, column=1, sticky="w", padx=(10, 0), pady=3)
            self._campos[clave] = (tipo, var)
            fila += 1
            return campo

        self._campos = {}

        etiqueta("Teclas activadoras (separadas por comas):")
        entrada("tecla_activadora")
        etiqueta("Teclas para escribir donde está el cursor:")
        entrada("tecla_activadora_escribir")

        etiqueta("Modo de activación:")
        var_modo = tk.StringVar(
            value=str(self.datos.get("modo_activacion", "toggle"))
        )
        ttk.Combobox(
            marco, textvariable=var_modo, values=["toggle", "mantener"],
            state="readonly", width=14,
        ).grid(row=fila - 1, column=1, sticky="w", padx=(10, 0), pady=3)
        self._campos["modo_activacion"] = ("texto", var_modo)

        etiqueta("Modo de salida:")
        var_salida = tk.StringVar(
            value=str(self.datos.get("modo_salida", "portapapeles"))
        )
        ttk.Combobox(
            marco, textvariable=var_salida,
            values=["portapapeles", "escribir"], state="readonly", width=14,
        ).grid(row=fila - 1, column=1, sticky="w", padx=(10, 0), pady=3)
        self._campos["modo_salida"] = ("texto", var_salida)

        etiqueta("Dispositivo de entrada:")
        entrada("dispositivo_entrada", ancho=40, tipo="texto")
        ttk.Label(
            marco, text="(«interno», «default», un índice o parte del nombre)",
            foreground="#666",
        ).grid(row=fila - 1, column=2, sticky="w", padx=(8, 0))

        if "servidor" in self.datos:
            etiqueta("Servidor (IP:PUERTO):")
            entrada("servidor", ancho=22, tipo="texto")

        casillas = [
            ("modo_escucha", "Escucha continua (palabra clave)"),
            ("bandeja", "Icono en la bandeja"),
            ("boton_auricular_bluetooth", "Botón del auricular Bluetooth (MPRIS)"),
        ]
        if "escucha_local" in self.datos:
            casillas.append(("escucha_local", "Escucha local de la palabra clave"))
        for clave, texto in casillas:
            var = tk.BooleanVar(value=bool(self.datos.get(clave, False)))
            ttk.Checkbutton(marco, text=texto, variable=var).grid(
                row=fila, column=0, columnspan=2, sticky="w", pady=2
            )
            self._campos[clave] = ("bool", var)
            fila += 1

        ttk.Label(
            marco,
            text=(
                "Los cambios se aplican al guardar: el cliente relee comandos.json "
                "sin reiniciarse."
            ),
            foreground="#666",
            wraplength=740,
            justify="left",
        ).grid(row=fila, column=0, columnspan=3, sticky="w", pady=(14, 0))

    # --- pestaña Diccionario ----------------------------------------------

    def _pestana_diccionario(self):
        """Lo aprendido: correcciones del dictado y palabras propias.

        Se aplica al texto que llega **dictado** (no a lo que se teclea) y viaja
        con la cuenta: lo enseñado aquí aparece en el celular, y al revés. Las
        palabras propias sirven para que el teléfono no las autocorrija.
        """
        marco = ttk.Frame(self.pestanas, padding=12)
        self.pestanas.add(marco, text="Diccionario")

        self.texto_dicc = tk.StringVar()
        self.var_antes = tk.StringVar()
        self.var_despues = tk.StringVar()
        self.var_palabra_propia = tk.StringVar()

        correcciones = ttk.LabelFrame(
            marco, text="Correcciones del dictado", padding=8
        )
        correcciones.pack(fill="both", expand=True)
        ttk.Label(
            correcciones,
            text=(
                "Cuando el dictado diga «antes», se escribe «después». Vale para "
                "una palabra o para varias seguidas."
            ),
            foreground="#666",
        ).pack(anchor="w")
        self.tabla_correcciones = ttk.Treeview(
            correcciones, columns=("antes", "despues", "veces"), show="headings", height=7
        )
        for columna, titulo, ancho in (
            ("antes", "Se oye", 240),
            ("despues", "Se escribe", 240),
            ("veces", "Veces", 70),
        ):
            self.tabla_correcciones.heading(columna, text=titulo)
            self.tabla_correcciones.column(columna, width=ancho, anchor="w")
        self.tabla_correcciones.pack(fill="both", expand=True, pady=(6, 6))

        fila = ttk.Frame(correcciones)
        fila.pack(fill="x")
        ttk.Label(fila, text="Antes:").pack(side="left")
        ttk.Entry(fila, textvariable=self.var_antes, width=22).pack(
            side="left", padx=(4, 10)
        )
        ttk.Label(fila, text="Después:").pack(side="left")
        ttk.Entry(fila, textvariable=self.var_despues, width=22).pack(
            side="left", padx=(4, 10)
        )
        ttk.Button(fila, text="Añadir", command=self._anadir_correccion).pack(side="left")
        ttk.Button(
            fila, text="Olvidar la seleccionada", command=self._olvidar_correccion
        ).pack(side="left", padx=(6, 0))

        palabras = ttk.LabelFrame(
            marco, text="Palabras propias (el teclado no las autocorrige)", padding=8
        )
        palabras.pack(fill="both", expand=True, pady=(10, 0))
        self.tabla_palabras_propias = ttk.Treeview(
            palabras, columns=("palabra", "veces"), show="headings", height=6
        )
        for columna, titulo, ancho in (("palabra", "Palabra", 300), ("veces", "Veces", 70)):
            self.tabla_palabras_propias.heading(columna, text=titulo)
            self.tabla_palabras_propias.column(columna, width=ancho, anchor="w")
        self.tabla_palabras_propias.pack(fill="both", expand=True, pady=(6, 6))

        fila = ttk.Frame(palabras)
        fila.pack(fill="x")
        ttk.Label(fila, text="Palabra:").pack(side="left")
        ttk.Entry(fila, textvariable=self.var_palabra_propia, width=26).pack(
            side="left", padx=(4, 10)
        )
        ttk.Button(fila, text="Añadir", command=self._anadir_palabra).pack(side="left")
        ttk.Button(
            fila, text="Olvidar la seleccionada", command=self._olvidar_palabra
        ).pack(side="left", padx=(6, 0))

        pie = ttk.Frame(marco)
        pie.pack(fill="x", pady=(10, 0))
        ttk.Button(
            pie, text="Sincronizar con la cuenta", command=self._sincronizar_cuenta
        ).pack(side="left")
        ttk.Label(pie, textvariable=self.texto_dicc, foreground="#555").pack(
            side="left", padx=(12, 0)
        )

        self._refrescar_diccionario()

    def _dicc(self):
        """El diccionario del perfil: uno solo para toda la ventana.

        Tener dos instancias (una por pestaña) haría que la última en guardar
        pisara lo de la otra.
        """
        if getattr(self, "_diccionario_ventana", None) is None:
            self._diccionario_ventana = DiccionarioEscritorio(sesion.perfil()).cargar()
        return self._diccionario_ventana

    def _refrescar_diccionario(self):
        if not hasattr(self, "tabla_correcciones"):
            return
        dicc = self._dicc()
        dicc.recargar_si_cambio()
        for tabla in (self.tabla_correcciones, self.tabla_palabras_propias):
            for fila in tabla.get_children():
                tabla.delete(fila)
        correcciones = dicc.correcciones_aprendidas(limite=5000)
        palabras = dicc.palabras_aprendidas(limite=5000)
        for antes, despues, veces in correcciones:
            self.tabla_correcciones.insert("", "end", values=(antes, despues, veces))
        for palabra, veces in palabras:
            self.tabla_palabras_propias.insert("", "end", values=(palabra, veces))
        self.texto_dicc.set(
            f"{len(palabras)} palabra(s) y {len(correcciones)} corrección(es) "
            f"del perfil «{sesion.perfil()}»."
        )

    def _anadir_correccion(self):
        antes = self.var_antes.get().strip()
        despues = self.var_despues.get().strip()
        if len(antes) < 2 or len(despues) < 2:
            messagebox.showwarning(
                "Diccionario",
                "Escribe lo que se oye mal y cómo debe escribirse (2 letras o más).",
                parent=self.raiz,
            )
            return
        if antes.lower() == despues.lower():
            messagebox.showwarning(
                "Diccionario", "El antes y el después son iguales.", parent=self.raiz
            )
            return
        self._dicc().registrar_correccion(antes, despues)
        self._dicc().guardar()
        self.var_antes.set("")
        self.var_despues.set("")
        self._refrescar_diccionario()

    def _olvidar_correccion(self):
        seleccion = self.tabla_correcciones.selection()
        if not seleccion:
            messagebox.showinfo(
                "Diccionario", "Elige una corrección de la lista.", parent=self.raiz
            )
            return
        antes, despues = self.tabla_correcciones.item(seleccion[0], "values")[:2]
        self._dicc().olvidar_correccion(antes, despues)
        self._dicc().guardar()
        self._refrescar_diccionario()

    def _anadir_palabra(self):
        palabra = self.var_palabra_propia.get().strip()
        if len(palabra) < 2:
            messagebox.showwarning(
                "Diccionario", "Escribe una palabra de 2 letras o más.", parent=self.raiz
            )
            return
        self._dicc().aprender(palabra)
        self._dicc().guardar()
        self.var_palabra_propia.set("")
        self._refrescar_diccionario()

    def _olvidar_palabra(self):
        seleccion = self.tabla_palabras_propias.selection()
        if not seleccion:
            messagebox.showinfo(
                "Diccionario", "Elige una palabra de la lista.", parent=self.raiz
            )
            return
        palabra = self.tabla_palabras_propias.item(seleccion[0], "values")[0]
        self._dicc().olvidar(palabra)
        self._dicc().guardar()
        self._refrescar_diccionario()

    # --- pestaña Cuenta ---------------------------------------------------

    def _pestana_cuenta(self):
        """Cuenta del puente: entrar, crear, salir y sincronizar el perfil.

        La contraseña NO se guarda: se manda una vez y lo que queda en el disco
        es el token del aparato (`sesion.json`, con permisos 600).
        """
        marco = ttk.Frame(self.pestanas, padding=12)
        self.pestanas.add(marco, text="Cuenta")

        self.var_puente = tk.StringVar(
            value=str(self.datos.get("puente", "") or "").strip()
            or PUENTE_POR_DEFECTO
        )
        self.var_puente_clave = tk.StringVar(
            value=str(self.datos.get("puente_clave", "") or "")
        )
        self.var_usuario_cuenta = tk.StringVar(value=sesion.usuario())
        self.var_email_cuenta = tk.StringVar(value=sesion.cargar().get("email") or "")
        self.var_clave_cuenta = tk.StringVar()
        self.texto_estado_cuenta = tk.StringVar(value=self._describir_sesion())

        puente = ttk.LabelFrame(marco, text="Puente (cuentas y perfil)", padding=8)
        puente.pack(fill="x")
        fila = ttk.Frame(puente)
        fila.pack(fill="x")
        ttk.Label(fila, text="Dirección:", width=16).pack(side="left")
        ttk.Entry(fila, textvariable=self.var_puente).pack(
            side="left", fill="x", expand=True
        )
        fila = ttk.Frame(puente)
        fila.pack(fill="x", pady=(6, 0))
        ttk.Label(fila, text="Clave del proxy:", width=16).pack(side="left")
        ttk.Entry(fila, textvariable=self.var_puente_clave, show="•").pack(
            side="left", fill="x", expand=True
        )
        ttk.Label(
            puente,
            text=(
                "Es el puente web, no el servidor de dictado. Si está en esta misma "
                f"computadora, la dirección es {PUENTE_POR_DEFECTO}. La clave del "
                "proxy solo se rellena si el puente está detrás de nginx."
            ),
            foreground="#666",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        acceso = ttk.LabelFrame(marco, text="Tu cuenta", padding=8)
        acceso.pack(fill="x", pady=(10, 0))
        for etiqueta, variable, oculto in (
            ("Usuario:", self.var_usuario_cuenta, False),
            ("Correo (opcional):", self.var_email_cuenta, False),
            ("Contraseña:", self.var_clave_cuenta, True),
        ):
            fila = ttk.Frame(acceso)
            fila.pack(fill="x", pady=2)
            ttk.Label(fila, text=etiqueta, width=16).pack(side="left")
            ttk.Entry(
                fila,
                textvariable=variable,
                show="•" if oculto else "",
            ).pack(side="left", fill="x", expand=True)

        botones = ttk.Frame(acceso)
        botones.pack(fill="x", pady=(8, 0))
        ttk.Button(botones, text="Entrar", command=self._entrar_cuenta).pack(side="left")
        ttk.Button(botones, text="Crear cuenta", command=self._crear_cuenta).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(botones, text="Cerrar sesión", command=self._salir_cuenta).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            botones, text="Sincronizar diccionario", command=self._sincronizar_cuenta
        ).pack(side="left", padx=(6, 0))

        ttk.Label(
            marco,
            textvariable=self.texto_estado_cuenta,
            foreground="#333",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

    def _config_puente(self):
        """Configuración tal como está en la ventana (aún sin guardar)."""
        datos = dict(self.datos)
        datos["puente"] = self.var_puente.get().strip()
        datos["puente_clave"] = self.var_puente_clave.get().strip()
        return datos

    def _describir_sesion(self):
        datos = sesion.cargar()
        if not datos.get("token"):
            return (
                "Sin sesión. El dictado está desactivado hasta que entres con tu "
                "usuario y contraseña."
            )
        plan = datos.get("plan_nombre") or datos.get("plan") or "—"
        correo = datos.get("email") or "sin correo"
        return (
            f"Sesión de «{datos.get('usuario') or '—'}» ({correo}) · plan {plan} · "
            f"perfil «{datos.get('perfil') or 'personal'}»"
        )

    def _refrescar_cuenta(self):
        self.texto_estado_cuenta.set(self._describir_sesion())

    def _terminar_cuenta(self, mensaje):
        self.texto_estado_cuenta.set(mensaje)
        self.var_usuario_cuenta.set(sesion.usuario())
        self.var_email_cuenta.set(sesion.cargar().get("email") or "")
        # Tras sincronizar (o cambiar de cuenta) las listas pueden haber cambiado.
        self._refrescar_diccionario()

    def _llamar_cuenta(self, funcion, texto_ok):
        """Hace la llamada al puente en una hebra: la ventana no se congela."""

        def trabajar():
            try:
                funcion()
                mensaje = texto_ok
            except perfil_cuenta.ErrorPerfil as e:
                mensaje = str(e)
            except Exception as e:  # cualquier fallo se enseña, no se traga
                mensaje = f"No se pudo completar: {e}"
            self.raiz.after(0, lambda: self._terminar_cuenta(mensaje))

        self.texto_estado_cuenta.set("Contactando con el puente…")
        threading.Thread(target=trabajar, daemon=True).start()

    def _direccion_valida(self):
        """Comprueba la dirección antes de llamar: un error de red no explica nada."""
        direccion = self.var_puente.get().strip()
        if not direccion:
            messagebox.showwarning(
                "Cuenta",
                "Falta la dirección del puente.\n\nSi el puente está en esta misma "
                f"computadora, escribe {PUENTE_POR_DEFECTO}.",
                parent=self.raiz,
            )
            return False
        if not direccion.lower().startswith(("http://", "https://")):
            messagebox.showwarning(
                "Cuenta",
                "La dirección debe empezar por http:// o https://.",
                parent=self.raiz,
            )
            return False
        return True

    def _entrar_cuenta(self):
        usuario = self.var_usuario_cuenta.get().strip()
        clave = self.var_clave_cuenta.get()
        if not usuario or not clave:
            messagebox.showwarning(
                "Cuenta", "Escribe tu usuario y tu contraseña.", parent=self.raiz
            )
            return
        if not self._direccion_valida():
            return
        self._llamar_cuenta(
            lambda: perfil_cuenta.entrar(self._config_puente(), usuario, clave),
            f"Entraste como «{usuario}».",
        )
        self.var_clave_cuenta.set("")

    def _crear_cuenta(self):
        usuario = self.var_usuario_cuenta.get().strip()
        clave = self.var_clave_cuenta.get()
        email = self.var_email_cuenta.get().strip()
        if not usuario or not clave:
            messagebox.showwarning(
                "Cuenta", "Escribe el usuario y una contraseña.", parent=self.raiz
            )
            return
        if len(clave) < 8:
            messagebox.showwarning(
                "Cuenta", "La contraseña debe tener al menos 8 caracteres.",
                parent=self.raiz,
            )
            return
        if not self._direccion_valida():
            return
        self._llamar_cuenta(
            lambda: perfil_cuenta.registrar(
                self._config_puente(), usuario, clave, email
            ),
            f"Cuenta «{usuario}» creada.",
        )
        self.var_clave_cuenta.set("")

    def _salir_cuenta(self):
        if not sesion.hay_sesion():
            messagebox.showinfo("Cuenta", "No hay ninguna sesión abierta.", parent=self.raiz)
            return
        if not self._direccion_valida():
            return
        if not messagebox.askyesno(
            "Cerrar sesión",
            "Se cerrará la sesión en esta computadora y no podrás dictar hasta "
            "que vuelvas a entrar.",
            parent=self.raiz,
        ):
            return
        self._llamar_cuenta(
            lambda: perfil_cuenta.salir(self._config_puente()),
            "Sesión cerrada en esta computadora.",
        )

    def _sincronizar_cuenta(self):
        """Baja, fusiona y sube el diccionario de la cuenta."""
        if not sesion.hay_sesion():
            messagebox.showwarning(
                "Cuenta", "Primero entra con tu usuario y contraseña.", parent=self.raiz
            )
            return
        if not self._direccion_valida():
            return
        resultado = {"cambios": 0}

        def trabajar():
            dicc = self._dicc()
            cambios, error = perfil_cuenta.sincronizar(self._config_puente(), dicc)
            resultado["cambios"] = cambios
            if error:
                raise perfil_cuenta.ErrorPerfil(error)

        self._llamar_cuenta(
            trabajar,
            "Diccionario sincronizado.",  # el detalle se ajusta al volver
        )

    # --- guardar ----------------------------------------------------------

    def _guardar(self):
        datos = dict(self.datos)
        datos["clave"] = self.var_clave.get().strip() or "computadora"

        cierre = [str(f) for f in self.lista_cierre.get(0, "end")]
        datos["frase_fin"] = cierre or ["fin del dictado"]

        datos["verbos"] = {
            normalizar(fila[0]): normalizar(fila[1])
            for fila in self._filas(self.tabla_verbos)
            if fila[0] and fila[1]
        }
        datos["frases"] = {
            normalizar(fila[0]): fila[1]
            for fila in self._filas(self.tabla_frases)
            if fila[0] and fila[1]
        }

        for accion in ("abre", "cerrar"):
            datos[accion] = {}
        for accion, nombre, destino in self._filas(self.tabla_programas):
            datos.setdefault(accion, {})[normalizar(nombre)] = destino

        datos["web"] = {
            normalizar(nombre): url
            for nombre, url in self._filas(self.tabla_web)
            if nombre and url
        }

        # Dirección del puente (cuentas y perfil). Van aquí y no en la sesión
        # porque son del servidor, no del usuario.
        datos["puente"] = self.var_puente.get().strip()
        datos["puente_clave"] = self.var_puente_clave.get().strip()

        for clave, (tipo, var) in self._campos.items():
            if tipo == "bool":
                datos[clave] = bool(var.get())
            elif tipo == "lista":
                datos[clave] = [
                    parte.strip() for parte in str(var.get()).split(",")
                    if parte.strip()
                ] or ([str(self.datos.get(clave, ""))] if self.datos.get(clave) else [])
            else:
                datos[clave] = str(var.get()).strip()

        try:
            with open(self.ruta_json, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception as e:
            messagebox.showerror(
                "Configuración", f"No se pudo guardar:\n{e}", parent=self.raiz
            )
            return

        self.datos = datos
        self.estado.configure(text=f"Guardado en {self.ruta_json}")
        # Refrescar la copia local de `marcas` para que la pestaña Skills y los
        # ejemplos salgan ya con lo que se acaba de guardar.
        try:
            from marcas import aplicar_configuracion

            aplicar_configuracion(datos)
        except Exception:
            pass
        self._avisar_al_cliente()
        self._refrescar_skills()

    def _avisar_al_cliente(self):
        """Manda la señal de recarga; si el cliente no corre, no pasa nada."""
        try:
            import senal_cliente

            senal_cliente.enviar_recargar()
        except Exception:
            pass

    def _cerrar(self):
        self.raiz.destroy()


def main():
    parser = argparse.ArgumentParser(description="Configuración del cliente de dictado")
    parser.add_argument("--json", default="", help="ruta de comandos.json")
    # `parse_known_args` porque en Windows el ejecutable se lanza con
    # `--configuracion`, que aquí no significa nada más.
    args, _desconocidos = parser.parse_known_args()

    ruta = args.json or _ruta_por_defecto()
    raiz = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    VentanaConfiguracion(raiz, ruta)
    raiz.mainloop()


if __name__ == "__main__":
    main()
