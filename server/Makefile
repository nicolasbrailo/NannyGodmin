run:
	pipenv run python3 ./app.py

rebuild_deps:
	rm -f Pipfile Pipfile.lock
	pipenv --rm || true
	pipenv --python python3
	pipenv install flask 'qrcode[pil]'

