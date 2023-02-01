default_dir = /opt/reddit-scheduler

default:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install -r requirements.txt; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. --mypy_out=. reddit.proto; \
	)

proto:
	( \
	python -m venv venv; \
	source venv/bin/activate; \
	pip install grpcio grpcio-tools; \
	python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. --mypy_out=. reddit.proto; \
	)

start:
	systemctl --user restart reddit-scheduler

stop:
	systemctl --user stop reddit-scheduler

status:
	systemctl --user status reddit-scheduler

reload:
	systemctl --user daemon-reload

install: default
	install -Dm755 server.py $(DESTDIR)$(default_dir)/server.py
	install -Dm755 client.py $(DESTDIR)$(default_dir)/client.py
	install -Dm644 *_pb2.py -t $(DESTDIR)$(default_dir)/ 
	install -Dm644 *_pb2_grpc.py -t $(DESTDIR)$(default_dir)/ 
	install -Dm644 examples/config.ini $(DESTDIR)/usr/share/doc/reddit-scheduler/examples/config.ini
	install -Dm644 examples/text-post.yaml $(DESTDIR)/usr/share/doc/reddit-scheduler/examples/text-post.yaml
	install -Dm644 examples/poll-post.yaml $(DESTDIR)/usr/share/doc/reddit-scheduler/examples/poll-post.yaml
	install -Dm644 examples/image-post.yaml $(DESTDIR)/usr/share/doc/reddit-scheduler/examples/image-post.yaml
	install -Dm644 reddit-scheduler.service $(DESTDIR)/usr/lib/systemd/user/reddit-scheduler.service
	install -Dm755 client $(DESTDIR)/usr/bin/reddit
	cp -r venv $(DESTDIR)$(default_dir)/

uninstall:
	rm -rf $(DESTDIR)$(default_dir)
	rm -f $(DESTDIR)/usr/lib/systemd/user/reddit-scheduler.service
	rm -rf $(DESTDIR)/usr/share/doc/reddit-scheduler/
	rm -f $(DESTDIR)/usr/bin/reddit

clean:
	rm -rf *_pb2.py *_pb2_grpc.py venv

.PHONY: default proto start stop status reload install uninstall clean
