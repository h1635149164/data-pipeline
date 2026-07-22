# OpenBudget Data Pipeline

The **OpenBudget Data Pipeline** is an automated ETL (Extract, Transform, Load) and synchronization engine designed to fetch public financial data from official Czech Republic state budget endpoints, transform yearly/monthly datasets, and synchronize them with OpenBudget's remote JSON mirror storage (GitLab Snippets / GitHub Gists).

---

## 🚀 Key Capabilities

- **Budget Chapter Synchronization (`chapters.py`)**: Fetches official Czech state budget chapters, anchors entities using **fuzzy name matching**, maintains internal auto-incrementing `iid` identifiers independently of state ID reassignments, and mirrors official chapter updates (`startDate`, `endDate`, `expired`).
- **Yearly & Monthly Dataset Traversal (`vYYYY.json`)**: Traverses monthly batches (encoded as YYMM, e.g. `2605`) downwards from month 12 to 1 for years back to 2010. Ignores unsubmitted months containing all `0` values.
- **Modular Storage Abstraction**: Uses small, single-purpose functions to read/write JSON datasets stored in GitLab Snippets / GitHub Gists using API authentication (`token` and `secret`).

---

## 💻 Tech Stack & Requirements

- **Python**: `>= 3.14`
- **Project & Package Manager**: [`uv`](https://github.com/astral-sh/uv)
- **HTTP Client**: [`httpx`](https://www.python-httpx.org/)
- **Test Framework**: `pytest`
- **Linting & Type Checking**: `ruff`, `mypy`

---

## ⚙️ Setup & Local Installation

Ensure [`uv`](https://github.com/astral-sh/uv) is installed on your system.

1. **Install Dependencies**:
   ```bash
   uv sync
   ```

2. **Run Pipeline Entrypoint**:
   ```bash
   uv run python -m src.main
   ```

---

## 🧪 Testing & Code Quality

- **Run Pytest Test Suite**:
  ```bash
  uv run pytest
  ```

- **Run Static Type Checker**:
  ```bash
  uv run mypy src
  ```

- **Run Linter / Formatter**:
  ```bash
  uv run ruff check
  ```

---

## 📁 Repository Structure

```
data-pipeline/
├── inspo/                # Reference data snapshots (official & internal API outputs)
├── src/                  # Application source code
│   ├── support/          # Modular functions (chapters, getenv, scrapper, storage)
│   ├── main.py           # Pipeline runner
│   └── task.py           # Scheduled sync execution
├── tests/                # Unit & integration test suite
├── pyproject.toml        # UV dependencies & configuration
└── README.md             # Project documentation
```

---

## 📜 Guidelines & Rules

- **Testing Discipline**: Every newly added function must include granular `pytest` test cases covering potential single points of failure.
- **High Modularity**: Functions must be kept tiny and single-purposed to simplify switching storage targets or endpoint formats.

---

## 🐳 Container Deployment (Docker & Docker Compose)

The pipeline ships a multi-stage `Dockerfile` (Python 3.14 + `uv`) and a `Makefile` with convenience targets.

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENBUDGET_CONFIG` | ✅ | Path **inside the container** to `config.json` |
| `OPENBUDGET_TARGET` | ✅ | Path **inside the container** to `target.json` |
| `OPENBUDGET_AUTO_RELOAD` | optional | `true` / `false` (default `false`) — reload config on file change |
| `OPENBUDGET_RELOAD_INTERVAL` | optional | Seconds between config mtime checks (default `300`) |

### Plain Docker

Build and run with config/target mounted from the host:

```bash
# Build the image
docker build -t openbudget-pipeline .

# Run (mount your config files at /cfg/)
docker run -d \
  --name openbudget-pipeline \
  --restart unless-stopped \
  -v /path/to/your/config.json:/cfg/config.json:ro \
  -v /path/to/your/target.json:/cfg/target.json:ro \
  -e OPENBUDGET_CONFIG=/cfg/config.json \
  -e OPENBUDGET_TARGET=/cfg/target.json \
  -e OPENBUDGET_AUTO_RELOAD=true \
  openbudget-pipeline
```

### Docker Compose

Create a `docker-compose.yml` alongside your `config.json` and `target.json`:

```yaml
version: "3.9"

services:
  openbudget-pipeline:
    image: openbudget-pipeline:latest   # or build: .
    restart: unless-stopped
    volumes:
      - ./config.json:/cfg/config.json:ro
      - ./target.json:/cfg/target.json:ro
    environment:
      OPENBUDGET_CONFIG: /cfg/config.json
      OPENBUDGET_TARGET: /cfg/target.json
      # Enable live config reload every 5 minutes (no restart needed):
      OPENBUDGET_AUTO_RELOAD: "true"
      OPENBUDGET_RELOAD_INTERVAL: "300"
```

Then spin it up:

```bash
# Start in the background
docker compose up -d

# Tail logs
docker compose logs -f openbudget-pipeline

# Stop
docker compose down
```

### Makefile shortcuts

```bash
make build           # Build Docker image
make publish-podman  # Push via podman
make publish-docker  # Push via docker
```
