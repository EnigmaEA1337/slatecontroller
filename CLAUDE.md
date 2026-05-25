# Slate Controller

> Application Docker pour piloter et personnaliser un routeur GL.iNet Slate 7 Pro (GL-BE10000) via son API JSON-RPC.

## Contexte projet

Ce projet vise à construire une **interface web autohébergée** (déployable en Docker sur un host Linux distant) qui pilote un routeur **GL.iNet Slate 7 Pro** via son API JSON-RPC interne.

Le routeur est utilisé dans un contexte mobile (mission, voyage, vacances, backup) avec plusieurs **profils contextuels** à activer en un clic. Le but est de centraliser le management du Slate (et plus tard d'autres équipements GL.iNet comme le Mudi 7) dans une UI moderne et reproductible.

### Cas d'usage cibles

- **Activation rapide de profils contextuels** (Mission / Vacances / OSINT / Home / Lockdown) qui reconfigurent simultanément le VPN actif, le DNS, les SSID, le firewall, AdGuard, Tor, Tailscale.
- **Pilotage à distance** du Slate via Tailscale depuis n'importe quel device.
- **Visualisation de l'état temps réel** (CPU, RAM, VPN actif, clients connectés, débit).
- **Gestion centralisée des VPN** (WireGuard ProtonVPN, OpenVPN, VPN corporate).
- **Versioning Git** de toutes les configurations.
- **Logs centralisés** vers un SIEM externe (Splunk HEC, Security Onion).
- **Évolution future** : multi-équipements (Slate 7 Pro + Mudi 7), rotation auto VPN, intégration Homey Pro, mode lockdown durci.

## Architecture cible

```
┌─────────────────────────────────────────────────┐
│  HOST LINUX (Docker)                            │
│  ├── slate-backend (FastAPI + python-glinet)    │
│  ├── slate-frontend (React + Vite + Tailwind)   │
│  ├── slate-scheduler (APScheduler)              │
│  ├── slate-db (SQLite ou PostgreSQL)            │
│  └── traefik (reverse proxy + TLS)              │
└─────────────┬───────────────────────────────────┘
              │ HTTPS + Tailscale
              │ API JSON-RPC GL.iNet
              ▼
┌─────────────────────────────────────────────────┐
│  SLATE 7 PRO (GL-BE10000)                       │
│  - Reçoit commandes via /rpc                    │
│  - Exécute changements                          │
│  - Renvoie état                                 │
└─────────────────────────────────────────────────┘
```

## Stack technique imposée

### Backend

- **Python 3.12** (typage moderne, async natif)
- **FastAPI** (async, OpenAPI auto-doc, validation Pydantic)
- **python-glinet** (https://github.com/tomtana/python-glinet) — wrapper API JSON-RPC GL.iNet officiel communautaire
- **httpx** pour les appels HTTP async
- **pydantic v2** pour la validation et les modèles
- **SQLAlchemy 2.0** (async) + **SQLite** par défaut (PostgreSQL en option)
- **APScheduler** pour les tâches planifiées
- **structlog** pour les logs structurés JSON
- **pytest** + **pytest-asyncio** pour les tests

### Frontend

- **React 18** + **TypeScript** + **Vite**
- **TailwindCSS 4** pour le style
- **shadcn/ui** pour les composants
- **TanStack Query** (React Query) pour la gestion d'état serveur
- **Axios** pour le client HTTP
- **Lucide React** pour les icônes
- **React Router** v6 pour la navigation

### Infrastructure

- **Docker** + **Docker Compose** v2
- **Traefik v3** pour le reverse proxy et les certificats Let's Encrypt
- **Variables d'environnement** via `.env` (jamais committé)
- **Healthchecks** Docker sur tous les services

## Standards et conventions

### Code Python

- **PEP 8** strict, formaté avec **ruff** (qui remplace black/isort/flake8)
- **Type hints partout**, vérifiés avec **mypy** en mode strict
- **Docstrings Google style** pour toutes les fonctions publiques
- **Async/await** systématique pour les I/O
- **Nommage** : snake_case pour variables/fonctions, PascalCase pour classes
- **Imports** : groupés (stdlib / third-party / local) et triés par ruff

### Code TypeScript

- **TypeScript strict mode** activé
- **ESLint** + **Prettier** configurés
- **Composants fonctionnels** uniquement (pas de classes)
- **Hooks personnalisés** dans `src/hooks/`
- **Types/interfaces** dans `src/types/`
- **Nommage** : camelCase pour variables/fonctions, PascalCase pour composants/types

### Git

- **Commits conventionnels** (Conventional Commits)
  - `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`
- **Branches** : `main`, `develop`, puis `feature/xxx`, `fix/xxx`
- **Pas de commit direct sur `main`**, toujours via PR
- **.gitignore** exhaustif (secrets, node_modules, __pycache__, .env, db, logs)

### Sécurité

- **Aucun secret committé** : utiliser `.env` + `.env.example`
- **Mot de passe Slate** stocké chiffré ou via secret manager
- **Validation stricte** de tous les inputs côté backend (Pydantic)
- **CORS** restrictif (whitelist explicite)
- **Authentification** sur l'UI obligatoire (JWT minimal au début, OIDC plus tard)
- **HTTPS uniquement** en production (Traefik + Let's Encrypt)
- **Rate limiting** sur les endpoints sensibles

## Arborescence du projet

```
slate-controller/
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
├── .gitignore
├── README.md
├── CLAUDE.md                          # Ce fichier
├── LICENSE
│
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── ruff.toml
│   ├── mypy.ini
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI app entry point
│   │   ├── config.py                  # Settings (Pydantic Settings)
│   │   ├── auth.py                    # JWT/OIDC
│   │   ├── exceptions.py              # Custom exceptions
│   │   │
│   │   ├── slate/
│   │   │   ├── __init__.py
│   │   │   ├── client.py              # SlateClient (wrapper python-glinet)
│   │   │   ├── profiles.py            # ProfileManager
│   │   │   ├── vpn.py                 # VPN management
│   │   │   ├── wifi.py                # WiFi/SSID management
│   │   │   ├── tor.py                 # Tor management
│   │   │   ├── tailscale.py           # Tailscale management
│   │   │   ├── adguard.py             # AdGuard Home management
│   │   │   └── firewall.py            # Firewall/lockdown rules
│   │   │
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── deps.py                # Dependencies (Depends)
│   │   │   └── routes/
│   │   │       ├── __init__.py
│   │   │       ├── auth.py            # /api/auth
│   │   │       ├── profiles.py        # /api/profiles
│   │   │       ├── vpn.py             # /api/vpn
│   │   │       ├── slate.py           # /api/slate
│   │   │       └── audit.py           # /api/audit
│   │   │
│   │   ├── models/                    # Pydantic models
│   │   │   ├── __init__.py
│   │   │   ├── profile.py
│   │   │   ├── vpn_config.py
│   │   │   ├── slate_state.py
│   │   │   └── audit_log.py
│   │   │
│   │   ├── scheduler/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                # Scheduler entry point
│   │   │   ├── rotation.py            # Rotation VPN
│   │   │   ├── backup.py              # Backup configs
│   │   │   └── healthcheck.py         # Slate health monitoring
│   │   │
│   │   └── db/
│   │       ├── __init__.py
│   │       ├── database.py            # Async SQLAlchemy
│   │       ├── models.py              # ORM models
│   │       └── migrations/            # Alembic
│   │
│   ├── profiles/                      # Profil definitions (YAML)
│   │   ├── mission.yaml
│   │   ├── vacances.yaml
│   │   ├── osint.yaml
│   │   ├── home.yaml
│   │   └── lockdown.yaml
│   │
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_slate_client.py
│       ├── test_profiles.py
│       └── test_api.py
│
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── components.json                # shadcn/ui config
│   ├── public/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── ui/                    # shadcn/ui components
│   │   │   ├── ProfileCard.tsx
│   │   │   ├── VPNStatus.tsx
│   │   │   ├── SlateHealth.tsx
│   │   │   └── Layout.tsx
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Profiles.tsx
│   │   │   ├── VPNs.tsx
│   │   │   ├── Settings.tsx
│   │   │   └── Login.tsx
│   │   ├── hooks/
│   │   │   ├── useSlate.ts
│   │   │   ├── useProfiles.ts
│   │   │   └── useAuth.ts
│   │   ├── api/
│   │   │   ├── client.ts
│   │   │   ├── profiles.ts
│   │   │   ├── vpn.ts
│   │   │   └── slate.ts
│   │   ├── types/
│   │   │   ├── slate.ts
│   │   │   ├── profile.ts
│   │   │   └── vpn.ts
│   │   └── lib/
│   │       └── utils.ts
│
├── traefik/
│   ├── traefik.yml
│   └── dynamic.yml
│
└── data/                              # Volumes Docker (gitignored)
    ├── db/
    ├── logs/
    └── backups/
```

## Modèles de données clés

### Profile (YAML)

```yaml
# profiles/mission.yaml
name: mission
description: "Profil mission client - VPN corporate, kill switch strict"
icon: briefcase
color: "#3b82f6"

vpn:
  type: wireguard
  client: "hubone-paris"
  kill_switch: true
  
tor:
  enabled: false
  
tailscale:
  enabled: true
  
adguard:
  enabled: true
  lists:
    - "hagezi-tif"
    - "oisd-big"
    
ssids:
  - name: "MissionPro"
    enabled: true
    band: "5GHz"
    security: "WPA3-SAE"
  - name: "Parents"
    enabled: false
  - name: "Enfants"
    enabled: false
    
dns:
  servers:
    - "10.2.0.1"
  forced: true
  
firewall:
  lockdown: true
  geoip_whitelist:
    - "FR"
    - "CH"
    
logging:
  level: "INFO"
  forward_to_siem: true
```

### SlateState (Pydantic)

```python
class SlateState(BaseModel):
    """État instantané du Slate 7 Pro."""
    
    timestamp: datetime
    firmware_version: str
    uptime_seconds: int
    cpu_usage_percent: float
    ram_usage_percent: float
    storage_usage_percent: float
    
    active_profile: Optional[str]
    
    vpn: VPNState
    tor: TorState
    tailscale: TailscaleState
    adguard: AdGuardState
    
    connected_clients: int
    wan_status: str
    wan_ip: Optional[str]
    
    temperature_celsius: Optional[float]
    battery_level: Optional[int]  # Pour le Mudi 7
```

## Endpoints API à implémenter (V1)

### Authentication
- `POST /api/auth/login` — Login avec password
- `POST /api/auth/logout` — Logout
- `GET /api/auth/me` — Current user info

### Slate
- `GET /api/slate/status` — État complet du Slate
- `GET /api/slate/info` — Info hardware/firmware
- `POST /api/slate/reboot` — Reboot du Slate (avec confirmation)

### Profiles
- `GET /api/profiles` — Liste de tous les profils
- `GET /api/profiles/{name}` — Détails d'un profil
- `POST /api/profiles/{name}/activate` — Activer un profil
- `GET /api/profiles/active` — Profil actuellement actif
- `POST /api/profiles` — Créer un profil
- `PUT /api/profiles/{name}` — Modifier un profil
- `DELETE /api/profiles/{name}` — Supprimer un profil

### VPN
- `GET /api/vpn/clients` — Liste des configs VPN
- `POST /api/vpn/clients` — Ajouter une config (upload .conf)
- `DELETE /api/vpn/clients/{name}` — Supprimer
- `POST /api/vpn/clients/{name}/connect` — Activer un VPN
- `POST /api/vpn/disconnect` — Déconnecter VPN actif
- `GET /api/vpn/status` — Statut connexion actuelle

### Audit
- `GET /api/audit/logs` — Historique des actions (paginé)
- `GET /api/audit/events` — Événements système

## Endpoints à différer (V2+)

- Multi-équipements (Mudi 7, autres Slates)
- Webhooks
- Schedules complexes
- Mode lockdown automatique géolocalisé
- Intégration Homey/Home Assistant
- Export de rapports PDF
- Multi-utilisateurs / RBAC

## Variables d'environnement

```bash
# .env.example

# Slate 7 Pro
SLATE_URL=https://100.x.x.x          # IP Tailscale ou LAN
SLATE_USERNAME=root
SLATE_PASSWORD=changeme              # /!\ jamais committer

# Database
DB_URL=sqlite+aiosqlite:///./data/slate.db

# Auth
JWT_SECRET=generate-a-strong-secret-here
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# CORS
CORS_ORIGINS=http://localhost:5173,https://slate.tonlab.local

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# SIEM (optionnel)
SPLUNK_HEC_URL=
SPLUNK_HEC_TOKEN=

# Traefik
TRAEFIK_ACME_EMAIL=ton@email.com
TRAEFIK_DOMAIN=slate.tonlab.local

# Frontend
VITE_API_URL=https://slate-api.tonlab.local
```

## Workflow de développement

### Setup initial

```bash
# Clone et init
git clone <repo>
cd slate-controller

# Backend
cd backend
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Frontend
cd ../frontend
npm install

# Env
cp .env.example .env
# Éditer .env avec tes valeurs

# Lancement dev
docker compose -f docker-compose.dev.yml up
```

### Tests

```bash
# Backend
cd backend
pytest -v
pytest --cov=app --cov-report=html

# Frontend
cd frontend
npm test
npm run lint
npm run typecheck
```

### Build production

```bash
docker compose build
docker compose up -d
```

## Roadmap par phase

### Phase 1 — MVP (semaine 1-2)
- [x] Structure projet + Docker setup
- [ ] Auth backend (JWT simple)
- [ ] Connexion API JSON-RPC Slate (via python-glinet)
- [ ] Endpoint `GET /api/slate/status`
- [ ] Endpoint `GET /api/profiles` (lecture YAML)
- [ ] UI Dashboard basique (état Slate)
- [ ] UI page Profils (liste + activation)

### Phase 2 — Profils complets (semaine 3-4)
- [ ] ProfileManager : application complète d'un profil sur le Slate
- [ ] 5 profils YAML pré-définis (Mission, Vacances, OSINT, Home, Lockdown)
- [ ] Endpoints CRUD profils
- [ ] UI création/édition profils
- [ ] Historique audit logs

### Phase 3 — VPN management (semaine 5-6)
- [ ] Upload configs WireGuard
- [ ] Bascule entre VPN actifs
- [ ] Failover automatique
- [ ] Stats par VPN

### Phase 4 — Scheduler et automation
- [ ] Rotation VPN périodique
- [ ] Backup configs vers NAS
- [ ] Healthcheck monitoring
- [ ] Notifications (Pushover/Discord)

### Phase 5 — Production hardening
- [ ] Tests E2E
- [ ] Documentation utilisateur
- [ ] Performance tuning
- [ ] Sécurité audit interne

## Considérations spéciales pour Claude Code

### À toujours faire
- **Vérifier l'état du repo** avant de modifier (`git status`)
- **Lire les fichiers existants** avant de les modifier
- **Suivre les conventions** définies dans ce CLAUDE.md
- **Ajouter des tests** pour toute nouvelle fonction
- **Mettre à jour le README** si l'API change
- **Logger les actions importantes** côté backend
- **Valider les inputs** avec Pydantic systématiquement
- **Gérer les erreurs** explicitement (pas de except bare)

### À éviter absolument
- **Hardcoder des secrets** dans le code
- **Commit dans `main`** directement
- **Modifier des fichiers `.env`** ou similaires sans demander
- **Installer des paquets** sans valider la cohérence (toujours vérifier qu'ils sont compatibles avec la stack imposée)
- **Casser la rétrocompatibilité** de l'API sans incrément de version
- **Désactiver les types** mypy sauf cas explicite et commenté
- **Utiliser des libs obsolètes** ou non maintenues

### Points d'attention spécifiques au Slate 7 Pro

- L'API JSON-RPC du Slate est à `https://<slate_ip>/rpc`
- L'auth se fait via challenge/response (voir python-glinet)
- Le SID expire après inactivité, gérer la reconnexion
- Le Slate peut être inaccessible (mobile, off, mauvais réseau) — toujours timeout et fallback gracieux
- Certains endpoints API GL.iNet changent entre firmware versions — pin la lib python-glinet
- Le firmware Slate 7 Pro tourne sur OpenWrt 21.02 + GL.iNet 4.x

### Documentation de référence

- **API GL.iNet JSON-RPC** : https://dev.gl-inet.cn/ (en chinois, traduisible)
- **python-glinet** : https://github.com/tomtana/python-glinet
- **FastAPI** : https://fastapi.tiangolo.com/
- **shadcn/ui** : https://ui.shadcn.com/
- **OpenWrt UCI** : https://openwrt.org/docs/guide-user/base-system/uci

## Décisions architecturales (ADR)

### ADR-001 : Pourquoi FastAPI plutôt que Flask/Django
**Décision** : FastAPI.  
**Raison** : Async natif (cohérent avec httpx pour appels Slate), validation Pydantic native, doc OpenAPI auto-générée, performance excellente, typage moderne.

### ADR-002 : Pourquoi SQLite par défaut plutôt que PostgreSQL
**Décision** : SQLite (PostgreSQL en option).  
**Raison** : Simple à déployer, un seul utilisateur au début, suffisant pour l'usage cible, pas de service supplémentaire à gérer. Migration PostgreSQL prévue si multi-user.

### ADR-003 : Pourquoi React plutôt que Vue/HTMX
**Décision** : React + TypeScript.  
**Raison** : Écosystème mature, shadcn/ui excellent, typage strict, déjà familier. HTMX serait viable pour version ultra-light mais moins flexible pour le dashboard temps réel.

### ADR-004 : Pourquoi YAML pour les profils
**Décision** : Fichiers YAML versionnés dans le repo.  
**Raison** : Lisible humainement, versionnable Git, éditable hors UI, exportable/partageable entre instances. La DB stocke seulement les états et logs, pas les définitions de profils.

### ADR-005 : Pourquoi python-glinet plutôt que des appels JSON-RPC manuels
**Décision** : python-glinet.  
**Raison** : Lib communautaire active, gère challenge/response, SID, abstraction des endpoints. Risque : si la lib casse à un upgrade GL.iNet, fork ou contribution upstream.

## Notes de sécurité importantes

- **Le mot de passe du Slate** ne doit JAMAIS apparaître dans les logs
- **Les configs WireGuard** contiennent des clés privées : stockage chiffré obligatoire
- **L'API doit être derrière auth** dès le MVP (pas d'accès anonyme)
- **HTTPS obligatoire** en production, certificats via Traefik + Let's Encrypt
- **Audit log** de toutes les modifications de config Slate
- **Pas de bypass d'auth en dev** (utiliser un compte dev dédié)

## Contact / Mainteneur

Projet personnel — propriétaire unique.
Pour toute question technique : ouvrir une issue GitHub.

---

**Version CLAUDE.md** : 1.0  
**Dernière mise à jour** : Mai 2026  
**Statut projet** : En développement initial
