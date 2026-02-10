# Stock Daily API

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
uvicorn app.main:app --reload
```

## Example

```text
GET http://127.0.0.1:8000/daily/sh600519
```

