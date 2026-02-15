#!/bin/bash

# Configuration
APP_DIR="$(pwd)"
PARENT_DIR="$(dirname "$APP_DIR")"
GUNICORN_BIN="gunicorn"
PID_FILE="$APP_DIR/gunicorn.pid"
CAFFEINATE_PID_FILE="$APP_DIR/caffeinate.pid"
CLOUDFLARED_CAFFEINATE_PID_FILE="$APP_DIR/caffeinate-cloudflared.pid"
LOG_DIR="$APP_DIR/logs"
ACCESS_LOG="$LOG_DIR/access.log"
ERROR_LOG="$LOG_DIR/error.log"
BIND_ADDRESS="127.0.0.1:5001"

# Ensure log directory exists
mkdir -p $LOG_DIR

# Function to check if the server is running
check_status() {
    local server_running=false
    local tunnel_running=false
    
    # Check gunicorn server
    if [ -f $PID_FILE ]; then
        pid=$(cat $PID_FILE)
        if ps -p $pid > /dev/null; then
            echo "✅ Web Server is running (PID: $pid)"
            server_running=true
            
            # Calculate uptime
            start_time=$(ps -p $pid -o lstart=)
            echo "   Running since: $start_time"
            
            # Get request count from access log
            if [ -f $ACCESS_LOG ]; then
                requests=$(wc -l < $ACCESS_LOG)
                echo "   Total requests served: $requests"
            fi
            
            # Show resource usage
            echo -e "   Resource usage:"
            ps -p $pid -o %cpu,%mem,rss | tail -1
        fi
    fi
    
    if [ "$server_running" = false ]; then
        echo "❌ Web Server is not running"
    fi
    
    # Check CloudFlare Tunnel
    cloudflared_pid=$(ps aux | grep "cloudflared tunnel" | grep -v grep | head -1 | awk '{print $2}')
    if [ -n "$cloudflared_pid" ]; then
        echo -e "\n✅ CloudFlare Tunnel is running (PID: $cloudflared_pid)"
        tunnel_running=true
    else
        echo -e "\n❌ CloudFlare Tunnel is not running"
    fi
    
    # Check caffeinate processes
    caffeinate_count=$(ps aux | grep "caffeinate" | grep -v grep | wc -l | tr -d ' ')
    if [ "$caffeinate_count" -gt 0 ]; then
        echo -e "\n✅ Caffeinate processes active: $caffeinate_count (preventing sleep)"
    else
        echo -e "\n⚠️  No caffeinate processes running (Mac may sleep when lid closes)"
    fi
    
    if [ "$server_running" = true ] && [ "$tunnel_running" = true ]; then
        return 0
    else
        return 1
    fi
}

# Start the server
start() {
    if check_status > /dev/null; then
        echo "Server is already running"
    else
        echo "Starting server with caffeinate (prevents sleep when lid is closed)..."
        cd $APP_DIR
        
        # Start gunicorn first
        PYTHONPATH="$PARENT_DIR" $GUNICORN_BIN \
            --daemon \
            --pid $PID_FILE \
            --bind $BIND_ADDRESS \
            --workers 3 \
            --access-logfile $ACCESS_LOG \
            --error-logfile $ERROR_LOG \
            --capture-output \
            --reload \
            website.app:app
        
        sleep 2
        
        # Start caffeinate watching the gunicorn process
        # -d: prevent display sleep, -i: prevent idle sleep, -m: prevent disk sleep, -s: prevent system sleep
        # -w: wait for process to exit
        if [ -f $PID_FILE ]; then
            gunicorn_pid=$(cat $PID_FILE)
            nohup caffeinate -dims -w $gunicorn_pid > /dev/null 2>&1 &
            echo $! > "$APP_DIR/caffeinate.pid"
            echo "Started caffeinate to prevent sleep (PID: $!)"
            echo "Note: System will stay awake with lid closed while server is running"
        fi
        
        check_status
    fi
}

# Stop the server
stop() {
    if [ -f $PID_FILE ]; then
        echo "Stopping web server..."
        pid=$(cat $PID_FILE)
        kill -TERM $pid
        rm -f $PID_FILE
        
        # Stop web server caffeinate if running
        if [ -f "$CAFFEINATE_PID_FILE" ]; then
            caffeinate_pid=$(cat "$CAFFEINATE_PID_FILE")
            kill $caffeinate_pid 2>/dev/null
            rm -f "$CAFFEINATE_PID_FILE"
            echo "Stopped web server caffeinate"
        fi
        
        echo "Web server stopped"
    else
        echo "Web server is not running"
    fi
    
    # Stop cloudflared caffeinate if running
    if [ -f "$CLOUDFLARED_CAFFEINATE_PID_FILE" ]; then
        cloudflared_caffeinate_pid=$(cat "$CLOUDFLARED_CAFFEINATE_PID_FILE")
        kill $cloudflared_caffeinate_pid 2>/dev/null
        rm -f "$CLOUDFLARED_CAFFEINATE_PID_FILE"
        echo "Stopped CloudFlare Tunnel caffeinate"
    fi
    
    echo "Note: CloudFlare Tunnel is managed by LaunchAgent and will keep running"
    echo "To stop tunnel: launchctl unload ~/Library/LaunchAgents/com.camping.cloudflared.plist"
}

# Restart the server
restart() {
    stop
    sleep 2
    start
}

# Show logs
logs() {
    echo "Access log (last 10 lines):"
    tail -n 10 $ACCESS_LOG
    echo -e "\nError log (last 10 lines):"
    tail -n 10 $ERROR_LOG
}

# Main script logic
case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        check_status
        ;;
    logs)
        logs
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac

exit 0 