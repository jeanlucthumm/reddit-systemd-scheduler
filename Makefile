default_dir = /opt/reddit-scheduler

default:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install -r requirements.txt; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. reddit.proto; \
	)

proto:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install grpcio grpcio-tools; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. reddit.proto; \
	)

install: default
	install -Dm755 server.py $(DESTDIR)$(default_dir)/server.py
	install -Dm755 client.py $(DESTDIR)$(default_dir)/client.py
	install -Dm644 *_pb2.py -t $(DESTDIR)$(default_dir)/ 
	install -Dm644 *_pb2_grpc.py -t $(DESTDIR)$(default_dir)/ 
	cp -r venv $(DESTDIR)$(default_dir)/
	install -Dm644 sample-config.ini $(DESTDIR)/usr/share/doc/reddit-scheduler/examples/config.ini
	install -Dm644 reddit-scheduler.service $(DESTDIR)/usr/lib/systemd/user/reddit-scheduler.service
	install -Dm755 client $(DESTDIR)/usr/bin/reddit

uninstall:
	rm -rf $(DESTDIR)$(default_dir)
	rm -f $(DESTDIR)/usr/lib/systemd/user/reddit-scheduler.service
	rm -f $(DESTDIR)/usr/share/doc/reddit-scheduler/examples/config.ini
	rm -f $(DESTDIR)/usr/bin/reddit

clean:
	rm -rf *_pb2.py *_pb2_grpc.py venv
