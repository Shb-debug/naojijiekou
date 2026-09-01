import json
import urllib.parse
import urllib.request
import numpy


class MyOVBox(OVBox):
	def __init__(self):
		OVBox.__init__(self)
		self.frequencies = [5.0, 5.4545, 6.0, 6.6667, 7.5, 8.5714, 10.0, 12.0, 15.0, 20.0]
		self.minimumScore = 0.35
		self.minimumMargin = 0.25
		self.smoothingWindows = 5
		self.confirmationWindows = 5
		self.neutralUnlockWindows = 6
		self.uiServerUrl = 'http://127.0.0.1:8765'
		self.signalHeader = None
		self.scoreHistory = []
		self.candidateDigit = -1
		self.candidateCount = 0
		self.neutralCount = 0
		self.armed = True
		self.lastOutputState = None
		self.codeByDigit = {}
		self.signalInfoPrinted = False

	def initialize(self):
		frequencyText = str(self.setting['Frequencies (Hz)'])
		self.frequencies = [float(value.strip()) for value in frequencyText.split(',') if value.strip()]
		if len(self.frequencies) != 10:
			raise ValueError('Frequencies (Hz) must contain exactly 10 values')
		self.minimumScore = float(self.setting['Minimum CCA score'])
		self.minimumMargin = float(self.setting['Minimum score margin'])
		self.smoothingWindows = max(1, int(self.setting['Smoothing windows']))
		self.confirmationWindows = max(1, int(self.setting['Confirmation windows']))
		self.neutralUnlockWindows = max(1, int(self.setting['Neutral unlock windows']))
		self.uiServerUrl = str(self.setting['UI server URL']).rstrip('/')
		self.codeByDigit[-1] = OpenViBE_stimulation['OVTK_StimulationId_Label_00']
		for digit in range(10):
			self.codeByDigit[digit] = OpenViBE_stimulation['OVTK_StimulationId_Label_{:02d}'.format(digit + 1)]
		self.output[0].append(OVStimulationHeader(0., 0.))

	def emitState(self, digit, date, scores):
		code = self.codeByDigit[digit]
		stimSet = OVStimulationSet(date, date + 1.0 / max(self.getClock(), 1.0))
		stimSet.append(OVStimulation(code, date, 0.))
		self.output[0].append(stimSet)
		label = '待机' if digit < 0 else str(digit)
		print('SSVEP_DIGIT:', label, 'best =', round(max(scores), 3),
			'margin =', round(self.scoreMargin(scores), 3))

	def scoreMargin(self, scores):
		ordered = sorted(scores, reverse=True)
		return (ordered[0] - ordered[1]) / max(ordered[0], 1e-12)

	def sendDigitToUi(self, digit):
		url = self.uiServerUrl + '/event?' + urllib.parse.urlencode({'digit': str(digit)})
		try:
			with urllib.request.urlopen(url, timeout=0.25) as response:
				json.loads(response.read().decode('utf-8'))
			return True
		except Exception as error:
			print('UI_EVENT_ERROR:', str(error))
			return False

	def bandpass(self, samplesByChannel, samplingRate, lowFrequency, highFrequency):
		# FFT-domain filter bank. The OpenViBE Temporal Filter already removes
		# out-of-band activity; these narrower bands isolate each reference
		# frequency and its harmonics for the CCA score.
		numberOfSamples = samplesByChannel.shape[0]
		nfft = max(4096, 4 * numberOfSamples)
		spectrum = numpy.fft.rfft(samplesByChannel, n=nfft, axis=0)
		frequencyAxis = numpy.fft.rfftfreq(nfft, 1.0 / samplingRate)
		mask = (frequencyAxis >= lowFrequency) & (frequencyAxis <= highFrequency)
		filtered = numpy.fft.irfft(spectrum * mask[:, None], n=nfft, axis=0)
		return filtered[:numberOfSamples, :]

	def ccaCorrelation(self, samplesByChannel, reference):
		# Regularized CCA keeps the calculation stable for one or a few EEG
		# channels and avoids requiring scipy inside the OpenViBE Python box.
		x = samplesByChannel - numpy.mean(samplesByChannel, axis=0, keepdims=True)
		y = reference - numpy.mean(reference, axis=0, keepdims=True)
		if x.shape[1] == 0 or y.shape[1] == 0:
			return 0.0
		denominator = max(x.shape[0] - 1, 1)
		covXX = numpy.dot(x.T, x) / denominator
		covYY = numpy.dot(y.T, y) / denominator
		covXY = numpy.dot(x.T, y) / denominator
		regX = max(float(numpy.trace(covXX)), 1.0) * 1e-6
		regY = max(float(numpy.trace(covYY)), 1.0) * 1e-6
		covXX += regX * numpy.eye(covXX.shape[0])
		covYY += regY * numpy.eye(covYY.shape[0])
		valuesX, vectorsX = numpy.linalg.eigh(covXX)
		valuesY, vectorsY = numpy.linalg.eigh(covYY)
		inverseSqrtX = numpy.dot(vectorsX * (1.0 / numpy.sqrt(numpy.maximum(valuesX, 1e-12))), vectorsX.T)
		inverseSqrtY = numpy.dot(vectorsY * (1.0 / numpy.sqrt(numpy.maximum(valuesY, 1e-12))), vectorsY.T)
		whitened = numpy.dot(numpy.dot(inverseSqrtX, covXY), inverseSqrtY)
		correlations = numpy.linalg.svd(whitened, compute_uv=False)
		return float(min(max(correlations[0], 0.0), 1.0)) if len(correlations) else 0.0

	def scoreAtFrequency(self, samplesByChannel, samplingRate, targetFrequency):
		numberOfSamples = samplesByChannel.shape[0]
		time = numpy.arange(numberOfSamples, dtype=float) / samplingRate
		score = 0.0
		# Fundamental dominates; harmonics support the decision but cannot
		# override it. This reduces errors from relationships such as 5->10 Hz.
		for harmonic, weight in ((1, 0.70), (2, 0.20), (3, 0.10)):
			frequency = targetFrequency * harmonic
			if frequency >= samplingRate / 2.0 - 0.5 or frequency > 22.0:
				continue
			reference = numpy.column_stack((
				numpy.sin(2.0 * numpy.pi * frequency * time),
				numpy.cos(2.0 * numpy.pi * frequency * time)))
			filtered = self.bandpass(samplesByChannel, samplingRate,
				max(0.5, frequency - 0.75), min(samplingRate / 2.0 - 0.5, frequency + 0.75))
			rho = self.ccaCorrelation(filtered, reference)
			score += weight * rho * rho
		return float(score)

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
				samplesByChannel = data.T
				samplingRate = float(self.signalHeader.samplingRate)
				if not self.signalInfoPrinted:
					print('SIGNAL_INFO: sampling_rate =', samplingRate,
						'channels =', data.shape[0], 'samples_per_epoch =', data.shape[1])
					self.signalInfoPrinted = True
				rawScores = [self.scoreAtFrequency(samplesByChannel, samplingRate, frequency) for frequency in self.frequencies]
				self.scoreHistory.append(rawScores)
				if len(self.scoreHistory) > self.smoothingWindows:
					self.scoreHistory.pop(0)
				scores = [float(numpy.median([history[index] for history in self.scoreHistory])) for index in range(10)]
				margin = self.scoreMargin(scores)
				bestScore = max(scores)
				if bestScore < self.minimumScore or margin < self.minimumMargin:
					candidate = -1
				else:
					candidate = int(numpy.argmax(scores))

				print('CCA_SCORE:', ' '.join('{:02d}={:.3f}'.format(digit, scores[digit]) for digit in range(10)),
					'candidate =', 'none' if candidate < 0 else candidate)

				if self.lastOutputState is None:
					self.lastOutputState = -1
					self.emitState(-1, chunk.startTime, scores)

				if candidate < 0:
					self.neutralCount += 1
					if self.neutralCount >= self.neutralUnlockWindows:
						if not self.armed:
							print('SSVEP_ARMED: ready for next digit')
						self.armed = True
				else:
					self.neutralCount = 0

				if candidate == self.candidateDigit:
					self.candidateCount += 1
				else:
					self.candidateDigit = candidate
					self.candidateCount = 1

				if candidate >= 0 and self.candidateCount >= self.confirmationWindows:
					if self.armed:
						self.emitState(candidate, chunk.startTime, scores)
						self.lastOutputState = candidate
						if self.sendDigitToUi(candidate):
							print('DIGIT_ACCEPTED:', candidate)
						else:
							print('DIGIT_ACCEPTED_WITHOUT_UI:', candidate)
						self.armed = False
					else:
						print('SSVEP_LOCKED: look at the center before the next digit')

			elif type(chunk) == OVSignalEnd:
				self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
