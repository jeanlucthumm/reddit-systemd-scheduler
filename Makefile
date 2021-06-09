# PREFIX is environment variable, but if it is not set, then set default value
ifeq ($(PREFIX),)
	PREFIX := /usr
endif

ifeq ($(XDG_CONFIG_HOME),)
	CONFIGDIR := $(HOME)/.config
else
	CONFIGDIR := $(XDG_CONFIG_HOME)
endif

default:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install -r requirements.txt; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. reddit.proto; \
	pyinstaller --onefile client.py; \
	pyinstaller --onefile server.py; \
	)

proto:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install grpcio grpcio-tools; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. reddit.proto; \
	)

install: default
	install -Dm755 dist/client $(DESTDIR)$(PREFIX)/bin/reddit
	install -Dm755 dist/server $(DESTDIR)$(PREFIX)/bin/reddit-scheduler
	install -Dm644 sample-config.ini $(DESTDIR)$(CONFIGDIR)/reddit-scheduler/config.ini

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/reddit
	rm -f $(DESTDIR)$(PREFIX)/bin/reddit-scheduler
	rm -rf $(DESTDIR)$(CONFIGDIR)/reddit-scheduler

clean:
	rm -rf build dist *_pb2.py *_pb2_grpc.py venv
