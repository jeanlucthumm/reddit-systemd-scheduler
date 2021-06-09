default:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install -r requirements.txt; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. reddit.proto; \
	pyinstaller --onefile client.py; \
	)
