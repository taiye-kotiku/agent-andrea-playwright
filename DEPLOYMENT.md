# Deployment Guide - Agent Andrea Wegest Booking Service

Complete guide for deploying and managing the service.

## Architecture

```
Client → Traefik (HTTPS/443) → Agent Andrea Service (port 8000)
                 ↓
          Let's Encrypt (SSL certificates)
```

## Server Details

- **Host**: `srv1252881.hstgr.cloud`
- **IP**: `46.202.170.95`
- **OS**: Ubuntu 24.04.3 LTS
- **Domain**: `agent-andrea.srv1252881.hstgr.cloud`
- **Reverse Proxy**: Traefik (Docker container)
- **SSL**: Let's Encrypt (auto-renewal)

---

## File Locations

| Component | Path |
|-----------|------|
| Application | `/opt/agent-andrea-playwright/` |
| Virtual Environment | `/opt/agent-andrea-playwright/venv/` |
| Configuration | `/opt/agent-andrea-playwright/.env` |
| Logs | `/opt/agent-andrea-playwright/logs/app.log` |
| Systemd Service | `/etc/systemd/system/agent-andrea.service` |
| Traefik Config | `/etc/traefik/conf.d/agent-andrea.yml` |
| Docker Compose | `/docker/n8n/docker-compose.yml` |

---

## Environment Variables

Configuration in `/opt/agent-andrea-playwright/.env`:

```bash
# API Security (CHANGE THIS!)
API_SECRET=22cc11433c8f4e48dfa0da5c38207b89e39441ad5c5592388804a1f291239769

# Session Management
POOL_SIZE=2
MAX_CONCURRENT_SESSIONS=3
SESSION_IDLE_TTL_SECONDS=900
CALL_STATE_TTL_SECONDS=3600

# Application
DEBUG_SCREENSHOTS=false
PORT=8000
HOST=0.0.0.0

# Playwright
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TIMEOUT=30000
```

**⚠️ Security**: Never commit `.env` file to Git. Use `.env.example` as template.

---

## Service Management

### Systemd Service

The application runs as a systemd service:

```bash
# Check status
systemctl status agent-andrea

# Start service
systemctl start agent-andrea

# Stop service
systemctl stop agent-andrea

# Restart service
systemctl restart agent-andrea

# Enable on boot
systemctl enable agent-andrea

# Disable on boot
systemctl disable agent-andrea

# View logs
journalctl -u agent-andrea -f
# OR
tail -f /opt/agent-andrea-playwright/logs/app.log
```

---

## Traefik Configuration

### Static Configuration (via Docker Compose)

Traefik is configured in `/docker/n8n/docker-compose.yml`:

```yaml
services:
  traefik:
    image: "traefik"
    command:
      - "--providers.file.directory=/etc/traefik/conf.d"
      - "--providers.file.watch=true"
      # ... other config
    volumes:
      - /etc/traefik/conf.d:/etc/traefik/conf.d:ro
```

### Dynamic Configuration

File: `/etc/traefik/conf.d/agent-andrea.yml`

```yaml
http:
  routers:
    agent-andrea:
      rule: "Host(`agent-andrea.srv1252881.hstgr.cloud`)"
      entrypoints:
        - websecure
      tls:
        certresolver: mytlschallenge
      service: agent-andrea-svc
      priority: 10

  services:
    agent-andrea-svc:
      loadbalancer:
        servers:
          - url: "http://172.18.0.1:8000"
```

**To apply changes**:
```bash
# No restart needed - Traefik watches for changes
# But if you modify docker-compose.yml:
cd /docker/n8n
docker compose up -d traefik
```

---

## SSL Certificates

Certificates are managed by Traefik with Let's Encrypt:

```bash
# View certificate storage
docker exec n8n-traefik-1 cat /letsencrypt/acme.json

# Check certificate expiry
echo | openssl s_client -connect agent-andrea.srv1252881.hstgr.cloud:443 2>/dev/null | openssl x509 -noout -dates

# Force renewal (if needed)
docker exec n8n-traefik-1 traefik --certificatesresolvers.mytlschallenge.acme.storage=/letsencrypt/acme.json
```

---

## Logs

### Application Logs

```bash
# Systemd logs
journalctl -u agent-andrea -n 100

# Application log file
tail -f /opt/agent-andrea-playwright/logs/app.log

# All logs
tail -f /opt/agent-andrea-playwright/logs/app.log | grep ERROR
```

### Traefik Logs

```bash
docker logs n8n-traefik-1 --tail 100
docker logs n8n-traefik-1 --follow
```

---

## Updating the Application

### Pull Latest Changes

```bash
cd /opt/agent-andrea-playwright
git pull origin main
```

### Update Dependencies

```bash
cd /opt/agent-andrea-playwright
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Restart Service

```bash
systemctl restart agent-andrea
systemctl status agent-andrea
```

---

## Backup

### Important Files to Backup

```bash
# Application code
/opt/agent-andrea-playwright/

# Environment (contains secrets!)
/opt/agent-andrea-playwright/.env

# Traefik config
/etc/traefik/conf.d/agent-andrea.yml

# SSL certificates (inside Docker volume)
docker volume inspect traefik_data
```

### Backup Command

```bash
# Create backup
tar -czf /backup/agent-andrea-$(date +%Y%m%d).tar.gz \
  /opt/agent-andrea-playwright/ \
  /etc/traefik/conf.d/agent-andrea.yml \
  /etc/systemd/system/agent-andrea.service

# Restore from backup
tar -xzf /backup/agent-andrea-20260429.tar.gz -C /
systemctl daemon-reload
systemctl restart agent-andrea
```

---

## Monitoring

### Health Check

```bash
# Local
curl http://localhost:8000/health

# Public
curl https://agent-andrea.srv1252881.hstgr.cloud/health
```

### Process Status

```bash
# Check if running
ps aux | grep "app.main"

# Check port
ss -tlnp | grep 8000
```

---

## Troubleshooting

### Service Won't Start

```bash
# Check logs
journalctl -u agent-andrea -n 50 --no-pager

# Check configuration
cd /opt/agent-andrea-playwright
source venv/bin/activate
python -c "from app.main import app; print('OK')"

# Check .env file
cat /opt/agent-andrea-playwright/.env
```

### SSL Certificate Issues

```bash
# Check Traefik logs
docker logs n8n-traefik-1 --tail 100 | grep -i "challenge\|cert\|error"

# Verify DNS
dig agent-andrea.srv1252881.hstgr.cloud

# Test locally
curl -k https://agent-andrea.srv1252881.hstgr.cloud/health
```

### Port Already in Use

```bash
# Find process using port 8000
lsof -i :8000
# OR
ss -tlnp | grep 8000

# Kill process
kill -9 <PID>
systemctl start agent-andrea
```

---

## Security Recommendations

1. **Change API_SECRET** periodically
2. **Enable firewall**: `ufw enable` (allow ports 80, 443, 22)
3. **Update system**: `apt update && apt upgrade`
4. **Monitor logs** for suspicious activity
5. **Backup regularly**
6. **Use strong API_SECRET** (already done - 64 hex chars)

---

## Performance Tuning

### Increase Pool Size

Edit `.env`:
```bash
POOL_SIZE=5  # Increase from 2 to 5
```

Then restart: `systemctl restart agent-andrea`

### Enable Caching

Caching is enabled by default. Cache file: `availability_cache.json`

Clear cache:
```bash
rm /opt/agent-andrea-playwright/availability_cache.json
systemctl restart agent-andrea
```

---

## Handoff Checklist

When handing off to another agent/developer:

- [ ] Provide `.env` file (or `.env.example` + secrets separately)
- [ ] Share this documentation
- [ ] Grant SSH access to server
- [ ] Provide GitHub repository access
- [ ] Share Traefik configuration details
- [ ] Document any custom configurations
- [ ] Test deployment after handoff

---

## Quick Reference

| Task | Command |
|------|---------|
| Health check | `curl https://agent-andrea.srv1252881.hstgr.cloud/health` |
| Restart service | `systemctl restart agent-andrea` |
| View logs | `tail -f /opt/agent-andrea-playwright/logs/app.log` |
| Update code | `git pull && systemctl restart agent-andrea` |
| Check status | `systemctl status agent-andrea` |
