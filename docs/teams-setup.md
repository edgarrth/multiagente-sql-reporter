# Microsoft Teams setup

# Recomendación de autenticación

La PoC usa el Microsoft 365 Agents SDK para validar las actividades enviadas por Azure Bot/Teams.
El identificador Entra (`aadObjectId`) de la actividad se transforma en un usuario externo del
backend. Esto autentica el canal y mantiene trazabilidad del usuario.

Cuando la autorización de datos dependa de la persona, implementar Teams SSO y On-Behalf-Of para
propagar la identidad a un gateway o plataforma que aplique RLS/CLS. No enviar el token de Teams al
LLM.

# Pasos

## 1. Registrar la aplicación Entra

1. Crear una App Registration en Microsoft Entra ID.
2. Guardar Application (client) ID, Directory (tenant) ID y un client secret.
3. Para SSO/OBO, exponer un API scope y configurar permisos/consentimiento corporativo.

## 2. Crear Azure Bot

1. Crear un recurso Azure Bot asociado al Application ID.
2. Habilitar el canal Microsoft Teams.
3. Configurar el messaging endpoint público:

```text
https://<host-publico>/api/messages
```

Para local usar Microsoft Dev Tunnels o un túnel HTTPS equivalente hacia el puerto 3978.

## 3. Variables

```dotenv
TEAMS_BOT_ID=<application-id>
TEAMS_BOT_PASSWORD=<client-secret>
TEAMS_TENANT_ID=<tenant-id>
INTERNAL_SERVICE_KEY=<same-value-used-by-api>
```

## 4. Generar el paquete

Reemplazar `${TEAMS_BOT_ID}` en `manifest.template.json`, agregar `outline.png` y `color.png`, y
comprimir únicamente los tres archivos del paquete.

## 5. Iniciar

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  --profile teams up --build -d
```

## 6. Cargar en Teams

Cargar el ZIP como aplicación personalizada en el tenant o publicarlo mediante el catálogo
organizacional. La plantilla usa alcance `personal`, que simplifica autenticación y HITL.

# Aislamiento de fallas

El adaptador solo llama al endpoint interno de FastAPI. No comparte proceso ni puerto con
Streamlit. Si Teams o Azure Bot falla, la UI web y el workflow principal siguen disponibles.
