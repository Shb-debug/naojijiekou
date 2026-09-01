import ctypes
import time
import numpy


class MyOVBox(OVBox):
	def __init__(self):
		OVBox.__init__(self)
		self.frequencyA = 10.0
		self.frequencyB = 15.0
		self.minimumSnr = 1.2
		self.minimumMargin = 0.15
		self.smoothingWindows = 3
		self.confirmationWindows = 3
		self.neutralUnlockWindows = 4
		self.cooldownSeconds = 5.0
		self.signalHeader = None
		self.scoreHistory = []
		self.candidateState = 0
		self.candidateCount = 0
		self.lastState = None
		self.lastToggleTime = -1e9
		self.neutralCount = 0
		self.armed = True
		self.codeNone = 0
		self.codeA = 0
		self.codeB = 0

	def initialize(self):
		self.frequencyA = float(self.setting['Frequency A (Hz)'])
		self.frequencyB = float(self.setting['Frequency B (Hz)'])
		self.minimumSnr = float(self.setting['Minimum SNR'])
		self.minimumMargin = float(self.setting['Minimum score margin'])
		self.smoothingWindows = max(1, int(self.setting['Smoothing windows']))
		self.confirmationWindows = max(1, int(self.setting['Confirmation windows']))
		self.neutralUnlockWindows = max(1, int(self.setting['Neutral unlock windows']))
		self.cooldownSeconds = max(0.0, float(self.setting['Media cooldown (sec)']))
		self.codeNone = OpenViBE_stimulation['OVTK_StimulationId_Label_00']
		self.codeA = OpenViBE_stimulation['OVTK_StimulationId_Label_01']
		self.codeB = OpenViBE_stimulation['OVTK_StimulationId_Label_02']
		self.output[0].append(OVStimulationHeader(0., 0.))

	def emitState(self, state, date, scoreA, scoreB):
		code = self.codeA if state == 1 else self.codeB if state == 2 else self.codeNone
		stimSet = OVStimulationSet(date, date + 1.0 / max(self.getClock(), 1.0))
		stimSet.append(OVStimulation(code, date, 0.))
		self.output[0].append(stimSet)
		label = '10Hz左侧' if state == 1 else '12Hz右侧' if state == 2 else '待机'
		print('SSVEP_STATE:', label, 'score10 =', round(scoreA, 3),
			'score12 =', round(scoreB, 3))

	def sendMediaCommand(self, action):
		# WM_APPCOMMAND supports separate Play and Pause commands on Windows.
		HWND_BROADCAST = 0xFFFF
		WM_APPCOMMAND = 0x0319
		APPCOMMAND_MEDIA_PLAY = 46
		APPCOMMAND_MEDIA_PAUSE = 47
		command = APPCOMMAND_MEDIA_PLAY if action == 'play' else APPCOMMAND_MEDIA_PAUSE
		lParam = command << 16
		ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_APPCOMMAND, 0, lParam)

	def scoreAtFrequency(self, signal, samplingRate, targetFrequency):
		signal = signal - numpy.mean(signal)
		window = numpy.hanning(len(signal))
		power = numpy.abs(numpy.fft.rfft(signal * window)) ** 2
		frequencies = numpy.fft.rfftfreq(len(signal), 1.0 / samplingRate)
		resolution = samplingRate / float(len(signal))
		band = numpy.abs(frequencies - targetFrequency) <= max(0.5, resolution * 1.1)
		noise = (numpy.abs(frequencies - targetFrequency) >= 1.5) & (numpy.abs(frequencies - targetFrequency) <= 3.0)
		if not numpy.any(band) or not numpy.any(noise):
			return 0.0
		return float(numpy.mean(power[band]) / (numpy.mean(power[noise]) + 1e-12))

	def process(self):
		for chunkIdx in range(len(self.input[0])):
			chunk = self.input[0].pop()

			if type(chunk) == OVSignalHeader:
				self.signalHeader = chunk

			elif type(chunk) == OVSignalBuffer:
				if self.signalHeader is None:
					continue
				data = numpy.array(chunk, dtype=float).reshape(tuple(self.signalHeader.dimensionSizes))
				if data.ndim == 1:
					data = data.reshape(1, -1)
				samplingRate = float(self.signalHeader.samplingRate)
				scoreA = float(numpy.mean([self.scoreAtFrequency(row, samplingRate, self.frequencyA) for row in data]))
				scoreB = float(numpy.mean([self.scoreAtFrequency(row, samplingRate, self.frequencyB) for row in data]))
				self.scoreHistory.append((scoreA, scoreB))
				if len(self.scoreHistory) > self.smoothingWindows:
					self.scoreHistory.pop(0)
				scoreA = float(numpy.median([item[0] for item in self.scoreHistory]))
				scoreB = float(numpy.median([item[1] for item in self.scoreHistory]))

				bestScore = max(scoreA, scoreB)
				margin = abs(scoreA - scoreB) / max(bestScore, 1e-12)
				if bestScore < self.minimumSnr or margin < self.minimumMargin:
					candidate = 0
				elif scoreA > scoreB:
					candidate = 1
				else:
					candidate = 2
				print('SSVEP_SCORE: 10Hz =', round(scoreA, 3),
					'15Hz =', round(scoreB, 3),
					'margin =', round(margin, 3),
					'candidate =', candidate)

				if self.lastState is None:
					self.lastState = 0
					self.emitState(0, chunk.startTime, scoreA, scoreB)

				if candidate == self.candidateState:
					self.candidateCount += 1
				else:
					self.candidateState = candidate
					self.candidateCount = 1

				now = time.monotonic()
				if candidate == 0:
					self.neutralCount += 1
					# A command is re-armed only after the user has looked away
					# from both targets for several consecutive windows.
					if self.neutralCount >= self.neutralUnlockWindows and now - self.lastToggleTime >= self.cooldownSeconds:
						if not self.armed:
							print('SSVEP_ARMED: ready for next command')
						self.armed = True
				else:
					self.neutralCount = 0

				if self.candidateCount >= self.confirmationWindows and candidate != self.lastState:
					if candidate == 0:
						self.emitState(0, chunk.startTime, scoreA, scoreB)
						self.lastState = 0
					elif self.armed:
						self.emitState(candidate, chunk.startTime, scoreA, scoreB)
						self.lastState = candidate
						action = 'play' if candidate == 1 else 'pause'
						self.sendMediaCommand(action)
						self.lastToggleTime = now
						self.armed = False
						print('MEDIA_ACTION:', 'Play' if action == 'play' else 'Pause')
					else:
						# Do not emit or send anything while locked. The user must
						# first enter the neutral state to prevent rapid repeats.
						print('SSVEP_LOCKED: hold neutral before next command')

			elif type(chunk) == OVSignalEnd:
				self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
