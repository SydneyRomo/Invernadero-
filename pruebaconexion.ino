#include <WiFi.h>
#include <PubSubClient.h>
#include <EEPROM.h>
#include <WebServer.h> // Para el servidor web en modo AP
#include <DHT.h>       // Para el sensor DHT11
#include <ArduinoJson.h> // Para parsear JSON de forma robusta
#include <U8g2lib.h>   // Para la pantalla OLED

// --- Configuración WiFi ---
const char* AP_SSID = "ESP32_Invernadero_AP";
const char* AP_PASSWORD = "password123"; // Contraseña para el AP del ESP32
const int EEPROM_SIZE = 96; // Suficiente para SSID (32) + Password (64)

// --- Configuración MQTT ---
const char* MQTT_BROKER = "broker.hivemq.com";
const int MQTT_PORT = 1883;
const char* MQTT_CLIENT_ID = "ESP32Invernadero";

// Temas MQTT (Asegúrate de que coincidan con tu GUI de Python)
const char* MQTT_TOPIC_CONFIG_WIFI = "invernadero/config/wifi";
const char* MQTT_TOPIC_STATUS_WIFI = "invernadero/status/wifi_connect";
const char* MQTT_TOPIC_SCAN_COMMAND = "invernadero/wifi/scan_command"; // Comando para iniciar escaneo
const char* MQTT_TOPIC_SCAN_RESULTS = "invernadero/wifi/scan_results"; // Resultados del escaneo

// Temas para sensores y actuadores
const char* MQTT_TOPIC_TEMPERATURA = "invernadero/temperatura";
const char* MQTT_TOPIC_HUMEDAD_AIRE = "invernadero/humedad_aire";
const char* MQTT_TOPIC_HUMEDAD_SUELO = "invernadero/humedad_suelo";
const char* MQTT_TOPIC_LUZ = "invernadero/luz";
const char* MQTT_TOPIC_NIVEL_AGUA = "invernadero/nivel_agua";
const char* MQTT_TOPIC_CONTROL_BOMBA = "invernadero/control_bomba";
const char* MQTT_TOPIC_STATUS_BOMBA = "invernadero/bomba_estado";
const char* MQTT_TOPIC_CONTROL_LED_ALERTA = "invernadero/control_led_alerta";
const char* MQTT_TOPIC_STATUS_LED_ALERTA = "invernadero/estado_led_alerta"; // Coherente con la GUI
const char* MQTT_TOPIC_CONTROL_RIEGO_AUTO_SENSOR = "invernadero/control_riego_auto_sensor";
const char* MQTT_TOPIC_STATUS_RIEGO_AUTO_SENSOR = "invernadero/status/riego_auto_sensor";


// --- Pines de Hardware ---
#define DHTPIN 4            // Pin para el sensor DHT11
#define DHTTYPE DHT11       // Tipo de sensor DHT
#define SOIL_MOISTURE_PIN 33 // Pin para el sensor de humedad del suelo (ADC1_CH5)
#define LDR_PIN 34          // Pin para el sensor de luz (LDR) (ADC1_CH6)
#define WATER_LEVEL_PIN 35  // Pin para el sensor de nivel de agua (ADC1_CH7)
#define BOMBA_PIN 27        // Pin para el relé de la bomba
#define LED_ALERTA_PIN 2    // Pin para el LED de alerta
#define LED_VERDE_PIN 25    // Pin para el LED indicador de WiFi conectado (verde)
#define LED_ROJO_PIN 26     // Pin para el LED indicador de modo AP (rojo)

// --- Objetos ---
WiFiClient espClient;
PubSubClient client(espClient);
WebServer server(80); // Servidor web en el puerto 80
DHT dht(DHTPIN, DHTTYPE); // Objeto DHT
// Configuración de la pantalla OLED (SSD1306, 128x64, I2C)
// Asegúrate de que los pines SDA y SCL de tu ESP32 coincidan con los que usa la librería.
// Por defecto, U8g2lib usa GPIO21 para SDA y GPIO22 para SCL en ESP32.
// Si usas otros pines, necesitarás Wire.begin(SDA_PIN, SCL_PIN) en setup().
// El último parámetro es el pin de reset, si no tienes uno, usa U8X8_PIN_NONE.
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);


// --- Variables Globales ---
char stored_ssid[32];
char stored_password[64];
bool automaticIrrigationEnabled = false; // Estado del riego automático por sensor

// Rangos de calibración para el sensor de humedad del suelo (ajustar según tu sensor)
// Estos valores RAW son los que lees del sensor cuando está en aire (seco) y en agua (mojado).
// Con un sensor capacitivo, el valor RAW disminuye a medida que la humedad aumenta.
const int HUMEDAD_SUELO_SECO_RAW = 3000; // Valor ADC cuando el sensor está seco (en el aire)
const int HUMEDAD_SUELO_MOJADO_RAW = 1200; // Valor ADC cuando el sensor está en agua (o muy húmedo)

// Umbrales para el control automático de riego (en porcentaje 0-100)
const int HUMEDAD_SUELO_UMBRAL_INICIO_RIEGO = 40; // Si la humedad cae por debajo de este valor, se activa el riego
const int HUMEDAD_SUELO_UMBRAL_FIN_RIEGO = 70;    // Si la humedad alcanza este valor, se desactiva el riego

unsigned long lastSensorReadMillis = 0;
const long SENSOR_READ_INTERVAL = 5000; // Intervalo de lectura de sensores en ms (5 segundos)

// --- Prototipos de funciones ---
void setup_wifi();
void reconnect_mqtt();
void callback(char* topic, byte* payload, unsigned int length);
void save_credentials(const char* ssid, const char* password);
void load_credentials();
void start_ap_mode();
void handleRoot();
void handleSaveWifi();
void handleNotFound();
void scan_networks_and_publish();
void handleSensors(); // Nueva función para manejar lecturas de sensores
void publishSensorData(float temp, float hum, float soilHumidity, int light, int waterLevel);
void displayData(float temp, float hum, float soilHumidity, int light, int waterLevel);
void controlBomba(bool state);
void controlLedAlerta(bool state);


void setup() {
  Serial.begin(115200);

  // Inicializar EEPROM
  if (!EEPROM.begin(EEPROM_SIZE)) {
    Serial.println("Error al inicializar EEPROM. Reiniciando...");
    delay(1000);
    ESP.restart();
  }

  // Configurar pines de salida
  pinMode(BOMBA_PIN, OUTPUT);
  digitalWrite(BOMBA_PIN, LOW); // Asegurarse de que la bomba esté apagada al inicio
  pinMode(LED_ALERTA_PIN, OUTPUT);
  digitalWrite(LED_ALERTA_PIN, LOW); // LED de alerta apagado al inicio
  pinMode(LED_VERDE_PIN, OUTPUT);
  digitalWrite(LED_VERDE_PIN, LOW); // LED verde apagado al inicio
  pinMode(LED_ROJO_PIN, OUTPUT);
  digitalWrite(LED_ROJO_PIN, LOW);   // LED rojo apagado al inicio

  // Inicializar sensor DHT
  dht.begin();

  // Inicializar OLED
  // Si usas pines I2C diferentes a los predeterminados (GPIO21 SDA, GPIO22 SCL),
  // descomenta y ajusta la siguiente línea:
  // Wire.begin(YOUR_SDA_PIN, YOUR_SCL_PIN);
  u8g2.begin();
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_ncenB08_tr); // Fuente pequeña
  u8g2.drawStr(0, 10, "Iniciando ESP32...");
  u8g2.sendBuffer();
  delay(1000); // Pequeña pausa para que el mensaje sea visible

  // Configurar MQTT
  client.setServer(MQTT_BROKER, MQTT_PORT);
  client.setCallback(callback);

  // Conectar WiFi (o iniciar AP si no hay credenciales)
  setup_wifi();
}

void loop() {
  // Si estamos en modo AP, manejar las solicitudes del servidor web
  if (WiFi.getMode() == WIFI_MODE_AP) {
    server.handleClient();
    digitalWrite(LED_ROJO_PIN, HIGH); // Mantener LED rojo encendido en modo AP
    digitalWrite(LED_VERDE_PIN, LOW);
  } else {
    // Si estamos en modo estación (conectados a WiFi), mantener la conexión MQTT y leer sensores
    if (!client.connected()) {
      reconnect_mqtt(); // Intentar reconectar a MQTT si se pierde la conexión
    }
    client.loop(); // Procesar mensajes MQTT entrantes y mantener la conexión
    handleSensors(); // Leer y publicar datos de sensores, y controlar el riego automático
    digitalWrite(LED_VERDE_PIN, HIGH); // Mantener LED verde encendido en modo estación
    digitalWrite(LED_ROJO_PIN, LOW);
  }
}

/**
 * @brief Maneja la lectura de sensores y la publicación de datos.
 * También implementa la lógica de riego automático basada en la humedad del suelo.
 */
void handleSensors() {
  // Solo leer sensores si ha pasado el intervalo definido
  if (millis() - lastSensorReadMillis >= SENSOR_READ_INTERVAL) {
    lastSensorReadMillis = millis();

    // Leer sensor DHT11
    float temp = dht.readTemperature();
    float hum = dht.readHumidity();

    // Leer sensor de humedad del suelo (valor RAW)
    int soilRaw = analogRead(SOIL_MOISTURE_PIN);
    // Mapear el valor RAW a un porcentaje (0-100)
    float soilHumidity = map(soilRaw, HUMEDAD_SUELO_SECO_RAW, HUMEDAD_SUELO_MOJADO_RAW, 0, 100);
    // Asegurarse de que el valor esté dentro del rango 0-100
    soilHumidity = constrain(soilHumidity, 0, 100);

    // Leer sensor de luz (LDR)
    int light = analogRead(LDR_PIN); // Valor RAW del LDR (0-4095)

    // Leer sensor de nivel de agua (asumiendo un sensor analógico simple)
    int waterLevelRaw = analogRead(WATER_LEVEL_PIN);
    // Mapear el valor RAW a un porcentaje (0-100). Ajustar los valores RAW según tu sensor.
    // Ejemplo: 0 = tanque vacío, 4095 = tanque lleno
    int waterLevel = map(waterLevelRaw, 0, 4095, 0, 100);
    waterLevel = constrain(waterLevel, 0, 100);

    // Publicar datos de sensores a MQTT
    publishSensorData(temp, hum, soilHumidity, light, waterLevel);

    // Lógica de control automático de riego
    if (automaticIrrigationEnabled) {
      // Si la humedad del suelo es baja y la bomba está apagada, encenderla
      if (soilHumidity < HUMEDAD_SUELO_UMBRAL_INICIO_RIEGO && digitalRead(BOMBA_PIN) == LOW) {
        Serial.println("Humedad del suelo baja. Activando bomba.");
        controlBomba(true);
      }
      // Si la humedad del suelo es suficiente y la bomba está encendida, apagarla
      else if (soilHumidity >= HUMEDAD_SUELO_UMBRAL_FIN_RIEGO && digitalRead(BOMBA_PIN) == HIGH) {
        Serial.println("Humedad del suelo óptima. Desactivando bomba.");
        controlBomba(false);
      }
    }

    // Mostrar datos en la pantalla OLED
    displayData(temp, hum, soilHumidity, light, waterLevel);
  }
}

/**
 * @brief Muestra los datos de los sensores en la pantalla OLED.
 * @param temp Temperatura en grados Celsius.
 * @param hum Humedad del aire en porcentaje.
 * @param soilHumidity Humedad del suelo en porcentaje.
 * @param light Valor de luz RAW.
 * @param waterLevel Nivel de agua en porcentaje.
 */
void displayData(float temp, float hum, float soilHumidity, int light, int waterLevel) {
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_ncenB08_tr); // Fuente pequeña

  u8g2.setCursor(0, 10);
  if (!isnan(temp)) {
    u8g2.print("Temp: "); u8g2.print(temp, 1); u8g2.print("C");
  } else {
    u8g2.print("Temp: N/A");
  }

  u8g2.setCursor(0, 25);
  if (!isnan(hum)) {
    u8g2.print("Hum: "); u8g2.print(hum, 1); u8g2.print("%");
  } else {
    u8g2.print("Hum: N/A");
  }

  u8g2.setCursor(0, 40);
  u8g2.print("Suelo: "); u8g2.print(soilHumidity, 0); u8g2.print("%"); // Mostrar sin decimales

  u8g2.setCursor(0, 55);
  u8g2.print("Luz: "); u8g2.print(light); u8g2.print(" Agua: "); u8g2.print(waterLevel); u8g2.print("%");

  u8g2.sendBuffer();
}

/**
 * @brief Publica los datos de los sensores a los temas MQTT correspondientes.
 * @param temp Temperatura en grados Celsius.
 * @param hum Humedad del aire en porcentaje.
 * @param soilHumidity Humedad del suelo en porcentaje.
 * @param light Valor de luz RAW.
 * @param waterLevel Nivel de agua en porcentaje.
 */
void publishSensorData(float temp, float hum, float soilHumidity, int light, int waterLevel) {
  // Publicar temperatura si es un valor válido
  if (!isnan(temp)) {
    client.publish(MQTT_TOPIC_TEMPERATURA, String(temp, 1).c_str());
  }
  // Publicar humedad del aire si es un valor válido
  if (!isnan(hum)) {
    client.publish(MQTT_TOPIC_HUMEDAD_AIRE, String(hum, 1).c_str());
  }
  // Publicar humedad del suelo
  client.publish(MQTT_TOPIC_HUMEDAD_SUELO, String(soilHumidity, 0).c_str()); // Enviar como entero
  // Publicar luz
  client.publish(MQTT_TOPIC_LUZ, String(light).c_str());
  // Publicar nivel de agua
  client.publish(MQTT_TOPIC_NIVEL_AGUA, String(waterLevel).c_str());
}


void setup_wifi() {
  Serial.println("Intentando conectar a WiFi...");
  load_credentials(); // Intentar cargar credenciales al inicio

  if (strlen(stored_ssid) > 0) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(stored_ssid, stored_password);
    Serial.print("Conectando a ");
    Serial.println(stored_ssid);

    u8g2.clearBuffer();
    u8g2.drawStr(0, 10, "Conectando WiFi...");
    u8g2.drawStr(0, 25, stored_ssid);
    u8g2.sendBuffer();

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) { // Esperar hasta 15 segundos
      delay(500);
      Serial.print(".");
      attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("\nWiFi conectado!");
      Serial.print("Dirección IP: ");
      Serial.println(WiFi.localIP());
      digitalWrite(LED_VERDE_PIN, HIGH); // Encender LED verde si la conexión es exitosa
      digitalWrite(LED_ROJO_PIN, LOW);
      client.publish(MQTT_TOPIC_STATUS_WIFI, ("CONECTADO OK: " + WiFi.localIP().toString()).c_str());
    } else {
      Serial.println("\nFallo al conectar a WiFi con credenciales almacenadas.");
      client.publish(MQTT_TOPIC_STATUS_WIFI, "FALLO CONEXION (credenciales almacenadas)");
      start_ap_mode(); // Si falla, iniciar modo AP
    }
  } else {
    Serial.println("No hay credenciales WiFi almacenadas. Iniciando modo AP.");
    client.publish(MQTT_TOPIC_STATUS_WIFI, "NO CREDENCIALES (Modo AP)");
    start_ap_mode(); // Si no hay credenciales, iniciar modo AP
  }
}

void reconnect_mqtt() {
  while (!client.connected()) {
    Serial.print("Intentando conexión MQTT...");
    if (client.connect(MQTT_CLIENT_ID)) {
      Serial.println("conectado!");
      // Suscribirse a los temas de control
      client.subscribe(MQTT_TOPIC_CONFIG_WIFI);
      client.subscribe(MQTT_TOPIC_SCAN_COMMAND); // Suscribirse para recibir comandos de escaneo
      client.subscribe(MQTT_TOPIC_CONTROL_BOMBA);
      client.subscribe(MQTT_TOPIC_CONTROL_LED_ALERTA);
      client.subscribe(MQTT_TOPIC_CONTROL_RIEGO_AUTO_SENSOR);

      Serial.print("Suscrito a: "); Serial.println(MQTT_TOPIC_CONFIG_WIFI);
      Serial.print("Suscrito a: "); Serial.println(MQTT_TOPIC_SCAN_COMMAND);
      Serial.print("Suscrito a: "); Serial.println(MQTT_TOPIC_CONTROL_BOMBA);
      Serial.print("Suscrito a: "); Serial.println(MQTT_TOPIC_CONTROL_LED_ALERTA);
      Serial.print("Suscrito a: "); Serial.println(MQTT_TOPIC_CONTROL_RIEGO_AUTO_SENSOR);

      // Publicar el estado inicial de los dispositivos y la conexión
      client.publish(MQTT_TOPIC_STATUS_WIFI, ("MQTT CONECTADO. IP: " + WiFi.localIP().toString()).c_str());
      client.publish(MQTT_TOPIC_STATUS_BOMBA, digitalRead(BOMBA_PIN) == HIGH ? "ON" : "OFF");
      client.publish(MQTT_TOPIC_STATUS_LED_ALERTA, digitalRead(LED_ALERTA_PIN) == HIGH ? "ON" : "OFF");
      client.publish(MQTT_TOPIC_STATUS_RIEGO_AUTO_SENSOR, automaticIrrigationEnabled ? "ON" : "OFF");

    } else {
      Serial.print("falló, rc=");
      Serial.print(client.state());
      Serial.println(" intentando de nuevo en 5 segundos");
      delay(5000);
    }
  }
}

void callback(char* topic, byte* payload, unsigned int length) {
  Serial.print("Mensaje recibido [");
  Serial.print(topic);
  Serial.print("] ");
  String message = "";
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.println(message);

  if (String(topic) == MQTT_TOPIC_CONFIG_WIFI) {
    StaticJsonDocument<100> doc; // Usar ArduinoJson para parsear
    DeserializationError error = deserializeJson(doc, message);

    if (error) {
      Serial.print("Error al deserializar JSON de WiFi: ");
      Serial.println(error.f_str());
      return;
    }

    const char* new_ssid = doc["ssid"];
    const char* new_password = doc["password"];

    if (new_ssid && strlen(new_ssid) > 0) {
      save_credentials(new_ssid, new_password);
      client.publish(MQTT_TOPIC_STATUS_WIFI, "CREDENTIALES RECIBIDAS. REINICIANDO WIFI...");
      Serial.println("Credenciales WiFi recibidas y guardadas. Reiniciando ESP32...");
      delay(1000);
      ESP.restart(); // Reiniciar para aplicar la nueva configuración WiFi
    }
  } else if (String(topic) == MQTT_TOPIC_SCAN_COMMAND) {
      // Si se recibe un comando de escaneo (por ejemplo, "1")
      if (message == "1") {
          scan_networks_and_publish();
      }
  } else if (String(topic) == MQTT_TOPIC_CONTROL_BOMBA) {
    if (message == "ON") {
      controlBomba(true);
      Serial.println("Comando Bomba: ON");
    } else if (message == "OFF") {
      controlBomba(false);
      Serial.println("Comando Bomba: OFF");
    }
  } else if (String(topic) == MQTT_TOPIC_CONTROL_LED_ALERTA) {
    if (message == "ON") {
      controlLedAlerta(true);
      Serial.println("Comando LED Alerta: ON");
    } else if (message == "OFF") {
      controlLedAlerta(false);
      Serial.println("Comando LED Alerta: OFF");
    }
  } else if (String(topic) == MQTT_TOPIC_CONTROL_RIEGO_AUTO_SENSOR) {
    if (message == "ON") {
      automaticIrrigationEnabled = true;
      Serial.println("Riego automático por sensor: ACTIVADO");
    } else if (message == "OFF") {
      automaticIrrigationEnabled = false;
      controlBomba(false); // Asegurarse de apagar la bomba si se desactiva el modo automático
      Serial.println("Riego automático por sensor: DESACTIVADO");
    }
    // Publicar el estado actual del riego automático
    client.publish(MQTT_TOPIC_STATUS_RIEGO_AUTO_SENSOR, automaticIrrigationEnabled ? "ON" : "OFF");
  }
}

void save_credentials(const char* ssid, const char* password) {
  EEPROM.writeString(0, ssid);
  EEPROM.writeString(32, password); // Offset para la contraseña
  EEPROM.commit();
  Serial.println("Credenciales guardadas en EEPROM.");
}

void load_credentials() {
  EEPROM.readString(0, stored_ssid, sizeof(stored_ssid));
  EEPROM.readString(32, stored_password, sizeof(stored_password));
  Serial.print("Credenciales cargadas: SSID='");
  Serial.print(stored_ssid);
  Serial.print("', Pass='");
  Serial.print(stored_password);
  Serial.println("'");
}

void start_ap_mode() {
  Serial.println("Iniciando modo Punto de Acceso (AP)...");
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASSWORD);
  IPAddress IP = WiFi.softAPIP();
  Serial.print("AP IP address: ");
  Serial.println(IP);
  client.publish(MQTT_TOPIC_STATUS_WIFI, "MODO CONFIGURACION AP. IP: 192.168.4.1");

  digitalWrite(LED_ROJO_PIN, HIGH); // Encender LED rojo para indicar modo AP
  digitalWrite(LED_VERDE_PIN, LOW);

  u8g2.clearBuffer();
  u8g2.drawStr(0, 10, "Modo AP Activo!");
  u8g2.drawStr(0, 25, "SSID: ESP32_Invernadero_AP");
  u8g2.drawStr(0, 40, "Pass: password123");
  u8g2.drawStr(0, 55, "IP: 192.168.4.1");
  u8g2.sendBuffer();

  // Configurar rutas del servidor web
  server.on("/", HTTP_GET, handleRoot);
  server.on("/savewifi", HTTP_POST, handleSaveWifi);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("Servidor web AP iniciado.");
}

void handleRoot() {
  String html = "<!DOCTYPE html><html><head><title>Config WiFi ESP32</title>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<style>body{font-family:Arial,sans-serif;margin:20px;background-color:#212121;color:white;}";
  html += "input[type=text],input[type=password]{width:100%;padding:10px;margin:8px 0;display:inline-block;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;background-color:#424242;color:white;}";
  html += "input[type=submit]{width:100%;background-color:#4CAF50;color:white;padding:14px 20px;margin:8px 0;border:none;border-radius:4px;cursor:pointer;}";
  html += "input[type=submit]:hover{background-color:#45a049;}";
  html += ".container{background-color:#333333;padding:20px;border-radius:8px;}</style></head><body>";
  html += "<div class='container'><h1>Configurar WiFi</h1>";
  html += "<form action='/savewifi' method='post'>";
  html += "<label for='ssid'><b>SSID</b></label>";
  html += "<input type='text' placeholder='Introduce SSID' name='ssid' required>";
  html += "<label for='pass'><b>Contraseña</b></label>";
  html += "<input type='password' placeholder='Introduce Contraseña' name='password'>";
  html += "<input type='submit' value='Guardar y Conectar'>";
  html += "</form></div></body></html>";
  server.send(200, "text/html", html);
}

void handleSaveWifi() {
  if (server.hasArg("ssid") && server.hasArg("password")) {
    String new_ssid = server.arg("ssid");
    String new_password = server.arg("password");
    save_credentials(new_ssid.c_str(), new_password.c_str());
    server.send(200, "text/html", "<h1>Credenciales guardadas. Reiniciando ESP32...</h1>");
    delay(1000);
    ESP.restart();
  } else {
    server.send(400, "text/plain", "Error: SSID o contraseña no proporcionados.");
  }
}

void handleNotFound() {
  server.send(404, "text/plain", "Not Found");
}

void scan_networks_and_publish() {
  Serial.println("Escaneando redes WiFi...");
  int n = WiFi.scanNetworks();
  Serial.println("Escaneo completado.");

  // Usar ArduinoJson para construir el array JSON de forma robusta
  DynamicJsonDocument doc(1024); // Ajusta el tamaño según el número esperado de redes

  if (n == 0) {
    Serial.println("No se encontraron redes.");
    // doc.add("No networks found"); // Puedes enviar un mensaje si lo prefieres
  } else {
    JsonArray networksArray = doc.to<JsonArray>();
    for (int i = 0; i < n; ++i) {
      // Puedes incluir más detalles si los necesitas, como RSSI o encriptación
      // JsonObject network = networksArray.add<JsonObject>();
      // network["ssid"] = WiFi.SSID(i);
      // network["rssi"] = WiFi.RSSI(i);
      networksArray.add(WiFi.SSID(i)); // Solo el SSID
    }
  }

  String json_output;
  serializeJson(doc, json_output); // Serializar el documento JSON a un String

  Serial.print("Redes encontradas: ");
  Serial.println(json_output);
  client.publish(MQTT_TOPIC_SCAN_RESULTS, json_output.c_str());
}

// --- Funciones de control de actuadores ---
void controlBomba(bool state) {
  digitalWrite(BOMBA_PIN, state ? HIGH : LOW); // Encender/apagar la bomba
  client.publish(MQTT_TOPIC_STATUS_BOMBA, state ? "ON" : "OFF"); // Publicar el estado actual
}

void controlLedAlerta(bool state) {
  digitalWrite(LED_ALERTA_PIN, state ? HIGH : LOW); // Encender/apagar el LED
  client.publish(MQTT_TOPIC_STATUS_LED_ALERTA, state ? "ON" : "OFF"); // Publicar el estado actual
}
