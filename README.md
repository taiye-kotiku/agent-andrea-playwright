# Agent Andrea - Wegest Booking Service

Automated booking service for Wegest using Playwright and FastAPI.

## Features

- **Automated Booking**: Book appointments directly in Wegest
- **Availability Checking**: Check available time slots for any date
- **Session Management**: Pooled browser sessions for efficient operation
- **Service Catalog**: Automatic service and operator catalog management
- **Caching**: Availability cache for improved performance

## Project Structure

```
agent-andrea-playwright/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app initialization
│   ├── models.py            # Pydantic models
│   ├── core/
│   │   ├── config.py       # Configuration (pydantic BaseSettings)
│   │   ├── auth.py         # Authentication dependencies
│   │   └── session.py     # Session management
│   ├── routers/
│   │   ├── booking.py      # /api/book, /api/check-availability
│   │   ├── context.py      # Booking context endpoints
│   │   ├── session.py      # Session management endpoints
│   │   └── admin.py       # Admin endpoints (cache, catalog)
│   ├── services/
│   │   ├── wegest.py       # Playwright automation
│   │   ├── catalog.py      # Operator/service catalog
│   │   ├── cache.py        # Availability cache
│   │   └── call_state.py   # Conversation state tracking
│   └── utils/
│       ├── time_utils.py    # Time parsing utilities
│       └── helpers.py       # General helpers
├── requirements.txt
├── Dockerfile
├── .gitignore
└── .env.example
```

## Setup

### Prerequisites

- Python 3.10+
- pip

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd agent-andrea-playwright
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
playwright install chromium
```

4. Create `.env` file:
```bash
cp .env.example .env
# Edit .env with your settings
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_SECRET` | Secret key for API authentication | `changeme` |
| `POOL_SIZE` | Number of pooled browser sessions | `2` |
| `MAX_CONCURRENT_SESSIONS` | Max concurrent sessions | `3` |
| `DEBUG_SCREENSHOTS` | Enable debug screenshots | `false` |
| `PORT` | Application port | `8000` |

## Running the Application

### Development

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

## API Endpoints

### Authentication

All endpoints (except `/health`) require Bearer token authentication:
```
Authorization: Bearer <API_SECRET>
```

### Endpoints

#### `GET /health`
Health check endpoint.

#### `POST /api/book`
Book an appointment.
```json
{
  "customer_name": "John Doe",
  "caller_phone": "+1234567890",
  "services": ["taglio", "colore"],
  "operator_preference": "prima disponibile",
  "preferred_date": "2024-12-25",
  "preferred_time": "14:30",
  "conversation_id": "conv_123"
}
```

#### `POST /api/check-availability`
Check availability for a date.
```json
{
  "preferred_date": "2024-12-25",
  "operator_preference": "prima disponibile",
  "services": ["taglio"],
  "conversation_id": "conv_123"
}
```

#### `POST /api/update-booking-context`
Update booking context for a conversation.

#### `POST /api/get-booking-context`
Get current booking context.

#### `POST /api/check-booking-options`
Check booking options based on current context.

#### `POST /api/finalize-booking`
Finalize a booking.

#### `POST /api/prepare-live-session`
Prepare a live Wegest session.

#### `POST /api/get-service-duration`
Get service duration information.

#### `POST /api/invalidate-cache`
Invalidate availability cache for a date.

## Docker Deployment

```bash
docker build -t agent-andrea-playwright .
docker run -p 8000:8000 --env-file .env agent-andrea-playwright
```

Or using Docker Compose:
```bash
docker-compose up -d
```

## Background Tasks

The application runs several background tasks:
- **Call State Cleanup**: Removes expired conversation states (every 5 minutes)
- **Session Cleanup**: Cleans up idle Wegest sessions (every 5 minutes)
- **Pool Warming**: Warms up browser session pool on startup

## License

[Add your license here]
