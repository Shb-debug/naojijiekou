import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
state = {}


def new_question():
	while True:
		op = random.choice(['+', '-', '×'])
		if op == '+':
			a = random.randint(10, 499)
			b = random.randint(10, 499)
			answer = a + b
		elif op == '-':
			a = random.randint(100, 999)
			b = random.randint(10, a)
			answer = a - b
		else:
			a = random.randint(2, 31)
			b = random.randint(2, 31)
			answer = a * b
		if 0 <= answer <= 999:
			break
	state.clear()
	state.update({
		'question': '{} {} {} = ?'.format(a, op, b),
		'input': '',
		'status': 'ready',
		'answer': '',
		'correct': None,
		'_expected': '{:03d}'.format(answer),
	})


def public_state():
	return {key: value for key, value in state.items() if not key.startswith('_')}


class Handler(BaseHTTPRequestHandler):
	def send_json(self, payload):
		body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
		self.send_response(200)
		self.send_header('Content-Type', 'application/json; charset=utf-8')
		self.send_header('Cache-Control', 'no-store')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		parsed = urlparse(self.path)
		if parsed.path == '/':
			body = (BASE_DIR / 'ssvep_arithmetic_ui.html').read_bytes()
			self.send_response(200)
			self.send_header('Content-Type', 'text/html; charset=utf-8')
			self.send_header('Content-Length', str(len(body)))
			self.end_headers()
			self.wfile.write(body)
			return
		if parsed.path == '/state':
			self.send_json(public_state())
			return
		if parsed.path == '/new':
			new_question()
			self.send_json(public_state())
			return
		if parsed.path == '/event':
			query = parse_qs(parsed.query)
			digitText = query.get('digit', [''])[0]
			if digitText.isdigit() and len(digitText) == 1 and state['status'] == 'ready':
				state['input'] += digitText
				if len(state['input']) >= 3:
					state['input'] = state['input'][:3]
					state['answer'] = state['input']
					state['correct'] = state['answer'] == state['_expected']
					state['status'] = 'correct' if state['correct'] else 'incorrect'
				self.send_json(public_state())
			return
		self.send_error(404)

	def log_message(self, formatString, *args):
		return


if __name__ == '__main__':
	new_question()
	server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
	print('SSVEP arithmetic UI: http://127.0.0.1:8765')
	print('Press Ctrl+C to stop.')
	server.serve_forever()
