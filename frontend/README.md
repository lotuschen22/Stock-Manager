# Frontend

## Install

```powershell
cd frontend
npm install
```

## Run

```powershell
npm run dev
```

## API Base URL

Create `frontend/.env.local`:

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## Structure

```text
frontend/
  src/
    app.vue
    main.js
    constants/api.js
    services/api/http.js
    services/api/stocks.js
    features/stocks/useStockDashboard.js
```
