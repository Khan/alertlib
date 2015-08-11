.PHONY: test tests

test tests:
	python -m unittest discover -p '*_test.py' tests
