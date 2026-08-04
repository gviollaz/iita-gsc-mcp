# IITA Google Search Console MCP Server

MCP server providing Search Console data for SEO analysis of iita.com.ar.

---

> ⚠️ **El deploy está caído (502) al 2026-08-04 y la autenticación NO está activa.**
> Estado real, causa diagnosticada y pasos pendientes: **[PENDIENTES.md](PENDIENTES.md)**.
> Leelo antes de tocar el deploy.

## ⚠️ Autenticación (cambió en v0.2.0)

Hasta la v0.1 este servidor **respondía sin autenticación**: cualquiera que
conociera la URL de Railway podía hacer `tools/list` y leer los datos de Search
Console de iita.com.ar (consultas, páginas, países, sitemaps). Verificado el
2026-08-03 con un `tools/call` sin credenciales que devolvió datos reales.

Desde la v0.2.0 hay dos modos, el mismo patrón que `iita-googleads`:

| Modo | `MCP_OAUTH_ENABLED` | Cómo se conecta |
|---|---|---|
| **OAuth 2.1** (default) | `true` | `https://<host>/mcp` — el cliente negocia RFC 9728 + 8414 + 7591 + PKCE |
| **URL-token** | `false` | `https://<host>/mcp/<MCP_AUTH_TOKEN>` o header `Authorization: Bearer <MCP_AUTH_TOKEN>` |

`MCP_AUTH_TOKEN` es **obligatorio en los dos modos**. Sin esa variable el
servidor **no arranca** — arrancar sin autenticación es justamente el agujero
que esta versión cierra.

Públicos (sin token): `/`, `/health` y los endpoints de descubrimiento OAuth.
Todo lo demás, incluido `/mcp`, exige credencial.

### Orden de despliegue (importante)

1. Poner `MCP_AUTH_TOKEN` en el servicio de Railway (`openssl rand -hex 32`).
2. Recién entonces desplegar esta versión. Al revés el servicio queda caído.
3. Actualizar el cliente MCP (ver abajo).

### Conectar el cliente

Modo OAuth (default), en `.mcp.json`:

```json
"iita-gsc": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://web-production-f5d12.up.railway.app/mcp"]
}
```

Modo URL-token (`MCP_OAUTH_ENABLED=false`), para clientes que no hacen OAuth:

```json
"iita-gsc": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://web-production-f5d12.up.railway.app/mcp/EL_TOKEN"]
}
```

### Verificar que la autenticación quedó activa

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://web-production-f5d12.up.railway.app/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}'
```

Tiene que devolver **401**. Si devuelve 200, la autenticación no está activa.
`/health` no sirve para verificar esto: es público a propósito.

---

## Entry point

`server.py` es el único punto de entrada soportado — es el que agrega la
autenticación. `main.py` solo define las tools; ejecutarlo directo aborta con un
mensaje, porque servía `/mcp` sin credenciales.

```bash
python server.py          # o: uvicorn server:app --host 0.0.0.0 --port 8080
```

## Tools (7)

| Tool | Description |
|------|-------------|
| `gsc_search_queries` | Top queries with clicks, impressions, CTR, position |
| `gsc_search_pages` | Top pages by search performance |
| `gsc_queries_by_page` | Queries driving traffic to a specific page |
| `gsc_daily_trend` | Daily search performance trend |
| `gsc_device_breakdown` | Mobile vs Desktop vs Tablet |
| `gsc_country_breakdown` | Traffic by country |
| `gsc_sitemaps` | Sitemap status |

## Env Vars

Plantilla completa y comentada: [`.env.example`](.env.example).

```
MCP_AUTH_TOKEN=...          # obligatorio, sin esto no arranca
MCP_OAUTH_ENABLED=true      # false → modo URL-token
GSC_SITE_URL=https://iita.com.ar/
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
PORT=8080
```
