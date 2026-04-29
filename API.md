# API Documentation - Agent Andrea Wegest Booking Service

Complete API reference for developers and agents.

## Base URL

**Production**: `https://agent-andrea.srv1252881.hstgr.cloud`
**Local**: `http://localhost:8000`

## Authentication

All endpoints (except `/health`) require Bearer token authentication:

```http
Authorization: Bearer <API_SECRET>
```

**Get API Secret**: Check `/opt/agent-andrea-playwright/.env` file.

---

## Endpoints

### Health Check

#### `GET /health`

Check service health status.

**Response**:
```json
{
  "status": "ok",
  "service": "Agent Andrea Wegest Booking",
  "version": "2.0.0"
}
```

---

### Booking Endpoints

#### `POST /api/book`

Book an appointment in Wegest.

**Request Body**:
```json
{
  "customer_name": "Mario Rossi",
  "caller_phone": "+391234567890",
  "services": ["taglio", "colore"],
  "service": null,
  "operator_preference": "prima disponibile",
  "preferred_date": "2026-04-30",
  "preferred_time": "14:30",
  "conversation_id": "conv_123"
}
```

**Parameters**:
- `customer_name` (required): Customer full name
- `caller_phone` (required): Phone number
- `services` (optional): List of service names
- `service` (optional): Single service (deprecated, use services instead)
- `operator_preference` (optional): "prima disponibile" or operator name
- `preferred_date` (required): Date in YYYY-MM-DD format
- `preferred_time` (required): Time in HH:MM format
- `conversation_id` (required): Unique conversation/session ID

**Response**:
```json
{
  "success": true,
  "booking_id": "12345",
  "message": "Appointment booked successfully",
  "details": {}
}
```

---

#### `POST /api/check-availability`

Check available time slots for a specific date.

**Request Body**:
```json
{
  "preferred_date": "2026-04-30",
  "operator_preference": "prima disponibile",
  "services": ["taglio", "colore"],
  "service": null,
  "conversation_id": "conv_123"
}
```

**Response**:
```json
{
  "date": "2026-04-30",
  "day_name": "Tuesday",
  "is_open": true,
  "requested_services": ["taglio", "colore"],
  "required_operator_minutes": 55,
  "operators": [
    {
      "name": "Mario",
      "id": "1",
      "present": true,
      "available_slots": ["09:00", "09:15", "..."],
      "valid_start_times": ["09:00", "09:30", "..."],
      "occupied_slots": ["10:00", "10:15"],
      "total_available": 20,
      "total_occupied": 5
    }
  ],
  "all_available_times": ["09:00", "09:15", "..."],
  "all_valid_start_times": ["09:00", "09:30", "..."],
  "total_available_slots": 20,
  "total_valid_start_times": 15,
  "total_operators_present": 3,
  "summary": "✅ 15 orari di inizio validi per taglio, colore con 3 operatori...",
  "active_operators": [
    {"name": "Mario", "id": "1", "present": true}
  ]
}
```

---

#### `POST /api/finalize-booking`

Finalize a booking after all context is collected.

**Request Body**:
```json
{
  "conversation_id": "conv_123"
}
```

**Response**:
```json
{
  "success": true,
  "conversation_id": "conv_123",
  "message": "Appointment booked successfully",
  "booking_result": {},
  "next_action": "booking_complete"
}
```

---

### Context Management Endpoints

#### `POST /api/update-booking-context`

Update booking context for a conversation (partial updates supported).

**Request Body**:
```json
{
  "conversation_id": "conv_123",
  "services": ["taglio"],
  "operator_preference": "Mario",
  "preferred_date": "2026-04-30",
  "preferred_time": "14:30",
  "customer_name": "Mario Rossi",
  "caller_phone": "+391234567890"
}
```

**Response**:
```json
{
  "success": true,
  "conversation_id": "conv_123",
  "booking_context": {
    "conversation_id": "conv_123",
    "services": ["taglio"],
    "operator_preference": "Mario",
    "preferred_date": "2026-04-30",
    "preferred_time": "14:30",
    "customer_name": "Mario Rossi",
    "caller_phone": "+391234567890",
    "last_availability_result": {},
    "booking_confirmed": false,
    "updated_at": "2026-04-29T20:00:00"
  },
  "missing_fields": [],
  "next_action": "ready_for_availability_or_confirmation"
}
```

---

#### `POST /api/get-booking-context`

Get current booking context for a conversation.

**Request Body**:
```json
{
  "conversation_id": "conv_123"
}
```

**Response**: Same as `update-booking-context` response structure.

---

#### `POST /api/check-booking-options`

Check booking options based on current context (returns operator suggestions, time suggestions).

**Request Body**:
```json
{
  "conversation_id": "conv_123"
}
```

**Response**:
```json
{
  "success": true,
  "conversation_id": "conv_123",
  "booking_context": {},
  "availability": {},
  "operators_available_at_requested_time": [
    {"name": "Mario", "id": "1", "time": "14:30", "delta_minutes": 0}
  ],
  "closest_operator_options": [
    {"name": "Luigi", "id": "2", "time": "14:45", "delta_minutes": 15}
  ],
  "spoken_summary_it": "Alle 14:30 sono disponibili Mario. Vuoi prenotare?",
  "spoken_summary_en": "At 14:30, Mario is available. Would you like to book?",
  "next_action": "choose_operator_or_confirm_time"
}
```

---

### Session Management Endpoints

#### `POST /api/prepare-live-session`

Prepare a live Wegest browser session for a conversation.

**Request Body**:
```json
{
  "conversation_id": "conv_123"
}
```

**Response**:
```json
{
  "success": true,
  "conversation_id": "conv_123",
  "session_ready": true,
  "message": "Live Wegest session is ready"
}
```

---

### Admin Endpoints

#### `POST /api/get-service-duration`

Get duration information for services.

**Request Body**:
```json
{
  "service": "taglio",
  "services": ["taglio", "colore"]
}
```

**Response**:
```json
{
  "success": true,
  "services": [
    {
      "requested_service": "taglio",
      "resolved_service": "taglio",
      "tempo_operatore": 25,
      "tempo_cliente": 30
    }
  ],
  "spoken_summary_it": "Il servizio taglio richiede circa 25 minuti...",
  "spoken_summary_en": "The service taglio requires about 25 minutes..."
}
```

---

#### `POST /api/invalidate-cache`

Invalidate availability cache for a specific date.

**Request Body**:
```json
{
  "preferred_date": "2026-04-30"
}
```

**Response**:
```json
{
  "ok": true,
  "invalidated": "2026-04-30"
}
```

---

## Booking Flow Example

1. **Prepare session**: `POST /api/prepare-live-session`
2. **Update context**: `POST /api/update-booking-context` (add date, services)
3. **Check availability**: `POST /api/check-availability`
4. **Check options**: `POST /api/check-booking-options`
5. **Update context**: `POST /api/update-booking-context` (add time, customer info)
6. **Finalize**: `POST /api/finalize-booking`

---

## Error Responses

All endpoints return appropriate HTTP status codes:

- `401 Unauthorized`: Invalid/missing API token
- `400 Bad Request`: Missing required fields
- `500 Internal Server Error`: Service errors

**Error Response Format**:
```json
{
  "detail": "Unauthorized"
}
```

---

## Service Durations (Fallback)

If service catalog is unavailable, these fallback durations are used:

| Service | Duration (min) |
|---------|----------------|
| colore | 30 |
| taglio | 25 |
| piega donna | 35 |
| filler | 15 |
| shampoo | 10 |
| manicure | 65 |
| ... | ... |

---

## Rate Limits

Currently no rate limiting is implemented. Consider adding rate limiting for production use.

## Support

For issues, check logs at: `/opt/agent-andrea-playwright/logs/app.log`

Service management:
```bash
systemctl status agent-andrea
systemctl restart agent-andrea
```
