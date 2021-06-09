# PREFIX is environment variable, but if it is not set, then set default value
ifeq ($(PREFIX),)
	PREFIX := /usr
endif

default:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install -r requirements.txt; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. reddit.proto; \
	pyinstaller --onefile client.py; \
	)

install: default
	install -Dm755 dist/client $(DESTDIR)$(PREFIX)/bin/reddit
