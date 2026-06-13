.PHONY: install run run-prod test clean

PYTHON = C:\Users\narut\AppData\Local\Programs\Python\Python313\python.exe

install:
	pip install -r requirements.txt

run:
	uvicorn serve:app --host 0.0.0.0 --port 8000 --reload

run-prod:
	uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log

test:
	$(PYTHON) -m pytest -v tests/

clean:
	rmdir /s /q __pycache__ .pytest_cache 2>nul || exit 0
	del /f /q rca_llm_trace.log 2>nul || exit 0
