.PHONY: dev backend frontend install

# Start both frontend and backend
dev:
	@echo "Starting backend on :8000 and frontend on :5173..."
	@DB_PATH=/tmp/citation-checker/reports.db \
		uvicorn app.main:app --port 8000 --reload --app-dir backend & \
	cd frontend && npm run dev & \
	wait

# Start backend only
backend:
	mkdir -p /tmp/citation-checker
	DB_PATH=/tmp/citation-checker/reports.db \
		uvicorn app.main:app --port 8000 --reload --app-dir backend

# Start frontend only
frontend:
	cd frontend && npm run dev

# Install all dependencies
install:
	cd backend && pip install -e .
	cd frontend && npm install
