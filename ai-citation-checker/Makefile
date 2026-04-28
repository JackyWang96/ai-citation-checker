.PHONY: dev backend frontend install

DATA_DIR := $(HOME)/.citation-checker
DB_PATH  := $(DATA_DIR)/reports.db

# Start both frontend and backend
dev:
	@mkdir -p $(DATA_DIR)
	@echo "Backend  → http://localhost:8000"
	@echo "Frontend → http://localhost:5173"
	@echo ""
	DB_PATH=$(DB_PATH) uvicorn app.main:app --port 8000 --reload --app-dir backend & \
	cd frontend && npm run dev; \
	wait

# Start backend only
backend:
	mkdir -p $(DATA_DIR)
	DB_PATH=$(DB_PATH) uvicorn app.main:app --port 8000 --reload --app-dir backend

# Start frontend only
frontend:
	cd frontend && npm run dev

# Install all dependencies
install:
	cd backend && pip install -e .
	cd frontend && npm install
