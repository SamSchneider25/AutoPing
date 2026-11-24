# AutoPing (PingCtl)

Массовый пинг и проверка TCP-портов в одном окне: статус, тайлы потерь, статистика.

## Запуск

```bash
python autoping.py ping 1.1.1.1 8.8.8.8 -c 4 -W 800
python autoping.py port 10.15.20.205:1501 -W 500
python autoping.py -t 1.1.1.1          # непрерывно
python autoping.py --watch 1 1.1.1.1   # обновление раз в секунду
python autoping.py                     # интерактивный режим
```

Слово `ping` можно опускать, хосты разделяются `;`, `,` или пробелом. В REPL: `help`, `clear`, `exit`.

## Флаги ping

| Флаг | Описание |
|------|----------|
| `-c N` | пакетов на хост (4) |
| `-t` | непрерывно |
| `-f` | flood: быстрая серия, live-потери |
| `-W N` / `--timeout` | таймаут пакета, мс (1000) |
| `-i N`, `-w N` | интервал и дедлайн, сек (Linux) |
| `-s N` | размер пакета, байт |
| `--watch N` | обновление экрана, сек |
| `--file F` | хосты из файла |
| `--output json\|csv --out F` | экспорт результата |

Валидны IP (v4/v6), DNS с точкой и `localhost`; мусор помечается `✗`. Порт задаётся как `host:port`, диапазон 1–65535.

Коды выхода: `0` ok · `1` проблемы · `2` аргументы · `130` Ctrl+C.

MIT — SamSchneider25 (Griph).

## Development
Built with AI assistance (OpenCode) for boilerplate and refactoring.
Architecture, Windows console rendering, debugging and acceptance tests are my own work
