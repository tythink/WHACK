.PHONY: install run-emotion run-emotion-mock run-orchestrator run-all test

install:
	pip install -r requirements-emotion.txt
	pip install -r requirements-orchestrator.txt

run-emotion:
	python src/services/emotion/server.py --port 5001

run-emotion-mock:
	python src/services/emotion/server.py --port 5001 --mock

run-orchestrator:
	python src/orchestrator/server.py --port 8080 --emotion-port 5001 --action-port 5002

run-all:
	python run_role1.py --mock

test:
	python -m unittest discover -s tests -p "test_*.py"
