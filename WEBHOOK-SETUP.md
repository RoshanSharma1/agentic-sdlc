# SDLC Webhook - 24/7 Setup Guide

**Real-Time GitHub Event Handling** — No polling, instant triggering via webhooks

## ✅ Current Status

**Webhook Server:** Running on port 8765 (managed by launchd)  
**Ngrok Tunnel:** Active  
**Public URL:** `https://cephalometric-venita-flowingly.ngrok-free.dev`

---

## 🔧 Service Management

### Check Status
```bash
./scripts/webhook-service.sh status
```

### View Live Logs
```bash
./scripts/webhook-service.sh logs
# or
tail -f ~/.sdlc/logs/webhook-server.log
```

### Restart Service
```bash
./scripts/webhook-service.sh restart
```

### Stop Service
```bash
./scripts/webhook-service.sh stop
```

---

## 🌐 Ngrok Management

### Check Ngrok Status
```bash
curl -s http://localhost:4040/api/tunnels | jq '.tunnels[0].public_url'
# or visit: http://localhost:4040
```

### Restart Ngrok
```bash
kill $(cat ~/.sdlc/ngrok.pid)
./scripts/start-ngrok.sh
```

### Get Current Public URL
```bash
./scripts/start-ngrok.sh  # Will show current URL if already running
```

---

## 🔐 GitHub Webhook Configuration

1. Go to your GitHub repository
2. Navigate to: **Settings → Webhooks → Add webhook**

Configure with these values:

| Field | Value |
|-------|-------|
| **Payload URL** | `https://cephalometric-venita-flowingly.ngrok-free.dev/webhook` |
| **Content type** | `application/json` |
| **Secret** | `fb927da571c5599a79ad0912171869d32a11f4af3cd3e0942800196422364a75` |
| **SSL verification** | Enable SSL verification |
| **Events** | Select individual events: |
|  | ✓ Pull requests |
|  | ✓ Pull request reviews |

3. Click **Add webhook**
4. Test it by approving a PR!

---

## 📋 Architecture

```
GitHub PR Approved
  ↓
GitHub webhook fires → https://*.ngrok-free.dev/webhook
  ↓
Ngrok forwards → localhost:8765/webhook
  ↓
SDLC webhook server (launchd service)
  ↓
record_approval_event()
  ↓
EventBus publishes APPROVAL_RECEIVED
  ↓
OrchestratorRuntime spawns next phase agent
  ↓
Agent runs autonomously
```

---

## 🚀 How It Works

### When You Approve a PR:

1. **GitHub** sends webhook to ngrok URL
2. **Webhook server** verifies signature and extracts event data
3. **Orchestrator** records approval event in SQLite
4. **EventBus** publishes `APPROVAL_RECEIVED` event
5. **Runtime** automatically spawns the next phase agent
6. **Agent** runs in headless mode to complete the next phase

### No Action Required!
Once configured, the system is fully autonomous. Just approve PRs on GitHub and the agents will automatically advance through phases.

---

## 📊 Monitoring

### View Webhook Requests
```bash
# Live webhook logs
tail -f ~/.sdlc/logs/webhook-server.log

# Ngrok dashboard (shows all requests)
open http://localhost:4040
```

### Check Recent Approvals
```bash
# Check backend database
sqlite3 ~/.sdlc/backend.sqlite3 "SELECT * FROM approval_events ORDER BY received_at DESC LIMIT 5;"
```

### Check Agent Runs
```bash
# Recent agent executions
sqlite3 ~/.sdlc/backend.sqlite3 "SELECT started_at, agent_name, skill, status FROM agent_runs ORDER BY started_at DESC LIMIT 5;"
```

---

## 🔄 Auto-Start on Login

The webhook service is configured to **auto-start when you log in** via launchd.

To disable auto-start:
```bash
./scripts/webhook-service.sh disable
```

To re-enable:
```bash
./scripts/webhook-service.sh enable
```

---

## ⚠️ Important Notes

### Ngrok URL Changes
- Free ngrok tunnels get a **new random URL on each restart**
- You'll need to **update GitHub webhook URL** if you restart ngrok
- Consider upgrading to ngrok paid plan for a static domain

### Keep Machine Awake
For true 24/7 operation on macOS:
```bash
# Prevent sleep when plugged in
sudo pmset -c sleep 0
sudo pmset -c displaysleep 10

# Or use caffeinate
caffeinate -s &  # Prevents sleep
```

### Network Changes
If your IP changes or network restarts:
```bash
# Restart both services
./scripts/webhook-service.sh restart
./scripts/start-ngrok.sh
```

---

## 🧪 Testing the Setup

### Test 1: Health Check
```bash
curl http://localhost:8765/health
# Expected: ok
```

### Test 2: Webhook Endpoint (Local)
```bash
curl -X POST http://localhost:8765/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"test"}'
# Expected: ok
```

### Test 3: Via Ngrok
```bash
curl -X POST https://cephalometric-venita-flowingly.ngrok-free.dev/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"test"}'
# Expected: ok
```

### Test 4: GitHub Webhook
1. Go to GitHub webhook settings
2. Click on your webhook
3. Scroll to "Recent Deliveries"
4. Click "Redeliver" on any past event
5. Check logs: `tail -f ~/.sdlc/logs/webhook-server.log`

---

## 📁 File Locations

| Item | Location |
|------|----------|
| Webhook secret | `~/.sdlc/webhook-secret.txt` |
| Webhook logs | `~/.sdlc/logs/webhook-server.log` |
| Ngrok logs | `~/.sdlc/logs/ngrok.log` |
| Ngrok PID | `~/.sdlc/ngrok.pid` |
| Service plist | `~/Library/LaunchAgents/com.sdlc.webhook.plist` |
| Backend DB | `~/.sdlc/backend.sqlite3` |

---

## 🆘 Troubleshooting

### Webhook not receiving events
```bash
# 1. Check service is running
./scripts/webhook-service.sh status

# 2. Check ngrok is active
curl http://localhost:4040/api/tunnels

# 3. Check GitHub webhook deliveries
# Go to: GitHub repo → Settings → Webhooks → Recent Deliveries

# 4. Check logs for errors
tail -50 ~/.sdlc/logs/webhook-server.log
```

### Port already in use
```bash
# Find what's using port 8765
lsof -i :8765

# Kill it
kill $(lsof -t -i :8765)

# Restart service
./scripts/webhook-service.sh restart
```

### Ngrok connection failed
```bash
# Check ngrok auth
ngrok config check

# Restart ngrok
kill $(cat ~/.sdlc/ngrok.pid)
./scripts/start-ngrok.sh
```

---

## 🎯 Next Steps

1. ✅ Configure GitHub webhook (see section above)
2. ✅ Test by approving a PR
3. ✅ Monitor logs to see automatic agent dispatch
4. Consider: Upgrade to ngrok paid plan for static URL
5. Consider: Keep machine awake 24/7 (see notes above)

---

**Setup Date:** 2026-04-26  
**Webhook Secret:** `fb927da571c5599a79ad0912171869d32a11f4af3cd3e0942800196422364a75`  
**Ngrok URL:** `https://cephalometric-venita-flowingly.ngrok-free.dev/webhook`
