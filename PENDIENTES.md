# Estado y pendientes — iita-gsc-mcp

> Última actualización: 2026-08-04. Este documento es el handoff del trabajo de
> autenticación. Léelo antes de tocar el deploy.

Servicio en Railway: **MCP Google Search Console** → `web-production-f5d12.up.railway.app`
(el mapeo dominio→servicio se confirma en Settings → Networking → Public Networking).

---

## Estado actual (verificado 2026-08-04)

**El código de autenticación está mergeado en `main`, pero el deploy está CAÍDO (502) y
el servicio NO cierra el agujero todavía.**

- ✅ Código de auth mergeado (PR #1) y pin `mcp[cli]<2.0.0` mergeado (PR #2).
- ✅ Imagen de `main` reconstruida en Docker: **con** `MCP_AUTH_TOKEN` arranca, `/health`=200,
  `/mcp` sin credencial=401. **Sin** el token sale con `ConfigError` y no arranca.
- ❌ Producción devuelve **502** en `/health` y `/mcp`. Reproduce exactamente el `ConfigError`
  de token faltante: **el proceso no está viendo `MCP_AUTH_TOKEN` en runtime**.

**Traducción:** el código funciona; falta que la variable llegue al proceso en Railway.
Mientras está en 502, el servicio no le responde a nadie (no hay datos expuestos en este
instante, pero tampoco funciona).

---

## PENDIENTE #1 — hacer que arranque (bloqueante, en Railway)

Causa raíz confirmada: falta `MCP_AUTH_TOKEN` en runtime. Causa probable (sin confirmar,
requiere mirar el dashboard): la variable quedó **staged sin aplicar**, o hay un **typo en
el nombre**, o se cargó en el **environment/scope equivocado**.

Diagnóstico: en Railway → pestaña **Deployments** → último deploy → **Deploy Logs**.
- Si dice `ConfigError: Missing required env var: MCP_AUTH_TOKEN` → es el token.
- Si dice `Uvicorn running` pero el deploy figura unhealthy → es el healthcheck (ver #3).

Arreglo: en **Variables**, confirmar que existe `MCP_AUTH_TOKEN` (nombre exacto, sin
espacios ni guiones); si hay un cartel de cambios sin desplegar, clickear **Deploy**; si ya
está aplicada, **Redeploy** del último deployment.

## PENDIENTE #2 — decidir el modo de auth (decisión, define si cierra de fondo)

El modo por defecto (**OAuth**) NO cierra el agujero contra un actor motivado. Verificado el
2026-08-04 reproduciendo el bypass: un cliente que solo conoce la URL (nunca el
`MCP_AUTH_TOKEN`) corre el flujo OAuth (`/oauth/register` → `/authorize` → `/token`) y obtiene
un token válido, porque `/authorize` auto-consiente sin autenticar al dueño. En modo OAuth el
`MCP_AUTH_TOKEN` solo firma los JWT internos, no controla acceso.

- Para **cerrar de fondo**: agregar `MCP_OAUTH_ENABLED=false` (modo url-token). Sin el token
  → 401. El cliente se conecta con `.../mcp/<TOKEN>`. Costo: el token va en la URL del
  `.mcp.json` (ver #4).
- Para **aceptar la limitación**: dejar el default OAuth. Cierra el drive-by casual (curl a
  `/mcp` → 401), no al actor motivado.

Recomendación: **url-token**, porque el objetivo declarado era cerrar el acceso.

## PENDIENTE #3 — verificar el Healthcheck Path (antes de dar por cerrado)

En Railway → Settings → Healthcheck. Si el path es `/mcp`, con la auth activa el healthcheck
recibe 401, Railway marca el deploy unhealthy y puede dejar la versión vieja (sin auth)
sirviendo, o crash-loopear. Debe ser `/health`, `/` o vacío (todos públicos → 200).

## PENDIENTE #4 — actualizar `.mcp.json` de IITA-MARKETING

En `C:\Users\violl\IITA-MARKETING\.mcp.json`, entrada `iita-gsc`.
- Si modo **url-token**: cambiar la URL a `.../mcp/<TOKEN>`. Ojo: el token queda en el
  archivo (repo privado, pero es un secreto en git — no commitearlo en claro sin criterio).
- Si modo **OAuth**: no hace falta cambiar nada (mcp-remote negocia el flujo solo).

## Verificación de cierre (cuando #1–#3 estén)

`POST /mcp` sin credencial debe dar **401** (no 200 ni 502):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://web-production-f5d12.up.railway.app/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}'
```

Si elegiste url-token, además `GET /.well-known/oauth-protected-resource` debe dar **404**
(confirma que NO está en modo OAuth).

---

## Riesgos abiertos (del análisis adversarial 2026-08-04)

Cada uno con severidad; ninguno es bloqueante salvo #1 arriba.

- **R1 — OAuth default evitable (ALTO).** El bypass de #2. Se cierra con url-token, o con
  ~2-4h de trabajo agregando autenticación del owner en `/authorize` (habría que portarlo
  también a `IITA-googleads-mcp`, que comparte el patrón).
- **R2 — Healthcheck en `/mcp` (MEDIO, sin confirmar).** Ver #3. Candidato a "desplegamos y
  no cierra". Requiere mirar el dashboard.
- **R3 — SimpleBearerMiddleware allow-by-default en url-token (BAJO, latente).** No explotable
  hoy (solo `/mcp` es sensible; el resto da 404). Alinear a deny-by-default: ~15 min.
- **R4 — Authorization codes reutilizables dentro de 60s (BAJO).** Heredado de googleads.
  Solo relevante si se agrega owner-auth. ~30-60 min.
- **R5 — Sin mínimo de entropía en `MCP_AUTH_TOKEN` (BAJO).** Solo chequea no-vacío; un token
  de 64 ceros arrancaría. Exigir longitud/entropía mínima: ~10 min.
- **R6 — `MCP_OAUTH_ENABLED` con valor no reconocido cae a url-token (INFORMATIVO).** Fail-safe
  (no abre nada), pero cambia el contrato en silencio. Validar el valor: ~10 min.

## Hallazgo colateral (fuera de este repo)

`IITA-googleads-mcp` corre HOY en producción en modo OAuth → tiene el **mismo bypass R1**, y
ahí el dato es más sensible (campañas, presupuestos). No se tocó. Queda para evaluar aparte.
