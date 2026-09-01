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
			a = random.randint(5, 49)
			b = random.randint(5, 49)
			answer = a + b
		elif op == '-':
			a = random.randint(20, 99)
			b = random.randint(1, a - 10)
			answer = a - b
		else:
			a = random.randint(2, 9)
			b = random.randint(2, 9)
			answer = a * b
		if 10 <= answer <= 99:
			break
	state.clear()
	state.update({
		'question': '{} {} {} = ?'.format(a, op, b),
		'input': '',
		'status': 'ready',
		'answer': '',
		'correct': None,
		'stage': 'row',
		'next_stage': None,
		'selected_row': None,
		'selected_column': None,
		'message': '先选择目标所在的行',
		'_expected': '{:02d}'.format(answer),
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
			body = (BASE_DIR / 'ssvep_grouped_arithmetic_ui.html').read_bytes()
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
		query = parse_qs(parsed.query)
		if parsed.path == '/row':
			try:
				row = int(query.get('index', ['-1'])[0])
			except ValueError:
				row = -1
			if state['status'] == 'ready' and state['stage'] == 'row' and 0 <= row <= 3:
				state['selected_row'] = row
				state['selected_column'] = None
				state['stage'] = 'neutral'
				state['next_stage'] = 'column'
				state['message'] = '已选择第 {} 行，请看上方中央十字，再选择目标所在的列'.format(row + 1)
			self.send_json(public_state())
			return
		if parsed.path == '/ready':
			nextStage = state.get('next_stage')
			requestedStage = query.get('stage', [''])[0]
			if state['status'] == 'ready' and state['stage'] == 'neutral' and requestedStage == nextStage:
				state['stage'] = nextStage
				state['next_stage'] = None
				state['message'] = '请选择目标所在的列' if nextStage == 'column' else '请选择下一位数字所在的行'
			self.send_json(public_state())
			return
		if parsed.path == '/column':
			try:
				column = int(query.get('index', ['-1'])[0])
			except ValueError:
				column = -1
			if state['status'] == 'ready' and state['stage'] == 'column' and 0 <= column <= 2:
				state['selected_column'] = column
				state['stage'] = 'neutral'
				state['next_stage'] = 'row'
				state['message'] = '列已选择，请看上方中央十字，准备下一位数字'
			self.send_json(public_state())
			return
		if parsed.path == '/invalid':
			state['stage'] = 'neutral'
			state['next_stage'] = 'row'
			state['selected_row'] = None
			state['selected_column'] = None
			state['message'] = '第 4 行只有中间的 0 有效，请重新选择'
			self.send_json(public_state())
			return
		if parsed.path == '/digit':
			digitText = query.get('digit', [''])[0]
			if digitText.isdigit() and len(digitText) == 1 and state['status'] == 'ready':
				state['input'] += digitText
				if len(state['input']) >= 2:
					state['input'] = state['input'][:2]
					state['answer'] = state['input']
					state['correct'] = state['answer'] == state['_expected']
					state['status'] = 'correct' if state['correct'] else 'incorrect'
				state['stage'] = 'neutral'
				state['next_stage'] = 'row'
				state['selected_row'] = None
				state['selected_column'] = None
				state['message'] = '请看上方中央十字，准备下一位数字'
			self.send_json(public_state())
			return
		self.send_error(404)

	def log_message(self, formatString, *args):
		return


if __name__ == '__main__':
	new_question()
	server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
	print('SSVEP grouped arithmetic UI: http://127.0.0.1:8765')
	print('Press Ctrl+C to stop.')
	server.serve_forever()
