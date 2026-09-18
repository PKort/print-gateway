# Print Gateway

Lekka aplikacja webowa do obsługi domowej drukarki przez CUPS. Została przygotowana dla HP LaserJet 4000 podłączonej przez JetDirect, ale nazwę drukarki i jej adres można zmienić w konfiguracji.

## Funkcje

- wysyłanie plików PDF do CUPS,
- wybór zakresu stron, np. `1-3, 5, 8-10`,
- drukowanie 1, 2, 4, 6, 9 lub 16 stron na arkuszu,
- wybór liczby kopii, jakości i druku dwustronnego,
- podgląd oraz anulowanie zadań w kolejce,
- podgląd stanu zasilania drukarki,
- włączanie i wyłączanie gniazdka przez Home Assistant/MQTT,
- uruchamianie w odizolowanym kontenerze Docker.

## Uruchomienie

1. Skopiuj `.env.example` do `.env`.
2. Ustaw silne, unikalne wartości `SECRET_KEY` i `MQTT_PASSWORD`.
3. Dostosuj `PRINTER_NAME`, `PRINTER_HOST`, sieć Home Assistanta i mapowanie portów w `docker-compose.yml`.
4. Uruchom:

   ```bash
   docker compose up -d --build
   ```

Aplikacja w kontenerze korzysta z gniazda CUPS hosta zamontowanego jako `/run/cups/cups.sock`.

## Home Assistant

Przykładowa konfiguracja MQTT znajduje się w `home-assistant-print-gateway.yaml`. Przed użyciem dostosuj encję `switch.gniazdko_drukarki` i dane brokera do własnej instalacji.

## Bezpieczeństwo

- `.env` nie jest śledzony przez Git.
- Nie zapisuj w repozytorium prawdziwych haseł MQTT ani klucza sesji.
- Domyślna konfiguracja publikuje backend tylko na `127.0.0.1:3030`; dostęp z LAN powinien prowadzić przez skonfigurowany reverse proxy.

