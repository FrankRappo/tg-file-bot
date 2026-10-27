#!/bin/bash
# Запуск/остановка Telegram бота
# Использование:
#   ./start.sh          — запустить в фоне
#   ./start.sh stop     — остановить
#   ./start.sh status   — проверить статус
#   ./start.sh restart  — перезапустить
#   ./start.sh log      — показать последние строки лога

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/bot.pid"
LOG_FILE="$DIR/bot.log"

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE") || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    grep -q "bot.py" "/proc/$pid/cmdline" 2>/dev/null
}

case "${1:-start}" in
    start)
        if is_running; then
            echo "Бот уже запущен (PID $(cat "$PID_FILE"))"
            exit 0
        fi
        echo "Запуск бота..."
        nohup python3 "$DIR/bot.py" >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        sleep 1
        if is_running; then
            echo "Бот запущен (PID $(cat "$PID_FILE"))"
        else
            echo "Ошибка запуска, смотри лог: tail -20 $LOG_FILE"
            exit 1
        fi
        ;;
    stop)
        if is_running; then
            PID=$(cat "$PID_FILE")
            kill "$PID"
            echo "Бот остановлен (PID $PID)"
            rm -f "$PID_FILE"
        else
            echo "Бот не запущен"
            rm -f "$PID_FILE"
        fi
        ;;
    restart)
        "$0" stop
        sleep 1
        "$0" start
        ;;
    status)
        if is_running; then
            echo "Бот работает (PID $(cat "$PID_FILE"))"
        else
            echo "Бот не запущен"
        fi
        ;;
    log)
        tail -30 "$LOG_FILE"
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|log}"
        exit 1
        ;;
esac
