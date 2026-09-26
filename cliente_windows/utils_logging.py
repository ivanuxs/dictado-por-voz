"""
Módulo de utilidades para logging y registro del sistema de trading.
"""

import time
import os
import inspect
import sys
import traceback
import threading
from datetime import datetime
from typing import Optional

# Lock global para evitar colisiones entre hilos al escribir logs
_log_lock = threading.Lock()

# Configuración global del módulo
LOG_FILE = "registro_AE_BITCOIN.txt"
DEBUG_MODE = False
CALLER_INFO_LEVEL = 1  # 0=Sin info, 1=Básico, 2=Detallado, 3=Stack completo

# Añadir al inicio del archivo utils_logging.py
CLASS_LOG_FILES = {
    "AsesorExperto": "registro_asesor_informacion_v2.log",
    "asesor_desicion_V2": "registro_asesor_desicion_V2.log",
    "AsesorDesicion": "registro_asesor_desicion_V2.log",
    "PersistenciaSQLite": "registro_persistencia.log",
    "SimpleHandler": "registro_datos_binance.log"
}

# Variable para archivo de log por defecto
DEFAULT_LOG_FILE = "registro_agente_informacion.log"

def set_caller_info_level(level: int):
    """
    Configura el nivel de información del caller.
    
    Args:
        level (int): 
            0 = Sin información del caller
            1 = Clase.método:línea (básico)
            2 = archivo:clase.método:línea (detallado)
            3 = Stack trace completo
    """
    global CALLER_INFO_LEVEL
    CALLER_INFO_LEVEL = max(0, min(3, level))


def _get_caller_info(level: int = 1) -> str:
    """
    Obtiene información del caller según el nivel especificado.
    
    Args:
        level (int): Nivel de detalle (0-3)
        
    Returns:
        str: Información formateada del caller
    """
    if level == 0:
        return ""
    
    try:
        # Obtener frame del caller (saltar este método y imprimir)
        frame = inspect.currentframe()
        if frame and frame.f_back and frame.f_back.f_back:
            caller_frame = frame.f_back.f_back
            
            filename = caller_frame.f_code.co_filename
            function_name = caller_frame.f_code.co_name
            line_number = caller_frame.f_lineno
            
            # Extraer nombre del archivo
            filename_short = os.path.basename(filename)
            
            # Obtener información de la clase
            class_name = None
            if 'self' in caller_frame.f_locals:
                class_name = caller_frame.f_locals['self'].__class__.__name__
            elif 'cls' in caller_frame.f_locals:
                class_name = caller_frame.f_locals['cls'].__name__
            
            # Formatear según el nivel
            if level == 1:  # Básico: Clase.método:línea
                if class_name:
                    return f"[{class_name}.{function_name}:{line_number}] "
                else:
                    return f"[{function_name}:{line_number}] "
                    
            elif level == 2:  # Detallado: archivo:clase.método:línea
                if class_name:
                    return f"[{filename_short}:{class_name}.{function_name}:{line_number}] "
                else:
                    return f"[{filename_short}:{function_name}:{line_number}] "
                    
            elif level == 3:  # Stack completo
                stack = traceback.extract_stack()[:-2]  # Excluir este método e imprimir
                if len(stack) > 0:
                    last_call = stack[-1]
                    if class_name:
                        basic_info = f"[{class_name}.{function_name}:{line_number}] "
                    else:
                        basic_info = f"[{filename_short}:{function_name}:{line_number}] "
                    
                    # Agregar stack reducido (últimas 3 llamadas)
                    stack_info = " -> ".join([
                        f"{os.path.basename(call.filename)}:{call.name}:{call.lineno}"
                        for call in stack[-3:]
                    ])
                    return f"{basic_info}(Stack: {stack_info}) "
            
    except Exception:
        return "[caller_info_error] "
    finally:
        if 'frame' in locals():
            del frame
    
    return ""

# --- Método para leer configuración desde ordenes.cfg ---
def cargar_configuracion_cfg(cfg_path=None):
    """
    Lee el archivo ordenes.cfg y devuelve un diccionario con la configuración.
    """
    import os, re
    if cfg_path is None:
        cfg_path = os.path.join(os.path.dirname(__file__), "ordenes.cfg")
    config = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                match = re.match(r"(\w+)\s*=\s*(.*)", line)
                if match:
                    key, value = match.groups()
                    value = value.strip()
                    # Quitar comillas si existen
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    # Convertir a int o float si es posible
                    if value.isdigit():
                        value = int(value)
                    else:
                        try:
                            value_float = float(value)
                            value = value_float
                        except Exception:
                            pass
                    config[key] = value
    else:
        raise FileNotFoundError(f"No se encontró el archivo de configuración: {cfg_path}")
    return config

def imprimir(cadena, archivo_log=None):
    """
    Imprime la cadena en consola y la guarda en archivo de log siguiendo el formato:
    registro_nombre_del_archivo_nombre_especifico.log
    """
    # Siempre imprimir en consola
    # print(cadena) # Comentado para evitar spam en consola/systemd
    
    try:
        # 1. Obtener el nombre del archivo del caller (sin .py)
        frame = inspect.currentframe()
        caller_fname = "sistema"
        try:
            # El frame f_back es imprimir. f_back.f_back es el caller de imprimir.
            caller_frame = frame.f_back
            if caller_frame:
                fpath = caller_frame.f_code.co_filename
                fname = os.path.basename(fpath)
                
                # Si el caller es este mismo archivo (ej. imprimir_debug), subimos un nivel
                if fname == "utils_logging.py" and caller_frame.f_back:
                    fpath = caller_frame.f_back.f_code.co_filename
                    fname = os.path.basename(fpath)
                
                caller_fname = fname.replace('.py', '')
        finally:
            del frame

        # 2. Construir el nombre del archivo final combinando ambos
        if archivo_log:
            # Limpiar nombre específico de posibles extensiones o prefijos
            especifico = str(archivo_log).replace('.log', '').replace('.txt', '')
            if especifico.startswith("registro_"):
                especifico = especifico.replace("registro_", "", 1)
            
            # Formato: registro_nombre_del_archivo_nombre_especifico.log
            log_file = f"registro_{caller_fname}_{especifico}.log"
        else:
            # Formato: registro_nombre_del_archivo.log
            log_file = f"registro_{caller_fname}.log"

        # 3. Determinar directorio y ruta final
        if getattr(sys, "frozen", False):
            # Ejecutable empaquetado: logs junto al exe (no en la carpeta temporal)
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(base_dir, "logs")
        
        if not os.path.exists(logs_dir):
            try:
                os.makedirs(logs_dir, exist_ok=True)
            except Exception:
                logs_dir = "."

        log_file_path = os.path.join(logs_dir, log_file)

        # 4. Obtener info del caller para el mensaje
        caller_info = _get_caller_info(CALLER_INFO_LEVEL)

        # 5. Preparar mensaje
        ahora = time.time()
        fecha = datetime.fromtimestamp(ahora).strftime("%H:%M del %d/%m/%Y")
        padding = " " * 150
        mensaje_final = f"{fecha}: {cadena}{padding}{caller_info}\n"

        # 6. Escribir al archivo con Lock y reintentos para evitar bloqueos en red
        with _log_lock:
            for _ in range(5): # 5 reintentos
                try:
                    with open(log_file_path, "a", encoding="utf-8", errors='replace') as f:
                        f.write(mensaje_final)
                        break # Éxito
                except PermissionError:
                    time.sleep(0.05) # Esperar 50ms antes de reintentar
                except Exception as e:
                    print(f"Error crítico en log: {e}")
                    break

    except Exception as e:
        print(f"Error al escribir en el archivo de log: {e}")

def imprimir_con_caller(cadena, incluir_stack=False):
    """
    Versión explícita que siempre incluye información del caller.
    """
    level = 3 if incluir_stack else 2
    caller_info = _get_caller_info(level)
    
    try:
        with _log_lock:
            for _ in range(5):
                try:
                    with open(LOG_FILE, "a", encoding="utf-8", errors='replace') as f:
                        ahora = time.time()
                        fecha = datetime.fromtimestamp(ahora).strftime("%H:%M del %d/%m/%Y")
                        f.write(f"{fecha}: {caller_info}{cadena}\n")
                        break
                except PermissionError:
                    time.sleep(0.05)
                except Exception:
                    break
    except Exception as e:
        print(f"Error al escribir en el archivo de log: {e}")


def imprimir_debug_avanzado(cadena, nivel="DEBUG"):
    """
    Función de debug con información detallada del caller.
    """
    if DEBUG_MODE or nivel != "DEBUG":
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # Incluir milisegundos
        caller_info = _get_caller_info(2)  # Nivel detallado
        mensaje_formateado = f"[{timestamp}] [{nivel}] {caller_info}{cadena}"
        
        try:
            with _log_lock:
                for _ in range(5):
                    try:
                        with open(LOG_FILE, "a", encoding="utf-8", errors='replace') as f:
                            ahora = time.time()
                            fecha = datetime.fromtimestamp(ahora).strftime("%H:%M del %d/%m/%Y")
                            f.write(f"{fecha}: {mensaje_formateado}\n")
                            break
                    except PermissionError:
                        time.sleep(0.05)
                    except Exception:
                        break
        except Exception as e:
            print(f"Error al escribir en el archivo de log: {e}")

def set_debug_mode(enabled: bool):
    """Habilita o deshabilita el modo debug."""
    global DEBUG_MODE
    DEBUG_MODE = enabled


def set_log_file(filename: str):
    """Cambia el nombre del archivo de log."""
    global LOG_FILE
    LOG_FILE = filename


def imprimir_debug(cadena, nivel="DEBUG"):
    """
    Función de impresión con niveles de debug.
    
    Args:
        cadena (str): Mensaje a imprimir
        nivel (str): Nivel de debug (DEBUG, INFO, WARNING, ERROR)
    """
    if DEBUG_MODE or nivel != "DEBUG":
        timestamp = datetime.now().strftime("%H:%M:%S")
        mensaje_formateado = f"[{timestamp}] [{nivel}] {cadena}"
        imprimir(mensaje_formateado)


def imprimir_con_separador(cadena, separador="=", longitud=50):
    """
    Imprime un mensaje con separadores decorativos.
    
    Args:
        cadena (str): Mensaje a imprimir
        separador (str): Carácter para el separador
        longitud (int): Longitud de la línea separadora
    """
    linea = separador * longitud
    imprimir(linea)
    imprimir(cadena)
    imprimir(linea)


def imprimir_encabezado(titulo, subtitulo=""):
    """
    Imprime un encabezado formateado.
    
    Args:
        titulo (str): Título principal
        subtitulo (str): Subtítulo opcional
    """
    imprimir_con_separador(f"*** {titulo.upper()} ***", "=", 60)
    if subtitulo:
        imprimir(f"    {subtitulo}")
        imprimir("")


def imprimir_transaccion(tipo_transaccion, detalles):
    """
    Imprime información de transacciones de trading.
    
    Args:
        tipo_transaccion (str): Tipo de transacción (COMPRA, VENTA, CIERRE, etc.)
        detalles (dict): Diccionario con detalles de la transacción
    """
    imprimir_encabezado(f"TRANSACCIÓN: {tipo_transaccion}")
    for clave, valor in detalles.items():
        imprimir(f"  {clave}: {valor}")
    imprimir("")


def limpiar_log():
    """Limpia el archivo de log creando uno nuevo."""
    try:
        with _log_lock:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                ahora = datetime.now()
                encabezado = (
                    ahora.strftime("%H:%M del %d/%m/%Y")
                    + " ****NUEVO REGISTRO DEL ASESOR EXPERTO****\n"
                )
                f.write(encabezado)
        print("Archivo de log limpiado correctamente")
    except Exception as e:
        print(f"Error al limpiar el archivo de log: {e}")


def obtener_tamaño_log():
    """Obtiene el tamaño del archivo de log en bytes."""
    try:
        return os.path.getsize(LOG_FILE)
    except FileNotFoundError:
        return 0
    except Exception as e:
        print(f"Error al obtener tamaño del log: {e}")
        return 0


def rotar_log(max_size_mb=10):
    """
    Rota el archivo de log si supera el tamaño máximo.
    
    Args:
        max_size_mb (int): Tamaño máximo en MB antes de rotar
    """
    tamaño_actual = obtener_tamaño_log()
    tamaño_max_bytes = max_size_mb * 1024 * 1024
    
    if tamaño_actual > tamaño_max_bytes:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nuevo_nombre = f"{LOG_FILE}.{timestamp}.bak"
        
        try:
            os.rename(LOG_FILE, nuevo_nombre)
            limpiar_log()
            imprimir(f"Log rotado. Archivo anterior: {nuevo_nombre}")
        except Exception as e:
            print(f"Error al rotar el log: {e}")


def log_sistema_iniciado():
    """Registra el inicio del sistema con información relevante."""
    imprimir_encabezado("SISTEMA DE TRADING INICIADO")
    imprimir(f"Fecha y hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    imprimir(f"Archivo de log: {LOG_FILE}")
    imprimir(f"Modo debug: {'Activado' if DEBUG_MODE else 'Desactivado'}")
    imprimir("")


def log_evento_trading(evento, datos):
    """
    Registra eventos específicos de trading.
    
    Args:
        evento (str): Tipo de evento (ORDEN_ABIERTA, CANDADO_FORMADO, etc.)
        datos (dict): Datos del evento
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    imprimir(f"[{timestamp}] EVENTO: {evento}")
    
    for clave, valor in datos.items():
        if isinstance(valor, float):
            imprimir(f"  {clave}: {valor:.4f}")
        else:
            imprimir(f"  {clave}: {valor}")
    imprimir("")

def rotar_logs_al_iniciar(filtro=None, logs_dir=None):
    """
    Rota todos los archivos de log que coincidan con el filtro, guardando una copia
    en 'historial' y vaciando el original para la nueva sesión.
    """
    import os
    import glob
    import shutil
    from datetime import datetime

    # Configurar directorio de logs: junto al exe si es ejecutable empaquetado
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    logs_folder = os.path.join(base_dir, "logs")
    
    # Asegurar que existe la carpeta de logs
    if not os.path.exists(logs_folder):
        try:
            os.makedirs(logs_folder)
        except Exception as e:
            print(f">>> [ROTACIÓN] Error al crear carpeta de logs: {e}")
            return

    # Carpeta para el historial
    if logs_dir is None:
        logs_dir = os.path.join(logs_folder, "historial")

    if not os.path.exists(logs_dir):
        try:
            os.makedirs(logs_dir)
        except Exception as e:
            print(f">>> [ROTACIÓN] Error al crear historial: {e}")
            logs_dir = logs_folder

    # Marca de tiempo para el respaldo
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Buscar archivos de log (.log y .txt)
    todos_logs = []
    for ext in ["*.log", "*.txt"]:
        todos_logs.extend(glob.glob(os.path.join(logs_folder, f"registro_{ext}")))
    
    if filtro:
        log_files = [f for f in todos_logs if filtro in os.path.basename(f)]
    else:
        # Si no hay filtro, rotamos todos por defecto
        log_files = todos_logs

    # Procesar cada archivo
    for log_file in log_files:
        try:
            if os.path.exists(log_file) and os.path.getsize(log_file) > 0:
                base_filename = os.path.basename(log_file)
                # Crear nombre de respaldo: registro_xxx_20230101_120000.ext
                name_part, ext_part = os.path.splitext(base_filename)
                backup_name = os.path.join(logs_dir, f"{name_part}_{timestamp}{ext_part}")
                
                # Copiar el contenido al respaldo
                shutil.copy2(log_file, backup_name)
                
                # VACIAR el archivo original (truncar) para empezar de cero sin borrarlo
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(f"--- INICIO DE SESIÓN {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (Rotación Automática) ---\n")
                
                print(f">>> [ROTACIÓN] Log rotado correctamente: {base_filename} -> {os.path.basename(backup_name)}")
        except PermissionError:
            # En Windows/Samba esto es común si el archivo está abierto
            pass 
        except Exception as e:
            print(f">>> [ROTACIÓN] Error inesperado al rotar {log_file}: {e}")

# Lista de todas las funciones disponibles para importación con *
