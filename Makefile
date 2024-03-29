APPENGINE_DIR=.
GOOGLE_API_CLIENT_DIR=.

# Note we do not enable testing appengine mail on python3, since the
# appengine libs are python2-only.
check: dev-deps
	export APPLICATION_ID=dev~khan-academy; \
	for f in tests/*_test.py; do \
	   echo "------ $$f PYTHON2" && env PYTHONPATH=${GOOGLE_API_CLIENT_DIR}:${APPENGINE_DIR}:$$PYTHONPATH python2 "$$f" && \
	   echo "------ $$f PYTHON3" && env PYTHONPATH=${GOOGLE_API_CLIENT_DIR}:${PYTHONPATH}::$$PYTHONPATH python3 "$$f"; \
	done

deps:
	pip install -r requirements.txt

dev-deps: dev_requirements.txt
	pip install -r dev_requirements.txt
