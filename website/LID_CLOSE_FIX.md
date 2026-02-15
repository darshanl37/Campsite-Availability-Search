# Website Stays Running When MacBook Lid is Closed

## Problem
The website (rerecreation.us) was going down whenever you closed your MacBook lid because:
1. The web server (gunicorn) was stopping
2. The CloudFlare Tunnel was disconnecting
3. macOS was putting the system to sleep

## Solution Applied

### 1. Web Server with Sleep Prevention
- Updated `manage.sh` to use `caffeinate -dims` when starting gunicorn
- This prevents the Mac from sleeping while the server is running
- Flags used:
  - `-d`: prevent display sleep
  - `-i`: prevent idle sleep  
  - `-m`: prevent disk sleep
  - `-s`: prevent system sleep
  - `-w <PID>`: watch the process and keep awake while it's running

### 2. CloudFlare Tunnel Auto-Start
- Created LaunchAgent: `~/Library/LaunchAgents/com.camping.cloudflared.plist`
- The tunnel now starts automatically when you log in
- It has `KeepAlive=true` so it restarts if it crashes
- Also protected with `caffeinate -dims` to prevent sleep

### 3. Configuration Files Created/Modified

#### CloudFlare Tunnel Config
**File:** `~/.cloudflared/config.yml`
```yaml
tunnel: 3d988e01-7dfd-4ed0-af0a-32b43fa03ed5
credentials-file: /Users/darshan/.cloudflared/3d988e01-7dfd-4ed0-af0a-32b43fa03ed5.json

ingress:
  - hostname: rerecreation.us
    service: http://localhost:5001
  - hostname: www.rerecreation.us
    service: http://localhost:5001
  - service: http_status:404
```

#### LaunchAgent for CloudFlare Tunnel
**File:** `~/Library/LaunchAgents/com.camping.cloudflared.plist`
- Automatically loads the tunnel on login
- Keeps it running with `KeepAlive`
- Logs to: `website/logs/cloudflared.log`

## Management Commands

### Check Status
```bash
cd ~/Projects/Camping_Reservation/website
./manage.sh status
```

### Start Web Server
```bash
./manage.sh start
```

### Stop Web Server
```bash
./manage.sh stop
```

### Restart Web Server
```bash
./manage.sh restart
```

### CloudFlare Tunnel Management

**Check if tunnel is running:**
```bash
launchctl list | grep cloudflared
```

**Stop tunnel:**
```bash
launchctl unload ~/Library/LaunchAgents/com.camping.cloudflared.plist
```

**Start tunnel:**
```bash
launchctl load ~/Library/LaunchAgents/com.camping.cloudflared.plist
```

**View tunnel logs:**
```bash
tail -f ~/Projects/Camping_Reservation/website/logs/cloudflared.log
```

## Current Status

✅ **Web Server:** Running on localhost:5001  
✅ **CloudFlare Tunnel:** Connected and routing traffic  
✅ **Public Website:** https://rerecreation.us  
✅ **Sleep Prevention:** Active via caffeinate  

## What Happens Now

1. **When you close the lid:**
   - Mac stays awake (connected to power)
   - Web server continues running
   - CloudFlare Tunnel stays connected
   - Website remains accessible at rerecreation.us

2. **When you restart your Mac:**
   - CloudFlare Tunnel auto-starts (via LaunchAgent)
   - Web server needs manual start: `./manage.sh start`
   - (Or add web server to LaunchAgent too if desired)

3. **When you disconnect power:**
   - Mac will eventually sleep to preserve battery
   - Website will go offline
   - Reconnect power and run `./manage.sh status` to check

## Monitoring

Check if everything is running:
```bash
cd ~/Projects/Camping_Reservation/website
./manage.sh status
```

Expected output:
```
✅ Web Server is running (PID: XXXX)
✅ CloudFlare Tunnel is running (PID: XXXX)
✅ Caffeinate processes active: 2 (preventing sleep)
```

## Testing

To test if it works when the lid is closed:
1. Run `./manage.sh status` - confirm everything is running
2. Close your MacBook lid
3. From another device, visit https://rerecreation.us
4. It should load normally! ✨

## Troubleshooting

### Website returns 522 error
```bash
# Check if tunnel is connected
cloudflared tunnel info camping-site

# If no connections, restart tunnel
launchctl unload ~/Library/LaunchAgents/com.camping.cloudflared.plist
launchctl load ~/Library/LaunchAgents/com.camping.cloudflared.plist
```

### Web server not responding
```bash
cd ~/Projects/Camping_Reservation/website
./manage.sh restart
```

### Mac is still sleeping
```bash
# Check power management settings
pmset -g

# Check if caffeinate is running
ps aux | grep caffeinate
```

## Files Modified/Created

1. `website/manage.sh` - Updated with caffeinate support
2. `~/.cloudflared/config.yml` - Created tunnel configuration
3. `~/Library/LaunchAgents/com.camping.cloudflared.plist` - Created LaunchAgent
4. `website/caffeinate.pid` - Tracks caffeinate process for web server
5. `website/caffeinate-cloudflared.pid` - Tracks caffeinate for tunnel

## Important Notes

- Keep your Mac connected to power for continuous operation
- The LaunchAgent will auto-start the tunnel on login
- The web server must be manually started after reboot
- Both services are protected by caffeinate to prevent sleep
- The public domain (rerecreation.us) routes through CloudFlare's network

---

**Created:** October 20, 2025  
**Last Updated:** October 20, 2025

