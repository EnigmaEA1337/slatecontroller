# Slate Controller

Application Docker pour piloter et personnaliser un routeur **GL.iNet Slate 7 Pro (GL-BE10000)** via son API JSON-RPC.

> Voir [CLAUDE.md](CLAUDE.md) pour la spécification complète, l'architecture et la roadmap.

## Quickstart (dev)

### 1. Prérequis

- Docker + Docker Compose v2
- Node.js 20+ (pour développement frontend hors Docker)
- Python 3.12+ (pour développement backend hors Docker)
- Un Slate 7 Pro accessible (ou ses credentials)

### 2. Configuration

```bash
cp .env.example .env
# Éditer .env (au minimum: SLATE_URL, SLATE_PASSWORD, JWT_SECRET)
```

### 3. Lancement (Docker, dev)

```bash
docker compose -f docker-compose.dev.yml up --build
```

- Backend (FastAPI) : http://localhost:8000
- Docs API (Swagger) : http://localhost:8000/docs
- Frontend (Vite) : http://localhost:5173

### 4. Lancement (production)

```bash
docker compose up -d --build
```

## Développement local (hors Docker)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Tests

```bash
# Backend
cd backend && pytest -v

# Frontend
cd frontend && npm test
```

## Modifier le schéma DB (Alembic)

Quand tu changes un modèle ORM dans `backend/app/db/models.py` (ou ajoutes une nouvelle table) :

```bash
cd backend
# 1. Générer la migration à partir de la différence model ↔ DB actuelle
.venv/bin/alembic revision --autogenerate -m "describe what changed"

# 2. Inspecter le fichier généré dans alembic/versions/ (relire, ajuster si besoin)

# 3. (optionnel) Appliquer manuellement, sinon le prochain boot le fera
.venv/bin/alembic upgrade head

# 4. Commit le fichier de migration avec ton change de code
```

Les migrations sont **appliquées automatiquement au démarrage du backend** (`alembic upgrade head` dans `init_db()`).

**Bases legacy** (DB qui existe sans table `alembic_version`) sont détectées et taguées au `head` actuel sans rien casser — aucune perte de données.

**Rollback** : `alembic downgrade -1` ou `alembic downgrade <revision_id>`.

## Structure

Voir [CLAUDE.md](CLAUDE.md#arborescence-du-projet).

## Statut

**Phase 1 — MVP** (en cours). Voir [CLAUDE.md](CLAUDE.md#roadmap-par-phase).
