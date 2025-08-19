import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import paho.mqtt.client as mqtt
import json
import time
import datetime
import threading
import random
import requests
from PIL import Image, ImageTk
import hashlib
import os

# --- Configuración MQTT ---
BROKER_MQTT_HOST = "broker.hivemq.com"
BROKER_MQTT_PUERTO = 1883
ID_CLIENTE_GUI_MQTT = "ClienteGUIInvernaderoPython"

# Temas MQTT a los que nos vamos a suscribir
TEMAS_MQTT = [
    "invernadero/temperatura",
    "invernadero/humedad_aire",
    "invernadero/humedad_suelo",
    "invernadero/luz",
    "invernadero/estado",
    "invernadero/bomba_estado",
    "invernadero/nivel_agua",
    "invernadero/control_led_alerta",
    "invernadero/control_bomba",
    "invernadero/config/wifi",
    "invernadero/status/wifi_connect",
    # --- CAMBIO: Nuevos tópicos para riego automático y escaneo WiFi ---
    "invernadero/control_riego_auto_sensor",
    "invernadero/status/riego_auto_sensor",
    "invernadero/wifi/scan_command", # Para enviar el comando de escaneo
    "invernadero/wifi/scan_results", # Para recibir los resultados del escaneo
    # --- FIN CAMBIO ---
]

# Diccionario para almacenar las últimas lecturas recibidas (o valores de deslizadores)
lecturas_actuales = {
    "temperatura": 25.0,
    "humedad_aire": 60.0,
    "humedad_suelo": 500,
    "luz": 500
}

# --- Nuevas variables globales/configuración ---
CONFIG_FILE = "config.json"
USERS_FILE = "users.json"
SCHEDULE_FILE = "irrigation_schedule.json"

# Rangos de sensores (se cargarán desde CONFIG_FILE)
sensor_ranges = {
    "temperatura": {"ideal_min": 20, "ideal_max": 28, "letal_min": 10, "letal_max": 35},
    "humedad_aire": {"ideal_min": 60, "ideal_max": 80, "letal_min": 30, "letal_max": 95},
    "humedad_suelo": {"ideal_min": 400, "ideal_max": 700, "letal_min": 50, "letal_max": 950}, # letal_min para activar riego, ideal_max para detener
    "luz": {"ideal_min": 200, "ideal_max": 500, "letal_min": 50, "letal_max": 800}
}

# Horarios de riego (se cargarán desde SCHEDULE_FILE)
irrigation_schedule = []

# --- Funciones de utilidad para cargar/guardar configuración ---
def load_config():
    global sensor_ranges
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            try:
                config = json.load(f)
                if "sensor_ranges" in config:
                    # Asegurarse de que todos los sensores estén presentes en la configuración cargada
                    for sensor_key, default_ranges in sensor_ranges.items():
                        if sensor_key in config["sensor_ranges"]:
                            sensor_ranges[sensor_key].update(config["sensor_ranges"][sensor_key])
            except json.JSONDecodeError:
                print(f"Error decoding {CONFIG_FILE}. Using default ranges.")
    print(f"Loaded sensor ranges: {sensor_ranges}")

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump({"sensor_ranges": sensor_ranges}, f, indent=4)
    print(f"Saved sensor ranges: {sensor_ranges}")

def load_schedule():
    global irrigation_schedule
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, 'r') as f:
            try:
                irrigation_schedule = json.load(f)
            except json.JSONDecodeError:
                print(f"Error decoding {SCHEDULE_FILE}. Starting with empty schedule.")
    print(f"Loaded irrigation schedule: {irrigation_schedule}")

def save_schedule():
    with open(SCHEDULE_FILE, 'w') as f:
        json.dump(irrigation_schedule, f, indent=4)
    print(f"Saved irrigation schedule: {irrigation_schedule}")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

# Cargar configuración al inicio
load_config()
load_schedule()

# --- Simulación de Crecimiento de Planta ---

class Planta:
    """
    Representa una planta con atributos que cambian con el tiempo según factores ambientales.
    """
    def __init__(self, nombre="Corona de Cristo", etapa_crecimiento="Semilla", edad_dias=0, altura_cm=0.5):
        self.nombre = nombre
        self.etapa_crecimiento = etapa_crecimiento # Semilla, Brote, Joven, Madura, Floración, Fructificación, Muerta
        self.edad_dias = edad_dias
        self.altura_cm = altura_cm
        self.salud = 100 # Porcentaje, 0-100
        self.esta_muerta = False # Indicador de si la planta está muerta
        self.color_planta = "#4CAF50" # Color verde saludable inicial
        self.altura_maxima_cm = 50.0 # Altura máxima que puede alcanzar la planta (ej. 50 cm)
        self.tipo_planta = "Sombra" # Tipo de planta, por defecto Sombra

    def __str__(self):
        return (f"--- Estado de {self.nombre} ---\n"
                f"  Etapa de Crecimiento: {self.etapa_crecimiento}\n"
                f"  Edad: {self.edad_dias:.2f} días\n"
                f"  Altura: {self.altura_cm:.1f} cm\n"
                f"  Salud: {self.salud:.1f}%\n"
                f"  Estado: {'Muerta' if self.esta_muerta else 'Viva'}\n"
                f"--------------------------")

    def obtener_color_etapa(self):
        """Devuelve un color basado en la salud de la planta."""
        if self.esta_muerta:
            return "#616161" # Color gris/muerta
        elif self.salud >= 75:
            return "#4CAF50" # Verde saludable
        elif self.salud >= 50:
            return "#FFC107" # Amarillento/estresado
        elif self.salud >= 25:
            return "#FF5722" # Naranja/poco saludable
        else:
            return "#B71C1C" # Rojo/casi muerta

def obtener_puntuacion_factor(valor, ideal_min, ideal_max, letal_min, letal_max):
    """
    Calcula una puntuación (0-1) para un factor ambiental.
    1.0 si está en el rango ideal.
    0.0 si está fuera del rango letal.
    Se interpola linealmente entre ideal y letal.
    """
    if valor is None:
        return 0.5 # Neutral si no hay datos

    if letal_min <= valor <= letal_max:
        if ideal_min <= valor <= ideal_max:
            return 1.0 # Ideal
        elif valor < ideal_min:
            # Interpolar entre letal_min e ideal_min
            if ideal_min == letal_min: return 0.0 # Evitar división por cero
            return (valor - letal_min) / (ideal_min - letal_min)
        else: # valor > ideal_max
            # Interpolar entre ideal_max y letal_max
            if letal_max == ideal_max: return 0.0 # Evitar división por cero
            return 1.0 - (valor - ideal_max) / (letal_max - ideal_max)
    else:
        return 0.0 # Letal

def simular_crecimiento(planta, dias_transcurridos, lecturas_ambiente):
    """
    Simula el crecimiento y la salud de una planta basándose en el tiempo transcurrido y las lecturas ambientales.
    Incluye condiciones de muerte y recuperación.
    """
    if planta.esta_muerta:
        # La planta se marchita gradualmente si está muerta
        planta.altura_cm = max(0.2, planta.altura_cm - (0.05 * dias_transcurridos))
        return # No hay crecimiento ni cambios de salud si está muerta

    planta.edad_dias += dias_transcurridos

    # Usar los rangos cargados globalmente
    temp_ranges = sensor_ranges["temperatura"]
    hum_air_ranges = sensor_ranges["humedad_aire"]
    hum_soil_ranges = sensor_ranges["humedad_suelo"]
    luz_ranges = sensor_ranges["luz"]

    # Calcular puntuaciones individuales de factores
    puntuacion_temp = obtener_puntuacion_factor(lecturas_ambiente.get("temperatura"), temp_ranges["ideal_min"], temp_ranges["ideal_max"], temp_ranges["letal_min"], temp_ranges["letal_max"])
    puntuacion_humedad_aire = obtener_puntuacion_factor(lecturas_ambiente.get("humedad_aire"), hum_air_ranges["ideal_min"], hum_air_ranges["ideal_max"], hum_air_ranges["letal_min"], hum_air_ranges["letal_max"])
    puntuacion_humedad_suelo = obtener_puntuacion_factor(lecturas_ambiente.get("humedad_suelo"), hum_soil_ranges["ideal_min"], hum_soil_ranges["ideal_max"], hum_soil_ranges["letal_min"], hum_soil_ranges["letal_max"])
    puntuacion_luz = obtener_puntuacion_factor(lecturas_ambiente.get("luz"), luz_ranges["ideal_min"], luz_ranges["ideal_max"], luz_ranges["letal_min"], luz_ranges["letal_max"])

    # Calcular puntuación ambiental promedio
    puntuaciones_ambientales = [puntuacion_temp, puntuacion_humedad_aire, puntuacion_humedad_suelo, puntuacion_luz]
    puntuacion_ambiente_promedio = sum(puntuaciones_ambientales) / len(puntuaciones_ambientales)

    # --- Impacto en la Salud ---
    sensibilidad_salud = 50.0 # Qué tan rápido cambia la salud
    cambio_salud_por_dia = (puntuacion_ambiente_promedio - 0.5) * sensibilidad_salud # Rango de -25 a +25

    planta.salud += cambio_salud_por_dia * dias_transcurridos
    planta.salud = max(0, min(100, planta.salud)) # Mantener la salud entre 0 y 100

    # Comprobar condición de muerte
    if planta.salud <= 0.1: # La planta muere si la salud cae a (casi) cero
        planta.esta_muerta = True
        planta.salud = 0
        planta.etapa_crecimiento = "Muerta"
        planta.altura_cm = max(0.2, planta.altura_cm * 0.9) # Empieza a marchitarse/encogerse si está muerta
        return # Detener el crecimiento y los cambios de etapa

    # --- Impacto en el Crecimiento ---
    # La tasa de crecimiento es proporcional a la puntuación ambiental y a la salud de la planta.
    # Ajustado para que la planta pueda alcanzar la altura máxima en ~120 días (4 meses) bajo condiciones ideales
    tasa_crecimiento_base_cm_por_dia = 0.4125 # Crecimiento base en condiciones ideales y salud perfecta (49.5 cm en 120 días)
    tasa_crecimiento_actual = tasa_crecimiento_base_cm_por_dia * puntuacion_ambiente_promedio * (planta.salud / 100.0)

    planta.altura_cm += tasa_crecimiento_actual * dias_transcurridos
    # Asegurarse de que la altura no exceda la altura máxima
    planta.altura_cm = min(planta.altura_cm, planta.altura_maxima_cm)
    planta.altura_cm = max(0.5, planta.altura_cm) # Altura mínima

    # Actualizar la etapa de crecimiento según la edad y la altura (solo si no está muerta)
    if planta.edad_dias >= 100 and planta.etapa_crecimiento != "Fructificación": # Ajustado para 4 meses
        planta.etapa_crecimiento = "Fructificación"
    elif planta.edad_dias >= 70 and planta.etapa_crecimiento != "Floración": # Ajustado para 4 meses
        planta.etapa_crecimiento = "Floración"
    elif planta.edad_dias >= 40 and planta.etapa_crecimiento != "Madura": # Ajustado para 4 meses
        planta.etapa_crecimiento = "Madura"
    elif planta.edad_dias >= 15 and planta.etapa_crecimiento != "Joven": # Ajustado para 4 meses
        planta.etapa_crecimiento = "Joven"
    elif planta.edad_dias >= 5 and planta.etapa_crecimiento != "Brote": # Ajustado para 4 meses
        planta.etapa_crecimiento = "Brote"

# --- Aplicación GUI ---

class AuthWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Login / Registro")
        self.geometry("300x200")
        self.resizable(False, False)
        self.grab_set() # Hace que esta ventana sea modal
        self.protocol("WM_DELETE_WINDOW", self.on_closing) # Manejar cierre de ventana

        self.users = load_users()

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(expand=True, fill="both")

        ttk.Label(main_frame, text="Usuario:").pack(pady=5)
        self.username_entry = ttk.Entry(main_frame)
        self.username_entry.pack(pady=5)

        ttk.Label(main_frame, text="Contraseña:").pack(pady=5)
        self.password_entry = ttk.Entry(main_frame, show="*")
        self.password_entry.pack(pady=5)

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10)

        ttk.Button(button_frame, text="Login", command=self.login).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Registrar", command=self.register).pack(side="left", padx=5)

    def login(self):
        username = self.username_entry.get()
        password = self.password_entry.get()
        hashed_password = hash_password(password)

        if username in self.users and self.users[username] == hashed_password:
            messagebox.showinfo("Login Exitoso", f"Bienvenido, {username}!")
            self.destroy()
            self.parent.show_main_app() # Llama a un método en AppInvernadero para mostrar la GUI principal
        else:
            messagebox.showerror("Error de Login", "Usuario o contraseña incorrectos.")

    def register(self):
        username = self.username_entry.get()
        password = self.password_entry.get()

        if not username or not password:
            messagebox.showwarning("Registro", "Usuario y contraseña no pueden estar vacíos.")
            return

        if username in self.users:
            messagebox.showwarning("Registro", "El usuario ya existe.")
        else:
            self.users[username] = hash_password(password)
            save_users(self.users)
            messagebox.showinfo("Registro Exitoso", f"Usuario {username} registrado exitosamente.")

    def on_closing(self):
        if messagebox.askokcancel("Salir", "¿Deseas salir de la aplicación?"):
            self.parent.destroy() # Cierra la aplicación principal si se cierra la ventana de login


class AppInvernadero(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw() # Oculta la ventana principal hasta que el login sea exitoso
        self.title("Simulador de Invernadero")
        self.geometry("1200x900")
        self.configure(bg="#212121")
        self.resizable(True, True)

        self.planta = Planta(nombre="Corona de Cristo", etapa_crecimiento="Semilla", edad_dias=0, altura_cm=0.5)
        self.ultima_actualizacion_tiempo = datetime.datetime.now()

        # Historial de valores para el gráfico de barras (para Agua)
        self.historial_agua = []
        self.max_historial_agua = 10

        # Historial de altura de la planta para el gráfico
        self.historial_altura = []
        self.historial_tiempo_dias = []
        self.max_puntos_grafico = 150

        # Estado del tanque de agua y bomba (actualizados por MQTT)
        self.nivel_tanque_agua = 100
        self.bomba_activa = False
        self.fotograma_animacion_bomba = 0
        self.alerta_led_activo = False

        self.esp32_wifi_status = "Desconocido"
        self.etiqueta_esp32_wifi_status = None

        # Inicializar los diccionarios de etiquetas aquí (solo una vez)
        self.etiquetas_estado = {}
        self.etiquetas_planta_info = {}

        # Referencia al lienzo de animación de la planta (ahora en la ventana principal)
        self.lienzo_animacion_planta = None
        self.lienzo_tanque_agua = None
        self.lienzo_grafico_altura = None
        self.lienzo_estado_alerta_led = None

        # Referencias a las etiquetas de los sensores (ahora en el nuevo panel inferior)
        self.etiqueta_sens_temp = None
        self.etiqueta_sens_hum_aire = None
        self.etiqueta_sens_hum_suelo = None
        self.etiqueta_sens_luz = None

        # INICIO DE MODIFICACIÓN: CARGAR IMÁGENES
        self.img_control_general = None
        self.img_notificacion = None
        self.img_sensor = None
        self.img_info_planta = None
        self.img_coronaplanta = None
        self.img_temp_sensor = None

        try:
            self.img_control_general = ImageTk.PhotoImage(Image.open("icono_control.png"))
            self.img_notificacion = ImageTk.PhotoImage(Image.open("icono_alerta.png"))
            self.img_sensor = ImageTk.PhotoImage(Image.open("icono_sensor.png"))
            self.img_info_planta = ImageTk.PhotoImage(Image.open("icono_planta_info.png"))

            self.img_coronaplanta = ImageTk.PhotoImage(Image.open("coronaplanta.png"))
            self.img_temp_sensor = ImageTk.PhotoImage(Image.open("temp.png"))

        except Exception as e:
            print(f"ERROR AL CARGAR UNA IMAGEN: {e}. Asegúrate de que la ruta sea correcta y el formato compatible (PNG/JPG con Pillow, GIF directo).")
        # FIN DE MODIFICACIÓN

        # Iniciar la ventana de autenticación
        self.auth_window = AuthWindow(self)

        # Hilo para verificar el riego programado
        self.irrigation_check_thread = threading.Thread(target=self.verificar_riego_programado, daemon=True)
        self.irrigation_check_thread.start()

        # Variable para controlar el estado del riego automático por sensor
        self.riego_automatico_activo = False


    def show_main_app(self):
        """Muestra la ventana principal después de un login exitoso."""
        self.deiconify() # Muestra la ventana principal
        self.crear_widgets() # Crea los widgets de la GUI principal

        # Configurar el cliente MQTT de la GUI
        self.cliente_mqtt_gui = mqtt.Client(client_id=ID_CLIENTE_GUI_MQTT)
        self.cliente_mqtt_gui.on_connect = self._al_conectar_gui
        self.cliente_mqtt_gui.on_message = self._al_recibir_mensaje_gui
        self.cliente_mqtt_gui.reconnect_delay_set(min_delay=1, max_delay=120)
        self.cliente_mqtt_gui.connect(BROKER_MQTT_HOST, BROKER_MQTT_PUERTO, 60)
        self.cliente_mqtt_gui.loop_start() # Iniciar el bucle en segundo segundo plano

        self.actualizar_lecturas_ambiente_desde_deslizadores() # Cargar valores iniciales de los deslizadores en lecturas_actuales
        self.dibujar_marco_invernadero(self.lienzo_animacion_planta) # Dibujo inicial del marco del invernadero
        self.dibujar_planta() # Dibujo inicial de la planta
        self.dibujar_tanque_agua() # Dibujo inicial del tanque de agua
        self.dibujar_indicador_alerta_led() # Dibujo inicial del indicador del LED de alerta
        self.after(100, self.actualizar_gui) # Programar la primera actualización de la GUI después de un breve retraso

    def _al_conectar_gui(self, cliente, datos_usuario, banderas, codigo_retorno):
        if codigo_retorno == 0:
            print("GUI MQTT: Conectado al broker MQTT exitosamente!")
            for tema in TEMAS_MQTT:
                cliente.subscribe(tema)
                print(f"GUI MQTT: Suscrito a: {tema}")
        else:
            print(f"GUI MQTT: Fallo al conectar, código de retorno: {codigo_retorno}\n")

    def _al_recibir_mensaje_gui(self, cliente, datos_usuario, mensaje):
        """Se llama cuando se recibe un mensaje del broker MQTT (para la GUI)."""
        try:
            carga_util_str = mensaje.payload.decode()
            print(f"DEBUG MQTT RECIBIDO: Tema='{mensaje.topic}', Carga='{carga_util_str}'")

            # Actualizar lecturas_actuales con los datos recibidos del ESP32 real
            if mensaje.topic == "invernadero/temperatura":
                lecturas_actuales["temperatura"] = float(carga_util_str)
                print(f"DEBUG: Temperatura actualizada a {lecturas_actuales['temperatura']}")
                self.deslizador_temp.set(lecturas_actuales["temperatura"]) # Actualizar deslizador
            elif mensaje.topic == "invernadero/humedad_aire":
                lecturas_actuales["humedad_aire"] = float(carga_util_str)
                print(f"DEBUG: Humedad Aire actualizada a {lecturas_actuales['humedad_aire']}")
                self.deslizador_humedad_aire.set(lecturas_actuales["humedad_aire"])
            elif mensaje.topic == "invernadero/humedad_suelo":
                lecturas_actuales["humedad_suelo"] = int(carga_util_str)
                print(f"DEBUG: Humedad Suelo actualizada a {lecturas_actuales['humedad_suelo']}")
                self.deslizador_humedad_suelo.set(lecturas_actuales["humedad_suelo"])
            elif mensaje.topic == "invernadero/luz":
                lecturas_actuales["luz"] = int(carga_util_str)
                print(f"DEBUG: Luz actualizada a {lecturas_actuales['luz']}")
                self.deslizador_luz.set(lecturas_actuales["luz"])
            elif mensaje.topic == "invernadero/nivel_agua":
                # ESP32 ahora envía el porcentaje directamente (0-100)
                self.nivel_tanque_agua = int(carga_util_str)
                print(f"DEBUG: Nivel Agua actualizado a {self.nivel_tanque_agua}")
            elif mensaje.topic == "invernadero/bomba_estado":
                self.bomba_activa = (carga_util_str == "ON")
                print(f"DEBUG: Bomba activa: {self.bomba_activa}")
            elif mensaje.topic == "invernadero/control_led_alerta":
                self.alerta_led_activo = (carga_util_str == "ON")
                print(f"DEBUG: LED Alerta activo: {self.alerta_led_activo}")
                self.dibujar_indicador_alerta_led() # Actualizar visualmente el indicador
            elif mensaje.topic == "invernadero/status/wifi_connect":
                self.esp32_wifi_status = carga_util_str
                print(f"DEBUG: Estado WiFi del ESP32: {self.esp32_wifi_status}")
                self.actualizar_estado_wifi_esp32_gui() # Actualizar la etiqueta en la GUI
            # --- CAMBIO: Manejo del estado del riego automático por sensor ---
            elif mensaje.topic == "invernadero/status/riego_auto_sensor":
                if carga_util_str == "ON":
                    self.riego_automatico_activo = True
                    self.boton_riego_auto_sensor.config(text="Desactivar Riego Auto (Sensor)", style="TButton")
                else:
                    self.riego_automatico_activo = False
                    self.boton_riego_auto_sensor.config(text="Activar Riego Auto (Sensor)", style="TButton")
                print(f"DEBUG: Riego automático por sensor: {carga_util_str}")
            # --- FIN CAMBIO ---
            # --- CAMBIO: Manejo de resultados de escaneo WiFi (para WiFiScannerGUI) ---
            elif mensaje.topic == "invernadero/wifi/scan_results":
                # Esta parte es para la clase WiFiScannerGUI, si se usa.
                # La AppInvernadero principal no la usa directamente, pero es bueno tenerla aquí.
                try:
                    scan_data = json.loads(carga_util_str)
                    print(f"DEBUG: Resultados de escaneo WiFi: {scan_data}")
                    # Si tu AppInvernadero principal necesita mostrar esto, lo harías aquí.
                    # Por ahora, solo se imprime.
                except json.JSONDecodeError:
                    print(f"ERROR: No se pudo decodificar JSON de resultados de escaneo: {carga_util_str}")
            # --- FIN CAMBIO ---

            # Forzar actualización de etiquetas de deslizadores después de recibir MQTT
            # Estas líneas son importantes para que los deslizadores y sus etiquetas reflejen el valor recibido
            self.etiqueta_valor_temp.config(text=f"{lecturas_actuales['temperatura']:.1f}°C")
            self.etiqueta_valor_humedad_aire.config(text=f"{lecturas_actuales['humedad_aire']:.1f}%")
            self.etiqueta_valor_luz.config(text=f"{lecturas_actuales['luz']:.0f}")
            self.etiqueta_valor_humedad_suelo.config(text=f"{lecturas_actuales['humedad_suelo']:.0f}")

        except ValueError as ve:
            print(f"ERROR GUI MQTT: Error al convertir datos MQTT: {ve} para mensaje '{mensaje.payload.decode()}' en tema '{mensaje.topic}'")
        except Exception as e:
            print(f"ERROR GUI MQTT: Error general al procesar el mensaje MQTT: {e}")

    def crear_widgets(self):
        # Configuración de estilos para un look más "oscuro" y "redondeado"
        estilo = ttk.Style(self)
        estilo.theme_use('clam')
        estilo.configure("TLabel", background="#333333", foreground="white", font=("Arial", 10))
        estilo.configure("TFrame", background="#333333", borderwidth=0, relief="flat")
        estilo.configure("TLabelframe", background="#333333", foreground="white", relief="solid", borderwidth=2, bordercolor="#424242")
        estilo.configure("TLabelframe.Label", background="#333333", foreground="white", font=("Arial", 11, "bold"))
        estilo.configure("TScale", background="#333333", troughcolor="#424242", slidercolor="#616161")
        estilo.configure("TButton", background="#424242", foreground="white", font=("Arial", 10, "bold"), relief="raised")
        estilo.map("TButton", background=[('active', '#525252')])

        # Crear el Notebook (sistema de pestañas)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(expand=True, fill="both", padx=5, pady=5)

        # Pestaña de Simulación (contiene la GUI existente)
        self.frame_simulacion = ttk.Frame(self.notebook, style="TFrame")
        self.notebook.add(self.frame_simulacion, text="Simulación Invernadero")

        # Configurar el grid para la pestaña de simulación
        self.frame_simulacion.grid_rowconfigure(0, weight=1)
        self.frame_simulacion.grid_rowconfigure(1, weight=1)
        self.frame_simulacion.grid_rowconfigure(2, weight=1)
        self.frame_simulacion.grid_rowconfigure(3, weight=0)

        self.frame_simulacion.grid_columnconfigure(0, weight=1)
        self.frame_simulacion.grid_columnconfigure(1, weight=2)
        self.frame_simulacion.grid_columnconfigure(2, weight=1)

        # Reubicar los paneles existentes dentro de self.frame_simulacion
        # Panel 1: Tanque de Agua y Bomba (Superior Izquierda)
        self.marco_tanque_agua = ttk.LabelFrame(self.frame_simulacion, text="Tanque de Agua y Bomba", padding="10", style="TLabelframe")
        self.marco_tanque_agua.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_tanque_agua(self.marco_tanque_agua)

        # Panel 2: Control Manual de Sensores (Media Izquierda)
        self.marco_pc_temp = ttk.LabelFrame(self.frame_simulacion, text="Control Manual de Sensores", padding="10", style="TLabelframe")
        self.marco_pc_temp.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_pc_temp(self.marco_pc_temp)

        # Panel 3: Gestión de Agua - Gráfico (Inferior Izquierda)
        self.marco_grafico_agua = ttk.LabelFrame(self.frame_simulacion, text="Gestión de Agua", padding="10", style="TLabelframe")
        self.marco_grafico_agua.grid(row=2, column=0, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_grafico_agua(self.marco_grafico_agua)

        # Lienzo de Animación de la Planta (Centro)
        self.marco_vista_invernadero = ttk.LabelFrame(self.frame_simulacion, text="Vista de la Planta", padding="10", style="TLabelframe")
        self.marco_vista_invernadero.grid(row=0, column=1, rowspan=2, padx=5, pady=5, sticky="nsew")
        self.lienzo_animacion_planta = tk.Canvas(self.marco_vista_invernadero, bg="#263238", highlightthickness=0)
        self.lienzo_animacion_planta.pack(expand=True, fill="both", padx=10, pady=10)
        self.lienzo_animacion_planta.bind("<Configure>", self.redimensionar_lienzo_animacion_planta)

        # Gráfico de Altura (Centro Inferior)
        self.marco_grafico_altura = ttk.LabelFrame(self.frame_simulacion, text="Gráfico: Altura de la Planta vs. Tiempo", padding="10", style="TLabelframe")
        self.marco_grafico_altura.grid(row=2, column=1, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_grafico_altura(self.marco_grafico_altura)

        # Panel 5: Notificaciones (Superior Derecha)
        self.marco_notificacion = ttk.LabelFrame(self.frame_simulacion, text="Notificaciones", padding="10", style="TLabelframe")
        self.marco_notificacion.grid(row=0, column=2, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_notificacion(self.marco_notificacion)

        # Panel 6: Control de Emergencia (Media Derecha)
        self.marco_nivel_notificador = ttk.LabelFrame(self.frame_simulacion, text="Control de Emergencia", padding="10", style="TLabelframe")
        self.marco_nivel_notificador.grid(row=1, column=2, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_nivel_notificador(self.marco_nivel_notificador)

        # Panel 7: Información de la Planta (Combinado Especie/Crecimiento) (Inferior Derecha)
        self.marco_info_planta_general = ttk.LabelFrame(self.frame_simulacion, text="Información de Planta", padding="10", style="TLabelframe")
        self.marco_info_planta_general.grid(row=2, column=2, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_info_planta(self.marco_info_planta_general)

        # Controles Generales (Abajo, abarca las dos primeras columnas)
        self.marco_controles = ttk.LabelFrame(self.frame_simulacion, text="Controles Generales", padding="10", style="TLabelframe")
        self.marco_controles.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")
        self.crear_controles_generales(self.marco_controles)

        # Nuevo Panel para Lecturas de Sensores (Abajo, tercera columna)
        self.marco_lecturas_sensores_bottom = ttk.LabelFrame(self.frame_simulacion, text="Lecturas Actuales Sensores", padding="10", style="TLabelframe")
        self.marco_lecturas_sensores_bottom.grid(row=3, column=2, padx=5, pady=5, sticky="nsew")
        self.crear_widgets_lecturas_sensores_bottom(self.marco_lecturas_sensores_bottom)

        # Pestaña de Programación de Riego
        self.frame_riego = ttk.Frame(self.notebook, style="TFrame")
        self.notebook.add(self.frame_riego, text="Programación de Riego")
        self.crear_widgets_programacion_riego(self.frame_riego)

        # Pestaña de Rangos de Sensores
        self.frame_rangos = ttk.Frame(self.notebook, style="TFrame")
        self.notebook.add(self.frame_rangos, text="Rangos de Sensores")
        self.crear_widgets_rangos_sensores(self.frame_rangos)


    def crear_widgets_tanque_agua(self, marco_padre):
        self.lienzo_tanque_agua = tk.Canvas(marco_padre, bg="#263238", highlightthickness=0, width=150, height=150)
        self.lienzo_tanque_agua.pack(expand=True, fill="both", padx=5, pady=5)
        self.lienzo_tanque_agua.bind("<Configure>", lambda event: self.dibujar_tanque_agua())

        self.boton_rellenar_tanque = ttk.Button(marco_padre, text="Rellenar Tanque (Solo GUI)", command=self.rellenar_tanque_agua, style="TButton")
        self.boton_rellenar_tanque.pack(pady=5)

    def dibujar_tanque_agua(self, evento=None):
        lienzo = self.lienzo_tanque_agua
        if not lienzo or not lienzo.winfo_exists():
            return

        lienzo.delete("all")
        lienzo_ancho = lienzo.winfo_width()
        lienzo_alto = lienzo.winfo_height()

        if lienzo_ancho < 50 or lienzo_alto < 50: return

        # Dimensiones del tanque (proporcionales al lienzo)
        ancho_tanque = lienzo_ancho * 0.7
        alto_tanque = lienzo_alto * 0.8
        tanque_x1 = (lienzo_ancho - ancho_tanque) / 2
        tanque_y1 = (lienzo_alto - alto_tanque) / 2
        tanque_x2 = tanque_x1 + ancho_tanque
        tanque_y2 = tanque_y1 + alto_tanque

        # Dibujar el contorno del tanque
        lienzo.create_rectangle(tanque_x1, tanque_y1, tanque_x2, tanque_y2, outline="#90A4AE", width=2)

        # Dibujar el nivel del agua
        altura_llenado_agua = alto_tanque * (self.nivel_tanque_agua / 100.0)
        agua_y1 = tanque_y2 - altura_llenado_agua
        lienzo.create_rectangle(tanque_x1 + 1, agua_y1, tanque_x2 - 1, tanque_y2 - 1, fill="#2196F3", outline="")

        # Dibujar la bomba y la salida de agua
        bomba_x = tanque_x2 + 5
        bomba_y = tanque_y2 - 20
        lienzo.create_rectangle(bomba_x, bomba_y, bomba_x + 10, bomba_y + 20, fill="#757575", outline="#424242")
        lienzo.create_line(bomba_x + 5, bomba_y + 10, tanque_x2, tanque_y2 - 10, fill="#757575", width=2)

        # La animación de la bomba ahora depende de self.bomba_activa (controlada por MQTT)
        if self.bomba_activa:
            # Animación de agua saliendo
            for i in range(self.fotograma_animacion_bomba):
                desplazamiento_x = random.randint(-2, 2)
                desplazamiento_y = random.randint(-2, 2)
                lienzo.create_oval(bomba_x + 2 + desplazamiento_x, bomba_y + 25 + (i*3) + desplazamiento_y,
                                   bomba_x + 8 + desplazamiento_x, bomba_y + 31 + (i*3) + desplazamiento_y,
                                   fill="#81D4FA", outline="#2196F3", tags="agua_bomba")
            self.fotograma_animacion_bomba = (self.fotograma_animacion_bomba + 1) % 5
        else:
            self.fotograma_animacion_bomba = 0
            lienzo.delete("agua_bomba")

        # Mostrar el porcentaje del tanque
        lienzo.create_text(lienzo_ancho / 2, tanque_y1 + 15, text=f"{self.nivel_tanque_agua:.0f}%", fill="white", font=("Arial", 10, "bold"))


    def rellenar_tanque_agua(self):
        self.nivel_tanque_agua = 100
        self.dibujar_tanque_agua()
        print("Tanque de agua rellenado (solo visualmente en la GUI).")


    def redimensionar_lienzo_animacion_planta(self, evento):
        """Redibuja el invernadero y la planta cuando el lienzo de la ventana de animación cambia de tamaño."""
        if self.lienzo_animacion_planta:
            self.dibujar_marco_invernadero(self.lienzo_animacion_planta)
            self.dibujar_planta()


    def crear_controles_generales(self, marco_padre):
        marco_padre.grid_columnconfigure(0, weight=1)
        marco_padre.grid_columnconfigure(1, weight=1)
        marco_padre.grid_rowconfigure(0, weight=1)
        marco_padre.grid_rowconfigure(1, weight=1)
        marco_padre.grid_rowconfigure(2, weight=0)

        # Marco para controles de simulación de tiempo
        marco_controles_sim = ttk.LabelFrame(marco_padre, text="Control de Simulación", padding="5", style="TLabelframe")
        marco_controles_sim.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")

        marco_controles_sim.grid_columnconfigure(1, weight=1)

        ttk.Label(marco_controles_sim, text="Avanzar Días:", style="TLabel").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.entrada_dias_avanzar = ttk.Entry(marco_controles_sim, width=8)
        self.entrada_dias_avanzar.insert(0, "1")
        self.entrada_dias_avanzar.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        self.boton_avanzar_dias = ttk.Button(marco_controles_sim, text="Avanzar", command=self.avanzar_dias_simulacion, style="TButton")
        self.boton_avanzar_dias.grid(row=0, column=2, sticky="ew", padx=5, pady=2)

        self.boton_reiniciar_planta = ttk.Button(marco_controles_sim, text="Reiniciar Planta", command=self.reiniciar_planta, style="TButton")
        self.boton_reiniciar_planta.grid(row=1, column=0, columnspan=3, sticky="ew", padx=5, pady=5)

        # INICIO DE MODIFICACIÓN: CONTROL DE BOMBA (MOVIDO Y REVISADO PARA VISIBILIDAD)
        marco_control_bomba = ttk.LabelFrame(marco_padre, text="Control Bomba Agua", padding="5", style="TLabelframe")
        marco_control_bomba.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")

        marco_control_bomba.grid_columnconfigure(0, weight=1)
        marco_control_bomba.grid_columnconfigure(1, weight=1)

        self.boton_bomba_on = ttk.Button(marco_control_bomba, text="Bomba ON", command=lambda: self.controlar_bomba("ON"), style="TButton")
        self.boton_bomba_on.grid(row=0, column=0, padx=5, pady=5, sticky="ew")

        self.boton_bomba_off = ttk.Button(marco_control_bomba, text="Bomba OFF", command=lambda: self.controlar_bomba("OFF"), style="TButton")
        self.boton_bomba_off.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        # Botón para activar/desactivar riego automático por sensor
        self.boton_riego_auto_sensor = ttk.Button(marco_control_bomba, text="Activar Riego Auto (Sensor)", command=self.toggle_riego_automatico_sensor, style="TButton")
        self.boton_riego_auto_sensor.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="ew")

        # FIN DE MODIFICACIÓN

        # --- PARA IMÁGENES ---
        self.canvas_controles_imagenes = tk.Canvas(marco_padre, bg="#263238", highlightthickness=0, height=50)
        self.canvas_controles_imagenes.grid(row=0, column=1, rowspan=2, padx=5, pady=5, sticky="nsew")

        # INICIO DE MODIFICACIÓN: DIBUJAR IMAGEN EN CONTROLES GENERALES
        if self.img_control_general:
            img_width = self.img_control_general.width()
            img_height = self.img_control_general.height()

            canvas_width = self.canvas_controles_imagenes.winfo_width()
            canvas_height = self.canvas_controles_imagenes.winfo_height()

            if canvas_width > 1 and canvas_height > 1:
                ratio_w = canvas_width / img_width
                ratio_h = canvas_height / img_height
                scale_factor = min(ratio_w, ratio_h)

                new_width = int(img_width * scale_factor)
                new_height = int(img_height * scale_factor)

                try:
                    original_image = Image.open("icono_control.png")
                    resized_image = original_image.resize((new_width, new_height), Image.LANCZOS)
                    self.img_control_general_resized = ImageTk.PhotoImage(resized_image)
                    self.canvas_controles_imagenes.create_image(
                        canvas_width / 2, canvas_height / 2,
                        image=self.img_control_general_resized,
                        anchor="center",
                        tags="control_image"
                    )
                except Exception as e:
                    print(f"Error al redimensionar icono_control.png: {e}")
                    self.canvas_controles_imagenes.create_image(
                        canvas_width / 2, canvas_height / 2,
                        image=self.img_control_general,
                        anchor="center",
                        tags="control_image"
                    )
            else:
                 self.canvas_controles_imagenes.create_image(
                    self.canvas_controles_imagenes.winfo_width()/2,
                    self.canvas_controles_imagenes.winfo_height()/2,
                    image=self.img_control_general,
                    anchor="center",
                    tags="control_image"
                )
        # FIN DE MODIFICACIÓN


    def crear_widgets_pc_temp(self, marco_padre):
        # INICIO DE MODIFICACIÓN PARA AGREGAR SCROLLBAR A "Control Manual de Sensores"
        canvas = tk.Canvas(marco_padre, bg="#333333", highlightthickness=0)
        scrollbar = ttk.Scrollbar(marco_padre, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner_frame = ttk.Frame(canvas, style="TFrame")
        canvas.create_window((0, 0), window=inner_frame, anchor="nw", tags="inner_frame_pc_temp")

        inner_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        # FIN DE MODIFICACIÓN

        inner_frame.grid_columnconfigure(2, weight=1)

        ttk.Label(inner_frame, text="Humedad:", style="TLabel").grid(row=0, column=1, sticky="w", padx=5, pady=2)
        self.deslizador_humedad_aire = ttk.Scale(inner_frame, from_=0, to=100, orient="horizontal", command=self.actualizar_lecturas_ambiente_desde_deslizadores, style="TScale")
        self.deslizador_humedad_aire.set(60)
        self.deslizador_humedad_aire.grid(row=0, column=2, sticky="ew", padx=5, pady=2)
        self.etiqueta_valor_humedad_aire = ttk.Label(inner_frame, text="60.0%", style="TLabel")
        self.etiqueta_valor_humedad_aire.grid(row=0, column=3, sticky="w", padx=5)

        ttk.Label(inner_frame, text="Temp (°C):", style="TLabel").grid(row=1, column=1, sticky="w", padx=5, pady=2)
        self.deslizador_temp = ttk.Scale(inner_frame, from_=0, to=50, orient="horizontal", command=self.actualizar_lecturas_ambiente_desde_deslizadores, style="TScale")
        self.deslizador_temp.set(25)
        self.deslizador_temp.grid(row=1, column=2, sticky="ew", padx=5, pady=2)
        self.etiqueta_valor_temp = ttk.Label(inner_frame, text="25.0°C", style="TLabel")
        self.etiqueta_valor_temp.grid(row=1, column=3, sticky="w", padx=5)

        ttk.Label(inner_frame, text="Luz:", style="TLabel").grid(row=2, column=1, sticky="w", padx=5, pady=2)
        self.deslizador_luz = ttk.Scale(inner_frame, from_=0, to=1023, orient="horizontal", command=self.actualizar_lecturas_ambiente_desde_deslizadores, style="TScale")
        self.deslizador_luz.set(500)
        self.deslizador_luz.grid(row=2, column=2, sticky="ew", padx=5, pady=2)
        self.etiqueta_valor_luz = ttk.Label(inner_frame, text="500", style="TLabel")
        self.etiqueta_valor_luz.grid(row=2, column=3, sticky="w", padx=5)

        ttk.Label(inner_frame, text="Suelo:", style="TLabel").grid(row=3, column=1, sticky="w", pady=2, padx=5)
        self.deslizador_humedad_suelo = ttk.Scale(inner_frame, from_=0, to=1023, orient="horizontal", command=self.actualizar_lecturas_ambiente_desde_deslizadores, style="TScale")
        self.deslizador_humedad_suelo.set(500)
        self.deslizador_humedad_suelo.grid(row=3, column=2, sticky="ew", padx=5, pady=2)
        self.etiqueta_valor_humedad_suelo = ttk.Label(inner_frame, text="500", style="TLabel")
        self.etiqueta_valor_humedad_suelo.grid(row=3, column=3, sticky="w", padx=5)


    def dibujar_icono_planta_pequena(self, lienzo):
        lienzo.delete("all")
        centro_x, centro_y = lienzo.winfo_width()/2, lienzo.winfo_height()/2
        # Maceta simple
        lienzo.create_rectangle(centro_x-20, centro_y+10, centro_x+20, centro_y+30, fill="brown", outline="black", tags="icono_planta")
        # Tallo
        lienzo.create_rectangle(centro_x-2, centro_y-10, centro_x+2, centro_y+10, fill="green", outline="green", tags="icono_planta")
        # Hojas
        lienzo.create_oval(centro_x-25, centro_y-25, centro_x+25, centro_y+5, fill="forestgreen", outline="darkgreen", tags="icono_planta")
        lienzo.create_oval(centro_x-15, centro_y-35, centro_x+15, centro_y-5, fill="forestgreen", outline="darkgreen", tags="icono_planta")

    def crear_widgets_grafico_agua(self, marco_padre):
        self.lienzo_grafico = tk.Canvas(marco_padre, bg="#263238", highlightthickness=0)
        self.lienzo_grafico.pack(expand=True, fill="both", padx=5, pady=5)
        self.lienzo_grafico.bind("<Configure>", lambda event: self.dibujar_grafico_agua())


    def dibujar_grafico_agua(self, evento=None):
        lienzo = self.lienzo_grafico
        if not lienzo or not lienzo.winfo_exists():
            return

        lienzo.delete("all")
        lienzo_ancho = lienzo.winfo_width()
        lienzo_alto = lienzo.winfo_height()

        if lienzo_ancho < 10 or lienzo_alto < 10:
            return

        datos = self.historial_agua
        if not datos:
            lienzo.create_text(lienzo_ancho/2, lienzo_alto/2, text="Sin datos de agua", fill="white", font=("Arial", 10))
            return

        num_barras = len(datos)
        if num_barras == 0: return

        margen_inferior = 20
        margen_superior = 10
        area_dibujo_alto = lienzo_alto - margen_inferior - margen_superior

        espacio_por_barra = lienzo_ancho / num_barras
        ancho_barra_real = espacio_por_barra * 0.7

        valor_max_dibujo = 100

        for i, valor in enumerate(datos):
            barra_x1 = (espacio_por_barra * i) + (espacio_por_barra * 0.15)
            barra_x2 = barra_x1 + ancho_barra_real

            altura_barra = (valor / valor_max_dibujo) * area_dibujo_alto
            barra_y1 = lienzo_alto - margen_inferior - altura_barra
            barra_y2 = lienzo_alto - margen_inferior

            lienzo.create_rectangle(barra_x1, barra_y1, barra_x2, barra_y2, fill="#00BCD4", outline="#0097A7", tags="barra")
            lienzo.create_text(barra_x1 + (barra_x2 - barra_x1) / 2, barra_y1 - 5, text=str(valor), fill="white", font=("Arial", 8))

        valor_linea = 10
        if valor_linea <= valor_max_dibujo:
            linea_y = lienzo_alto - margen_inferior - ((valor_linea / valor_max_dibujo) * area_dibujo_alto)
            lienzo.create_line(5, linea_y, lienzo_ancho - 5, linea_y, fill="white", dash=(2,2))
            lienzo.create_text(lienzo_ancho - 15, linea_y - 5, text=str(valor_linea), fill="white", font=("Arial", 8))

    def crear_widgets_notificacion(self, marco_padre):
        marco_padre.grid_rowconfigure(0, weight=1)
        marco_padre.grid_rowconfigure(1, weight=0)
        marco_padre.grid_rowconfigure(2, weight=0)
        marco_padre.grid_rowconfigure(3, weight=0)

        self.etiqueta_notificacion = ttk.Label(marco_padre, text="Estado: Todo bien en el invernadero.", style="TLabel", wraplength=200, justify="left")
        self.etiqueta_notificacion.pack(expand=True, fill="both", padx=5, pady=5)

        self.etiqueta_esp32_wifi_status = ttk.Label(marco_padre, text=f"ESP32 WiFi: {self.esp32_wifi_status}", style="TLabel", wraplength=200, justify="left")
        self.etiqueta_esp32_wifi_status.pack(padx=5, pady=2, fill="x")

        self.canvas_notificaciones_imagenes = tk.Canvas(marco_padre, bg="#333333", highlightthickness=0, height=50)
        self.canvas_notificaciones_imagenes.pack(padx=5, pady=5, fill="x")

        if self.img_notificacion:
            self.canvas_notificaciones_imagenes.create_image(
                self.canvas_notificaciones_imagenes.winfo_width()/2,
                self.canvas_notificaciones_imagenes.winfo_height()/2,
                image=self.img_notificacion,
                anchor="center",
                tags="notification_image"
            )

        marco_noti = ttk.Frame(marco_padre, style="TFrame")
        marco_noti.pack(padx=5, pady=5, fill="x")

        self.lienzo_estado_alerta_led = tk.Canvas(marco_noti, width=30, height=30, bg="#333333", highlightthickness=0)
        self.lienzo_estado_alerta_led.pack(side="left", padx=2)
        self.dibujar_indicador_alerta_led()

        lienzo_barra = tk.Canvas(marco_noti, width=100, height=15, bg="#424242", highlightthickness=0)
        lienzo_barra.pack(side="left", padx=2, fill="x", expand=True)
        lienzo_barra.create_rectangle(0, 0, 70, 15, fill="#4CAF50", outline="")

        ttk.Label(marco_padre, text="Songp", style="TLabel").pack(pady=5)

    def dibujar_pokeball(self, lienzo, color):
        lienzo.delete("all")
        lienzo.create_oval(5, 5, 35, 35, fill=color, outline="black", width=2)
        lienzo.create_rectangle(5, 18, 35, 22, fill="black", outline="black")
        lienzo.create_oval(15, 15, 25, 25, fill="white", outline="black", width=1)
        lienzo.create_oval(18, 18, 22, 22, fill="black", outline="black", width=1)

    def dibujar_indicador_alerta_led(self):
        """Dibuja el indicador del LED de alerta basado en self.alerta_led_activo."""
        color = "red" if self.alerta_led_activo else "#4CAF50"
        self.dibujar_pokeball(self.lienzo_estado_alerta_led, color)


    def crear_widgets_nivel_notificador(self, marco_padre):
        # INICIO DE MODIFICACIÓN PARA AGREGAR SCROLLBAR A "Control de Emergencia"
        canvas = tk.Canvas(marco_padre, bg="#333333", highlightthickness=0)
        scrollbar = ttk.Scrollbar(marco_padre, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner_frame = ttk.Frame(canvas, style="TFrame")
        canvas.create_window((0, 0), window=inner_frame, anchor="nw", tags="inner_frame_emergencia")

        inner_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        # FIN DE MODIFICACIÓN

        inner_frame.grid_rowconfigure(0, weight=1)

        marco_control_led = ttk.LabelFrame(inner_frame, text="Control LED Alerta", padding="5", style="TLabelframe")
        marco_control_led.pack(pady=5, fill="both", expand=True, padx=5)

        marco_control_led.grid_columnconfigure(0, weight=1)
        marco_control_led.grid_columnconfigure(1, weight=1)

        self.boton_led_encender = ttk.Button(marco_control_led, text="LED ON", command=lambda: self.alternar_luz_led("ON"), style="TButton")
        self.boton_led_encender.grid(row=0, column=0, padx=5, pady=5, sticky="ew")

        self.boton_led_apagar = ttk.Button(marco_control_led, text="LED OFF", command=lambda: self.alternar_luz_led("OFF"), style="TButton")
        self.boton_led_apagar.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        self.boton_config_wifi = ttk.Button(inner_frame, text="Configurar WiFi ESP32 (MQTT)", command=self.enviar_credenciales_wifi, style="TButton")
        self.boton_config_wifi.pack(pady=10, fill="x", padx=5)

        self.boton_config_wifi_http = ttk.Button(inner_frame, text="Configurar WiFi ESP32 (HTTP)", command=self.enviar_credenciales_wifi_http, style="TButton")
        self.boton_config_wifi_http.pack(pady=5, fill="x", padx=5)


    def crear_widgets_lecturas_sensores_bottom(self, marco_padre):
        """
        Nuevo método para crear el panel de Lecturas Actuales de Sensores
        en la parte inferior derecha.
        """
        marco_padre.grid_rowconfigure(0, weight=1)
        marco_padre.grid_rowconfigure(1, weight=1)
        marco_padre.grid_rowconfigure(2, weight=1)
        marco_padre.grid_rowconfigure(3, weight=1)
        marco_padre.grid_rowconfigure(4, weight=0)

        self.etiqueta_sens_temp = ttk.Label(marco_padre, text="Temp: N/A", style="TLabel")
        self.etiqueta_sens_temp.pack(anchor="w", padx=5, pady=2, fill="x")

        self.etiqueta_sens_hum_aire = ttk.Label(marco_padre, text="Hum Aire: N/A", style="TLabel")
        self.etiqueta_sens_hum_aire.pack(anchor="w", padx=5, pady=2, fill="x")

        self.etiqueta_sens_hum_suelo = ttk.Label(marco_padre, text="Hum Suelo: N/A", style="TLabel")
        self.etiqueta_sens_hum_suelo.pack(anchor="w", padx=5, pady=2, fill="x")

        self.etiqueta_sens_luz = ttk.Label(marco_padre, text="Luz: N/A", style="TLabel")
        self.etiqueta_sens_luz.pack(anchor="w", padx=5, pady=2, fill="x")

        self.canvas_sensores_imagenes = tk.Canvas(marco_padre, bg="#263238", highlightthickness=0, height=50)
        self.canvas_sensores_imagenes.pack(padx=5, pady=5, fill="x", side="bottom")

        if self.img_temp_sensor:
            self.canvas_sensores_imagenes.bind("<Configure>",
                lambda event, img=self.img_temp_sensor, tag="sensor_image_temp", filename="temp.png":
                    self.redimensionar_y_dibujar_imagen(event, img, tag, filename)
            )
            self.canvas_sensores_imagenes.create_image(
                self.canvas_sensores_imagenes.winfo_width()/2,
                self.canvas_sensores_imagenes.winfo_height()/2,
                image=self.img_temp_sensor,
                anchor="center",
                tags="sensor_image_temp"
            )


    def crear_widgets_info_planta(self, marco_padre):
        """
        Crea y organiza todas las etiquetas de información de la planta
        en un solo marco, mostrando solo la información relevante.
        """
        marco_padre.grid_columnconfigure(1, weight=1)

        info_planta_a_mostrar = {
            "Nombre": "Nombre",
            "Salud": "Salud",
            "Crecimiento (Altura)": "Altura",
            "Etapa de Crecimiento": "Etapa de Crecimiento",
            "Edad (Días)": "Edad_Dias",
            "Edad (Años)": "Edad_Anios",
            "Tipo de Planta": "Tipo_Planta"
        }

        row_idx = 0
        for label_text, key in info_planta_a_mostrar.items():
            ttk.Label(marco_padre, text=f"{label_text}:", style="TLabel").grid(row=row_idx, column=0, sticky="w", padx=5, pady=2)
            etiqueta_valor = ttk.Label(marco_padre, text="N/A", style="TLabel")
            etiqueta_valor.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=2)
            self.etiquetas_planta_info[key] = etiqueta_valor
            row_idx += 1

        self.canvas_info_planta_imagenes = tk.Canvas(marco_padre, bg="#263238", highlightthickness=0, height=50)
        self.canvas_info_planta_imagenes.grid(row=row_idx, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")

        if self.img_coronaplanta:
            self.canvas_info_planta_imagenes.bind("<Configure>",
                lambda event, img=self.img_coronaplanta, tag="info_planta_image", filename="coronaplanta.png":
                    self.redimensionar_y_dibujar_imagen(event, img, tag, filename)
            )
            self.canvas_info_planta_imagenes.create_image(
                self.canvas_info_planta_imagenes.winfo_width()/2,
                self.canvas_info_planta_imagenes.winfo_height()/2,
                image=self.img_coronaplanta,
                anchor="center",
                tags="info_planta_image"
            )

    def redimensionar_y_dibujar_imagen(self, event, tk_image, tag, filename):
        """
        Redimensiona una imagen y la dibuja en un canvas.
        tk_image: la ImageTk.PhotoImage original (no la PIL.Image)
        tag: la etiqueta para el elemento del canvas
        filename: el nombre del archivo original para reabrirlo con PIL
        """
        canvas = event.widget
        canvas.delete(tag)

        canvas_width = canvas.winfo_width()
        canvas_height = canvas.winfo_height()

        if canvas_width < 1 or canvas_height < 1:
            return

        try:
            original_image = Image.open(filename)
            img_width, img_height = original_image.size

            ratio_w = canvas_width / img_width
            ratio_h = canvas_height / img_height
            scale_factor = min(ratio_w, ratio_h)

            new_width = int(img_width * scale_factor)
            new_height = int(img_height * scale_factor)

            resized_image = original_image.resize((new_width, new_height), Image.LANCZOS)
            setattr(canvas, f"_resized_image_{tag}", ImageTk.PhotoImage(resized_image))

            canvas.create_image(
                canvas_width / 2, canvas_height / 2,
                image=getattr(canvas, f"_resized_image_{tag}"),
                anchor="center",
                tags=tag
            )
        except Exception as e:
            print(f"Error al redimensionar y dibujar {filename}: {e}")
            canvas.create_image(
                canvas_width / 2, canvas_height / 2,
                image=tk_image,
                anchor="center",
                tags=tag
            )


    def crear_widgets_grafico_altura(self, marco_padre):
        self.lienzo_grafico_altura = tk.Canvas(marco_padre, bg="#263238", highlightthickness=0)
        self.lienzo_grafico_altura.pack(expand=True, fill="both", padx=5, pady=5)
        self.lienzo_grafico_altura.bind("<Configure>", lambda event: self.dibujar_grafico_altura())

    def dibujar_grafico_altura(self):
        lienzo = self.lienzo_grafico_altura
        if not lienzo or not lienzo.winfo_exists():
            return

        lienzo.delete("all")
        lienzo_ancho = lienzo.winfo_width()
        lienzo_alto = lienzo.winfo_height()

        if lienzo_ancho < 50 or lienzo_alto < 50: return

        margen_x = 40
        margen_y = 30

        area_dibujo_ancho = lienzo_ancho - 2 * margen_x
        area_dibujo_alto = lienzo_alto - 2 * margen_y

        lienzo.create_line(margen_x, margen_y + area_dibujo_alto, margen_x + area_dibujo_ancho, margen_y + area_dibujo_alto, fill="white", width=1)
        lienzo.create_line(margen_x, margen_y + area_dibujo_alto, margen_x, margen_y, fill="white", width=1)

        lienzo.create_text(margen_x + area_dibujo_ancho / 2, lienzo_alto - margen_y / 2, text="Tiempo (Días)", fill="white", font=("Arial", 9))
        lienzo.create_text(margen_x / 2, margen_y + area_dibujo_alto / 2, text="Altura (cm)", fill="white", font=("Arial", 9), angle=90)

        if not self.historial_altura:
            return

        max_altura = max(self.historial_altura) if self.historial_altura else self.planta.altura_maxima_cm
        max_tiempo = max(self.historial_tiempo_dias) if self.historial_tiempo_dias else 1

        max_altura_escala = max(max_altura, self.planta.altura_maxima_cm * 0.2)
        if max_altura_escala == 0: max_altura_escala = 1

        if max_tiempo == 0: max_tiempo = 1

        puntos_grafico = []
        for i in range(len(self.historial_altura)):
            x = margen_x + (self.historial_tiempo_dias[i] / max_tiempo) * area_dibujo_ancho
            y = (margen_y + area_dibujo_alto) - (self.historial_altura[i] / max_altura_escala) * area_dibujo_alto
            puntos_grafico.append((x, y))

        if len(puntos_grafico) > 1:
            lienzo.create_line(puntos_grafico, fill="#4CAF50", width=2, tags="linea_altura")

            ultimo_x, ultimo_y = puntos_grafico[-1]
            lienzo.create_oval(ultimo_x - 3, ultimo_y - 3, ultimo_x + 3, ultimo_y + 3, fill="#FFC107", outline="white")
            lienzo.create_text(ultimo_x, ultimo_y - 10, text=f"{self.historial_altura[-1]:.1f} cm", fill="white", font=("Arial", 8))

        num_marcas_y = 5
        for i in range(num_marcas_y + 1):
            altura_valor = (i / num_marcas_y) * max_altura_escala
            y_pos = (margen_y + area_dibujo_alto) - (altura_valor / max_altura_escala) * area_dibujo_alto
            lienzo.create_line(margen_x - 5, y_pos, margen_x, y_pos, fill="white")
            lienzo.create_text(margen_x - 10, y_pos, text=f"{altura_valor:.0f}", anchor="e", fill="white", font=("Arial", 8))

        num_marcas_x = 5
        if max_tiempo > 0:
            for i in range(num_marcas_x + 1):
                tiempo_valor = (i / num_marcas_x) * max_tiempo
                x_pos = margen_x + (tiempo_valor / max_tiempo) * area_dibujo_ancho
                lienzo.create_line(x_pos, margen_y + area_dibujo_alto, x_pos, margen_y + area_dibujo_alto + 5, fill="white")
                lienzo.create_text(x_pos, margen_y + area_dibujo_alto + 15, text=f"{tiempo_valor:.0f}", anchor="n", fill="white", font=("Arial", 8))

    def actualizar_lecturas_ambiente_desde_deslizadores(self, evento=None):
        """Actualiza las lecturas simuladas desde los deslizadores y las muestra.
        Estas lecturas serán sobrescritas por MQTT si el ESP32 está enviando datos.
        """
        global lecturas_actuales
        lecturas_actuales["temperatura"] = self.deslizador_temp.get()
        lecturas_actuales["humedad_aire"] = self.deslizador_humedad_aire.get()
        lecturas_actuales["luz"] = self.deslizador_luz.get()
        lecturas_actuales["humedad_suelo"] = self.deslizador_humedad_suelo.get()

        self.etiqueta_valor_temp.config(text=f"{lecturas_actuales['temperatura']:.1f}°C")
        self.etiqueta_valor_humedad_aire.config(text=f"{lecturas_actuales['humedad_aire']:.1f}%")
        self.etiqueta_valor_luz.config(text=f"{lecturas_actuales['luz']:.0f}")
        self.etiqueta_valor_humedad_suelo.config(text=f"{lecturas_actuales['humedad_suelo']:.0f}")

    def alternar_luz_led(self, comando):
        """
        Envía un comando MQTT al ESP32 para controlar el LED de alerta.
        comando: "ON" o "OFF"
        """
        topic = "invernadero/control_led_alerta"
        self.cliente_mqtt_gui.publish(topic, comando)
        print(f"Comando LED enviado a ESP32: {comando}")

    def controlar_bomba(self, comando):
        """
        Envía un comando MQTT al ESP32 para controlar la bomba de agua.
        comando: "ON" o "OFF"
        """
        topic = "invernadero/control_bomba"
        self.cliente_mqtt_gui.publish(topic, comando)
        print(f"Comando Bomba enviado a ESP32: {comando}")

    def toggle_riego_automatico_sensor(self):
        """
        Activa o desactiva el riego automático basado en el sensor de humedad del suelo.
        """
        self.riego_automatico_activo = not self.riego_automatico_activo
        # --- CAMBIO: Publicar el comando al ESP32 ---
        topic = "invernadero/control_riego_auto_sensor"
        if self.riego_automatico_activo:
            self.boton_riego_auto_sensor.config(text="Desactivar Riego Auto (Sensor)", style="TButton")
            messagebox.showinfo("Riego Automático", "Riego automático por sensor ACTIVADO.")
            self.cliente_mqtt_gui.publish(topic, "ON")
        else:
            self.boton_riego_auto_sensor.config(text="Activar Riego Auto (Sensor)", style="TButton")
            messagebox.showinfo("Riego Automático", "Riego automático por sensor DESACTIVADO.")
            self.cliente_mqtt_gui.publish(topic, "OFF")
        # --- FIN CAMBIO ---


    def enviar_credenciales_wifi(self):
        """
        Solicita al usuario el SSID y la contraseña y los envía al ESP32 por MQTT.
        """
        ssid = simpledialog.askstring("Configurar WiFi ESP32 (MQTT)", "Introduce el SSID de la red WiFi:")
        if not ssid:
            messagebox.showwarning("Configuración WiFi", "SSID no puede estar vacío.")
            return

        password = simpledialog.askstring("Configurar WiFi ESP32 (MQTT)", "Introduce la contraseña de la red WiFi (dejar vacío si no tiene):")

        payload = json.dumps({"ssid": ssid, "password": password})
        topic = "invernadero/config/wifi"

        try:
            self.cliente_mqtt_gui.publish(topic, payload)
            print(f"Enviando credenciales WiFi a ESP32 (MQTT): {payload}")
            messagebox.showinfo("Configuración WiFi", "Credenciales enviadas por MQTT. El ESP32 intentará conectar a la nueva red. Por favor, espera unos segundos y observa el estado del ESP32.")
            self.esp32_wifi_status = "Enviando credenciales (MQTT)..."
            self.actualizar_estado_wifi_esp32_gui()
        except Exception as e:
            messagebox.showerror("Error MQTT", f"No se pudo enviar el mensaje MQTT: {e}")
            print(f"Error al enviar credenciales WiFi por MQTT: {e}")

    def enviar_credenciales_wifi_http(self):
        """
        Solicita al usuario la IP del ESP32, SSID y contraseña, y los envía
        al ESP32 a través de una solicitud HTTP POST.
        Esto es útil cuando el ESP32 está en modo AP y no puede conectar a MQTT.
        """
        esp32_ip = simpledialog.askstring("Configurar WiFi ESP32 (HTTP)", "Introduce la IP del ESP32 (ej. 192.168.4.1):")
        if not esp32_ip:
            messagebox.showwarning("Configuración WiFi", "La IP del ESP32 no puede estar vacía.")
            return

        ssid = simpledialog.askstring("Configurar WiFi ESP32 (HTTP)", "Introduce el SSID de la red WiFi:")
        if not ssid:
            messagebox.showwarning("Configuración WiFi", "SSID no puede estar vacío.")
            return

        password = simpledialog.askstring("Configurar WiFi ESP32 (HTTP)", "Introduce la contraseña de la red WiFi (dejar vacío si no tiene):")

        url = f"http://{esp32_ip}/savewifi"
        data = {"ssid": ssid, "password": password}

        try:
            response = requests.post(url, data=data, timeout=5)
            if response.status_code == 200:
                messagebox.showinfo("Configuración WiFi (HTTP)", "Credenciales enviadas por HTTP. El ESP32 debería reiniciarse y conectar a la nueva red.")
                self.esp32_wifi_status = "Credenciales enviadas (HTTP). Reiniciando ESP32..."
                self.actualizar_estado_wifi_esp32_gui()
            else:
                messagebox.showerror("Error HTTP", f"Fallo al enviar credenciales por HTTP. Código de estado: {response.status_code}\nRespuesta: {response.text}")
                self.esp32_wifi_status = f"Fallo HTTP ({response.status_code})"
                self.actualizar_estado_wifi_esp32_gui()
        except requests.exceptions.ConnectionError:
            messagebox.showerror("Error de Conexión", f"No se pudo conectar al ESP32 en {esp32_ip}. Asegúrate de que el ESP32 esté en modo AP y tu PC esté conectado a su red AP.")
            self.esp32_wifi_status = "Error de conexión HTTP"
            self.actualizar_estado_wifi_esp32_gui()
        except requests.exceptions.Timeout:
            messagebox.showerror("Tiempo de Espera Agotado", f"Tiempo de espera agotado al intentar conectar a {esp32_ip}. Asegúrate de que la IP sea correcta y el ESP32 esté activo.")
            self.esp32_wifi_status = "Tiempo de espera HTTP agotado"
            self.actualizar_estado_wifi_esp32_gui()
        except Exception as e:
            messagebox.showerror("Error Inesperado", f"Ocurrió un error inesperado: {e}")
            self.esp32_wifi_status = f"Error HTTP inesperado: {e}"
            self.actualizar_estado_wifi_esp32_gui()


    def actualizar_estado_wifi_esp32_gui(self):
        """
        Actualiza la etiqueta en la GUI con el estado de conexión WiFi del ESP32.
        Cambia el color del texto según el estado.
        """
        if self.etiqueta_esp32_wifi_status:
            self.etiqueta_esp32_wifi_status.config(text=f"ESP32 WiFi: {self.esp32_wifi_status}")
            if "CONECTADO OK" in self.esp32_wifi_status:
                self.etiqueta_esp32_wifi_status.config(foreground="#4CAF50")
            elif "FALLO" in self.esp32_wifi_status or "NO CREDENCIALES" in self.esp32_wifi_status:
                self.etiqueta_esp32_wifi_status.config(foreground="#FF5722")
            elif "MODO CONFIGURACION AP" in self.esp32_wifi_status:
                self.etiqueta_esp32_wifi_status.config(foreground="#FFC107")
            else:
                self.etiqueta_esp32_wifi_status.config(foreground="white")

    def avanzar_dias_simulacion(self):
        """Avanza la edad de la planta en un número específico de días."""
        try:
            dias_a_sumar = float(self.entrada_dias_avanzar.get())
            if dias_a_sumar > 0:
                simular_crecimiento(self.planta, dias_a_sumar, lecturas_actuales)
                self.actualizar_gui(manual_advance=True)
            else:
                print("Por favor, introduce un número positivo de días.")
        except ValueError:
            print("Entrada inválida para días a avanzar. Por favor, introduce un número.")

    def reiniciar_planta(self):
        """Reinicia la planta a su estado inicial."""
        self.planta = Planta(nombre="Corona de Cristo", etapa_crecimiento="Semilla", edad_dias=0, altura_cm=0.5)
        self.ultima_actualizacion_tiempo = datetime.datetime.now()
        self.deslizador_temp.set(25)
        self.deslizador_humedad_aire.set(60)
        self.deslizador_luz.set(500)
        self.deslizador_humedad_suelo.set(500)
        self.historial_altura = []
        self.historial_tiempo_dias = []
        self.historial_agua = []
        self.nivel_tanque_agua = 100
        self.bomba_activa = False
        self.alerta_led_activo = False
        self.riego_automatico_activo = False # Reiniciar también el estado del riego automático
        self.boton_riego_auto_sensor.config(text="Activar Riego Auto (Sensor)", style="TButton") # Actualizar texto del botón
        self.actualizar_lecturas_ambiente_desde_deslizadores()
        self.actualizar_gui()

    def crear_widgets_programacion_riego(self, marco_padre):
        marco_padre.grid_columnconfigure(0, weight=1)
        marco_padre.grid_rowconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(marco_padre, text="Horarios de Riego Programados", padding="10", style="TLabelframe")
        list_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        self.schedule_listbox = tk.Listbox(list_frame, height=10, bg="#333333", fg="white", selectbackground="#424242")
        self.schedule_listbox.grid(row=0, column=0, sticky="nsew")
        schedule_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.schedule_listbox.yview)
        schedule_scrollbar.grid(row=0, column=1, sticky="ns")
        self.schedule_listbox.config(yscrollcommand=schedule_scrollbar.set)

        ttk.Button(list_frame, text="Eliminar Horario Seleccionado", command=self.eliminar_horario_riego, style="TButton").grid(row=1, column=0, columnspan=2, pady=5)

        add_frame = ttk.LabelFrame(marco_padre, text="Añadir Nuevo Horario de Riego", padding="10", style="TLabelframe")
        add_frame.grid(row=1, column=0, padx=10, pady=10, sticky="ew")

        ttk.Label(add_frame, text="Día de la Semana:", style="TLabel").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.day_combobox = ttk.Combobox(add_frame, values=["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo", "Todos los días"])
        self.day_combobox.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        self.day_combobox.set("Todos los días")

        ttk.Label(add_frame, text="Hora (HH:MM):", style="TLabel").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.time_entry = ttk.Entry(add_frame)
        self.time_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        self.time_entry.insert(0, "08:00")

        ttk.Label(add_frame, text="Duración (minutos):", style="TLabel").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.duration_entry = ttk.Entry(add_frame)
        self.duration_entry.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        self.duration_entry.insert(0, "10")

        ttk.Button(add_frame, text="Añadir Horario", command=self.añadir_horario_riego, style="TButton").grid(row=3, column=0, columnspan=2, pady=10)

        self.actualizar_lista_horarios()

    def añadir_horario_riego(self):
        day = self.day_combobox.get()
        time_str = self.time_entry.get()
        duration_str = self.duration_entry.get()

        try:
            hour, minute = map(int, time_str.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Hora inválida")
            duration = int(duration_str)
            if not (1 <= duration <= 60):
                raise ValueError("Duración inválida (1-60 min)")
        except ValueError as e:
            messagebox.showerror("Error de Entrada", f"Formato de hora o duración inválido: {e}")
            return

        new_schedule = {"day": day, "time": time_str, "duration": duration}
        global irrigation_schedule
        irrigation_schedule.append(new_schedule)
        save_schedule()
        self.actualizar_lista_horarios()
        messagebox.showinfo("Horario Añadido", "Horario de riego añadido exitosamente.")

    def eliminar_horario_riego(self):
        selected_indices = self.schedule_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Eliminar Horario", "Por favor, selecciona un horario para eliminar.")
            return

        for index in selected_indices[::-1]:
            global irrigation_schedule
            del irrigation_schedule[index]
        save_schedule()
        self.actualizar_lista_horarios()
        messagebox.showinfo("Horario Eliminado", "Horario(s) de riego eliminado(s) exitosamente.")

    def actualizar_lista_horarios(self):
        self.schedule_listbox.delete(0, tk.END)
        for i, schedule in enumerate(irrigation_schedule):
            self.schedule_listbox.insert(tk.END, f"{i+1}. {schedule['day']} a las {schedule['time']} por {schedule['duration']} min")

    def verificar_riego_programado(self):
        """
        Función que se ejecuta en un hilo separado para verificar los horarios de riego.
        """
        last_check_minute = -1
        while True:
            now = datetime.datetime.now()
            current_day = now.strftime("%A")
            current_time_str = now.strftime("%H:%M")
            current_minute = now.minute

            if current_minute == last_check_minute:
                time.sleep(10)
                continue
            last_check_minute = current_minute

            for schedule in irrigation_schedule:
                schedule_day = schedule["day"]
                schedule_time = schedule["time"]
                schedule_duration = schedule["duration"]

                if (schedule_day == "Todos los días" or schedule_day == current_day) and schedule_time == current_time_str:
                    print(f"¡Es hora de regar! Día: {current_day}, Hora: {current_time_str}, Duración: {schedule_duration} min")
                    self.after(0, lambda: self.ejecutar_riego(schedule_duration))
                    time.sleep(schedule_duration * 60 + 5)
                    last_check_minute = -1
                    break

            time.sleep(30)

    def ejecutar_riego(self, duracion_minutos):
        """
        Envía el comando MQTT para encender la bomba y luego apagarla después de la duración.
        """
        messagebox.showinfo("Riego Automático", f"Iniciando riego automático por {duracion_minutos} minutos.")
        self.controlar_bomba("ON")
        self.after(duracion_minutos * 60 * 1000, lambda: self.controlar_bomba("OFF"))


    def crear_widgets_rangos_sensores(self, marco_padre):
        marco_padre.grid_columnconfigure(0, weight=1)
        marco_padre.grid_columnconfigure(1, weight=1)
        marco_padre.grid_columnconfigure(2, weight=1)
        marco_padre.grid_columnconfigure(3, weight=1)

        self.sensor_entries = {}

        row_idx = 0
        for sensor_name, ranges in sensor_ranges.items():
            ttk.Label(marco_padre, text=f"{sensor_name.capitalize()}:", style="TLabel", font=("Arial", 10, "bold")).grid(row=row_idx, column=0, columnspan=4, sticky="w", padx=5, pady=5)
            row_idx += 1

            ttk.Label(marco_padre, text="Ideal Min:", style="TLabel").grid(row=row_idx, column=0, sticky="w", padx=5, pady=2)
            entry_ideal_min = ttk.Entry(marco_padre, width=10)
            entry_ideal_min.insert(0, str(ranges["ideal_min"]))
            entry_ideal_min.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=2)

            ttk.Label(marco_padre, text="Ideal Max:", style="TLabel").grid(row=row_idx, column=2, sticky="w", padx=5, pady=2)
            entry_ideal_max = ttk.Entry(marco_padre, width=10)
            entry_ideal_max.insert(0, str(ranges["ideal_max"]))
            entry_ideal_max.grid(row=row_idx, column=3, sticky="ew", padx=5, pady=2)
            row_idx += 1

            ttk.Label(marco_padre, text="Letal Min:", style="TLabel").grid(row=row_idx, column=0, sticky="w", padx=5, pady=2)
            entry_letal_min = ttk.Entry(marco_padre, width=10)
            entry_letal_min.insert(0, str(ranges["letal_min"]))
            entry_letal_min.grid(row=row_idx, column=1, sticky="ew", padx=5, pady=2)

            ttk.Label(marco_padre, text="Letal Max:", style="TLabel").grid(row=row_idx, column=2, sticky="w", padx=5, pady=2)
            entry_letal_max = ttk.Entry(marco_padre, width=10)
            entry_letal_max.insert(0, str(ranges["letal_max"]))
            entry_letal_max.grid(row=row_idx, column=3, sticky="ew", padx=5, pady=2)
            row_idx += 1

            self.sensor_entries[sensor_name] = {
                "ideal_min": entry_ideal_min,
                "ideal_max": entry_ideal_max,
                "letal_min": entry_letal_min,
                "letal_max": entry_letal_max
            }
            ttk.Separator(marco_padre, orient="horizontal").grid(row=row_idx, column=0, columnspan=4, sticky="ew", pady=10)
            row_idx += 1

        ttk.Button(marco_padre, text="Guardar Rangos", command=self.guardar_nuevos_rangos, style="TButton").grid(row=row_idx, column=0, columnspan=4, pady=10)

    def guardar_nuevos_rangos(self):
        global sensor_ranges
        new_ranges = {}
        try:
            for sensor_name, entries in self.sensor_entries.items():
                new_ranges[sensor_name] = {
                    "ideal_min": float(entries["ideal_min"].get()),
                    "ideal_max": float(entries["ideal_max"].get()),
                    "letal_min": float(entries["letal_min"].get()),
                    "letal_max": float(entries["letal_max"].get())
                }
                if not (new_ranges[sensor_name]["letal_min"] <= new_ranges[sensor_name]["ideal_min"] <= new_ranges[sensor_name]["ideal_max"] <= new_ranges[sensor_name]["letal_max"]):
                    raise ValueError(f"Rangos inválidos para {sensor_name}. Asegúrate de que letal_min <= ideal_min <= ideal_max <= letal_max.")

            sensor_ranges.update(new_ranges)
            save_config()
            messagebox.showinfo("Rangos Guardados", "Los rangos de los sensores se han guardado exitosamente.")
        except ValueError as e:
            messagebox.showerror("Error de Validación", f"Error al guardar rangos: {e}. Asegúrate de que los valores sean numéricos y los rangos sean lógicos.")
        except Exception as e:
            messagebox.showerror("Error", f"Ocurrió un error inesperado al guardar los rangos: {e}")

    def actualizar_gui(self, manual_advance=False):
        """
        Actualiza los elementos de la GUI basándose en las lecturas actuales y el estado de la planta.
        Se llama periódicamente por el método after de Tkinter.
        """
        global lecturas_actuales
        ahora = datetime.datetime.now()

        if not manual_advance:
            dias_simulados_por_paso = 0.1
            simular_crecimiento(self.planta, dias_simulados_por_paso, lecturas_actuales)

        for clave, valor_defecto in {"temperatura": 25.0, "humedad_aire": 60.0, "humedad_suelo": 500, "luz": 500}.items():
            if lecturas_actuales.get(clave) is None:
                lecturas_actuales[clave] = valor_defecto

        self.historial_altura.append(self.planta.altura_cm)
        self.historial_tiempo_dias.append(self.planta.edad_dias)

        if len(self.historial_altura) > self.max_puntos_grafico:
            self.historial_altura.pop(0)
            self.historial_tiempo_dias.pop(0)
            tiempo_offset = self.historial_tiempo_dias[0]
            self.historial_tiempo_dias = [t - tiempo_offset for t in self.historial_tiempo_dias]

        self.historial_agua.append(self.nivel_tanque_agua)
        if len(self.historial_agua) > self.max_historial_agua:
            self.historial_agua.pop(0)

        self.etiquetas_planta_info["Nombre"].config(text=f"{self.planta.nombre}")
        self.etiquetas_planta_info["Salud"].config(text=f"{self.planta.salud:.1f}%")
        self.etiquetas_planta_info["Altura"].config(text=f"{self.planta.altura_cm:.1f} cm")
        self.etiquetas_planta_info["Etapa de Crecimiento"].config(text=self.planta.etapa_crecimiento)
        self.etiquetas_planta_info["Edad_Dias"].config(text=f"{self.planta.edad_dias:.2f} días")
        self.etiquetas_planta_info["Edad_Anios"].config(text=f"{self.planta.edad_dias / 365.25:.2f} años")
        self.etiquetas_planta_info["Tipo_Planta"].config(text=f"{self.planta.tipo_planta}")

        # Lógica de Notificaciones de la GUI (coherente con ESP32)
        notificaciones = []
        temp = lecturas_actuales.get("temperatura")
        hum_air = lecturas_actuales.get("humedad_aire")
        hum_soil = lecturas_actuales.get("humedad_suelo")
        luz = lecturas_actuales.get("luz")

        # Usar los rangos globales para las notificaciones
        temp_alert_min = sensor_ranges["temperatura"]["letal_min"]
        temp_alert_max = sensor_ranges["temperatura"]["letal_max"]
        hum_air_alert_min = sensor_ranges["humedad_aire"]["letal_min"]
        hum_air_alert_max = sensor_ranges["humedad_aire"]["letal_max"]
        hum_soil_alert_min = sensor_ranges["humedad_suelo"]["letal_min"]
        hum_soil_alert_max = sensor_ranges["humedad_suelo"]["letal_max"]
        luz_alert_min = sensor_ranges["luz"]["letal_min"]
        luz_alert_max = sensor_ranges["luz"]["letal_max"]

        if temp is not None and (temp < temp_alert_min or temp > temp_alert_max):
            notificaciones.append("Temperatura fuera de rango!")
        if hum_air is not None and (hum_air < hum_air_alert_min or hum_air > hum_air_alert_max):
            notificaciones.append("Humedad del aire incorrecta!")
        if hum_soil is not None and (hum_soil < hum_soil_alert_min or hum_soil > hum_soil_alert_max):
            notificaciones.append("Humedad del suelo crítica!")
        if luz is not None and (luz < luz_alert_min or luz > luz_alert_max):
            notificaciones.append("Nivel de luz inadecuado!")

        # Lógica de riego automático por sensor (solo para visualización en GUI, el control lo hace el ESP32)
        # La GUI solo publica el comando de activar/desactivar, el ESP32 es el que decide cuándo encender/apagar la bomba
        # basándose en el sensor y el estado de automaticIrrigationEnabled.
        # La GUI recibe el estado real de la bomba y del riego automático del ESP32.
        if self.riego_automatico_activo:
            if hum_soil is not None:
                # Aquí la GUI solo muestra lo que el ESP32 debería estar haciendo o ya hizo
                if hum_soil < sensor_ranges["humedad_suelo"]["letal_min"] and not self.bomba_activa:
                    notificaciones.append("Riego automático (ESP32): Humedad del suelo baja, bomba debería estar ON.")
                elif hum_soil >= sensor_ranges["humedad_suelo"]["ideal_max"] and self.bomba_activa:
                    notificaciones.append("Riego automático (ESP32): Humedad del suelo óptima, bomba debería estar OFF.")
            else:
                notificaciones.append("Riego automático (ESP32): No hay datos de humedad del suelo.")


        if self.planta.esta_muerta:
            notificaciones = ["¡Advertencia: La planta ha muerto! 💀"]
            self.etiqueta_notificacion.config(foreground="darkgrey")
        elif not notificaciones:
            self.etiqueta_notificacion.config(text="Estado: Todo bien en el invernadero.", foreground="white")
        else:
            self.etiqueta_notificacion.config(text="\n".join(["¡PROBLEMA!"] + notificaciones), foreground="red")

        print(f"DEBUG: Actualizando etiquetas visuales con: {lecturas_actuales}") # Línea de depuración añadida
        if self.etiqueta_sens_temp:
            self.etiqueta_sens_temp.config(text=f"Temp: {lecturas_actuales['temperatura']:.1f}°C")
            self.etiqueta_sens_hum_aire.config(text=f"Hum Aire: {lecturas_actuales['humedad_aire']:.1f}%")
            self.etiqueta_sens_hum_suelo.config(text=f"Hum Suelo: {lecturas_actuales['humedad_suelo']:.0f} (0-1023)")
            self.etiqueta_sens_luz.config(text=f"Luz: {lecturas_actuales['luz']:.0f} (0-1023)")

        self.dibujar_planta()
        self.dibujar_grafico_agua()
        self.dibujar_tanque_agua()
        self.dibujar_grafico_altura()
        self.dibujar_indicador_alerta_led()
        self.actualizar_estado_wifi_esp32_gui()

        self.after(500, self.actualizar_gui)

    def dibujar_marco_invernadero(self, lienzo):
        """Dibuja el marco del invernadero en el lienzo especificado."""
        lienzo.delete("estructura_invernadero")
        lienzo_ancho = lienzo.winfo_width()
        lienzo_alto = lienzo.winfo_height()

        if lienzo_ancho < 100 or lienzo_alto < 100:
            return

        lienzo.create_rectangle(50, lienzo_alto - 50, lienzo_ancho - 50, lienzo_alto - 30, fill="#607D8B", outline="#455A64", width=2, tags="estructura_invernadero")
        lienzo.create_rectangle(50, 50, lienzo_ancho - 50, lienzo_alto - 50, fill="#B3E0F2", outline="#455A64", width=2, stipple="gray50", tags="estructura_invernadero")
        lienzo.create_polygon(50, 50, lienzo_ancho - 50, 50, lienzo_ancho/2, 20, fill="#B3E0F2", outline="#455A64", width=2, stipple="gray50", tags="estructura_invernadero")
        lienzo.create_line(50, 50, lienzo_ancho/2, 20, fill="#455A64", width=2, tags="estructura_invernadero")
        lienzo.create_line(lienzo_ancho - 50, 50, lienzo_ancho/2, 20, fill="#455A64", width=2, tags="estructura_invernadero")
        lienzo.create_line(lienzo_ancho/2, 50, lienzo_ancho/2, lienzo_alto-50, fill="#455A64", width=1, tags="estructura_invernadero")
        lienzo.create_line(50, (lienzo_alto-50)/2 + 50, lienzo_ancho-50, (lienzo_alto-50)/2 + 50, fill="#455A64", width=1, tags="estructura_invernadero")


    def dibujar_planta(self):
        """Dibuja la planta en el lienzo de la ventana de animación basándose en su estado actual."""
        if not self.lienzo_animacion_planta or not self.lienzo_animacion_planta.winfo_exists():
            return

        self.lienzo_animacion_planta.delete("elementos_planta")
        self.lienzo_animacion_planta.delete("fruto")
        self.lienzo_animacion_planta.delete("flor")
        self.lienzo_animacion_planta.delete("hoja_marchita")
        self.lienzo_animacion_planta.delete("elementos_muertos")

        lienzo_ancho = self.lienzo_animacion_planta.winfo_width()
        lienzo_alto = self.lienzo_animacion_planta.winfo_height()

        if lienzo_ancho == 1 or lienzo_alto == 1:
            return

        suelo_planta_y_inferior = lienzo_alto - 35
        suelo_planta_y_superior = suelo_planta_y_inferior - 20

        base_x = lienzo_ancho / 2

        self.lienzo_animacion_planta.create_rectangle(base_x - 80, suelo_planta_y_superior, base_x + 80, suelo_planta_y_inferior, fill="brown", outline="brown", tags="elementos_planta")

        altura_maxima_permitida_visual = suelo_planta_y_inferior - 55
        altura_visual = min(self.planta.altura_cm * 4, altura_maxima_permitida_visual)
        altura_visual = max(altura_visual, 5)

        ancho_tallo = max(2, min(10, self.planta.altura_cm / 5))

        color_planta = self.planta.obtener_color_etapa()

        tallo_y_superior = suelo_planta_y_inferior - altura_visual
        self.lienzo_animacion_planta.create_rectangle(base_x - ancho_tallo/2, tallo_y_superior,
                                      base_x + ancho_tallo/2, suelo_planta_y_inferior,
                                      fill=color_planta, outline=color_planta, tags="elementos_planta")

        base_dosel_y = tallo_y_superior + (ancho_tallo/2)
        ancho_max_dosel = min(max(20, self.planta.altura_cm * 1.5), 100)
        ancho_dosel_actual = ancho_max_dosel * (self.planta.salud / 100.0)
        altura_dosel_actual = altura_visual * 0.7

        self.lienzo_animacion_planta.create_oval(base_x - ancho_dosel_actual/2, base_dosel_y - altura_dosel_actual/2,
                                 base_x + ancho_dosel_actual/2, base_dosel_y + altura_dosel_actual/2,
                                 fill=color_planta, outline=color_planta, tags="elementos_planta")
        self.lienzo_animacion_planta.create_oval(base_x - ancho_dosel_actual/3 - 10, base_dosel_y - altura_dosel_actual/2 + 5,
                                 base_x + ancho_dosel_actual/3 - 10, base_dosel_y + altura_dosel_actual/2 - 5,
                                 fill=color_planta, outline=color_planta, tags="elementos_planta")
        self.lienzo_animacion_planta.create_oval(base_x - ancho_dosel_actual/3 + 10, base_dosel_y - altura_dosel_actual/2 + 5,
                                 base_x + ancho_dosel_actual/3 + 10, base_dosel_y + altura_dosel_actual/2 - 5,
                                 fill=color_planta, outline=color_planta, tags="elementos_planta")


        if self.planta.salud < 40 and not self.planta.esta_muerta:
             self.lienzo_animacion_planta.create_line(base_x + ancho_dosel_actual/4, base_dosel_y - altura_dosel_actual/4,
                                     base_x + ancho_dosel_actual/4 + 10, base_dosel_y - altura_dosel_actual/4 + 10,
                                     fill="darkorange", width=1, tags="hoja_marchita")
             self.lienzo_animacion_planta.create_line(base_x - ancho_dosel_actual/4, base_dosel_y - altura_dosel_actual/4,
                                     base_x - ancho_dosel_actual/4 - 10, base_dosel_y - altura_dosel_actual/4 + 10,
                                     fill="darkorange", width=1, tags="hoja_marchita")


        if self.planta.esta_muerta:
            self.lienzo_animacion_planta.create_rectangle(base_x - ancho_tallo/2, tallo_y_superior,
                                          base_x + ancho_tallo/2, suelo_planta_y_inferior,
                                          fill="darkgrey", outline="black", tags="elementos_muertos")
            radio_dosel_muerto = min(max(5, self.planta.altura_cm * 0.5), 30)
            self.lienzo_animacion_planta.create_oval(base_x - radio_dosel_muerto, base_dosel_y - radio_dosel_muerto,
                                     base_x + radio_dosel_muerto, base_dosel_y + radio_dosel_muerto,
                                     fill="black", outline="black", tags="elementos_muertos")
            self.lienzo_animacion_planta.create_line(base_x - 10, tallo_y_superior + 5, base_x + 10, tallo_y_superior + 15, fill="brown", width=2, tags="elementos_muertos")
            self.lienzo_animacion_planta.create_line(base_x + 10, tallo_y_superior + 5, base_x - 10, tallo_y_superior + 15, fill="brown", width=2, tags="elementos_muertos")
            self.lienzo_animacion_planta.create_text(base_x, tallo_y_superior + altura_visual / 4, text="✝️", font=("Arial", 30), fill="black", tags="elementos_muertos")

        pos_y_flor_fruto = tallo_y_superior - 10
        if self.planta.etapa_crecimiento == "Floración" and not self.planta.esta_muerta:
            tamano_flor = 10
            self.lienzo_animacion_planta.create_oval(base_x - tamano_flor, pos_y_flor_fruto - tamano_flor,
                                     base_x + tamano_flor, pos_y_flor_fruto + tamano_flor,
                                     fill="magenta", outline="purple", tags="flor")
        elif self.planta.etapa_crecimiento == "Fructificación" and not self.planta.esta_muerta:
            tamano_fruto = 8
            self.lienzo_animacion_planta.create_oval(base_x - tamano_fruto, pos_y_flor_fruto - tamano_fruto,
                                     base_x + tamano_fruto, pos_y_flor_fruto + tamano_fruto,
                                     fill="red", outline="darkred", tags="fruto")


class WiFiScannerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ESP32 WiFi Configuration")
        self.root.geometry("400x400")

        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.connect("broker.hivemq.com", 1883)
        self.mqtt_client.loop_start()

        self.networks = []
        self.selected_ssid = tk.StringVar()

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Button(
            main_frame,
            text="Scan Networks",
            command=self.start_scan
        ).pack(pady=10)

        self.listbox = tk.Listbox(main_frame, height=10)
        self.listbox.pack(fill=tk.X, pady=5)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        ttk.Label(main_frame, text="WiFi Password:").pack()
        self.password_entry = ttk.Entry(main_frame, show="*")
        self.password_entry.pack(fill=tk.X)

        ttk.Button(
            main_frame,
            text="Connect",
            command=self.connect_to_network
        ).pack(pady=10)

        self.status_label = ttk.Label(main_frame, text="Status: Ready")
        self.status_label.pack()

    def start_scan(self):
        """Send command to ESP32 to scan networks"""
        self.status_label.config(text="Status: Scanning networks...")
        # --- CAMBIO: Publicar el comando de escaneo al nuevo tópico ---
        self.mqtt_client.publish("invernadero/wifi/scan_command", "1") # El ESP32 espera "1"
        # --- FIN CAMBIO ---

    def on_select(self, event):
        """Handle network selection"""
        selection = self.listbox.curselection()
        if selection:
            self.selected_ssid.set(self.listbox.get(selection[0]))

    def connect_to_network(self):
        """Send WiFi credentials to ESP32"""
        if not self.selected_ssid.get():
            messagebox.showwarning("Error", "Please select a network!")
            return

        password = self.password_entry.get()
        if not password:
            messagebox.showwarning("Error", "Please enter password!")
            return

        payload = {
            "ssid": self.selected_ssid.get(),
            "password": password
        }

        self.mqtt_client.publish(
            "invernadero/config/wifi",
            json.dumps(payload)
        )
        self.status_label.config(text=f"Connecting to {self.selected_ssid.get()}...")

    def on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        client.subscribe("invernadero/wifi/scan_results")
        client.subscribe("invernadero/status/wifi")
        # --- CAMBIO: Suscribirse al nuevo tópico de comando de escaneo ---
        client.subscribe("invernadero/wifi/scan_command")
        # --- FIN CAMBIO ---

    def on_message(self, client, userdata, msg):
        """MQTT message handler"""
        if msg.topic == "invernadero/wifi/scan_results":
            # --- CAMBIO: Manejar el JSON de resultados de escaneo ---
            try:
                self.networks = json.loads(msg.payload.decode())
                self.listbox.delete(0, tk.END)

                if isinstance(self.networks, list):
                    for network_info in self.networks:
                        if isinstance(network_info, dict) and "ssid" in network_info:
                            self.listbox.insert(tk.END, network_info["ssid"])
                        elif isinstance(network_info, str): # Para el caso de "No networks found"
                            self.listbox.insert(tk.END, network_info)
                else: # Para el caso de "ERROR: EN MODO AP" o "No networks found" como string
                    self.listbox.insert(tk.END, msg.payload.decode())

                self.status_label.config(text=f"Found {len(self.networks)} networks" if isinstance(self.networks, list) else msg.payload.decode())
            except json.JSONDecodeError:
                self.status_label.config(text=f"Error decoding scan results: {msg.payload.decode()}")
            # --- FIN CAMBIO ---

        elif msg.topic == "invernadero/status/wifi":
            self.status_label.config(text=f"WiFi Status: {msg.payload.decode()}")
        # --- CAMBIO: No es necesario manejar el comando de escaneo aquí, ya se envía ---
        # elif msg.topic == "invernadero/wifi/scan_command":
        #     pass # Este es un comando que se envía, no se recibe para procesar aquí.
        # --- FIN CAMBIO ---

    def run(self):
        self.root.mainloop()


# --- Ejecución principal ---
if __name__ == "__main__":
    app = AppInvernadero()
    app.mainloop()

