import json
import urllib.parse
import urllib.request
import numpy


class MyOVBox(OVBox):
	def __init__(self):
		OVBox.__init__(self)
		self.rowFrequencies = [8.5714, 10.0, 12.0, 15.0]
		self.columnFrequencies = [8.5714, 10.0, 12.0]
		self.frequencies = list(self.rowFrequencies)
		# The score is a weighted sum of squared CCA correlations. A floor of
		# Use a lower score floor together with a stricter winner margin and
		# consecutive confirmation, because real EEG CCA scores vary by person.
		self.minimumScore = 0.18
		self.minimumMargin = 0.30
		self.smoothingWindows = 3
		self.confirmationWindows = 4
		self.neutralUnlockWindows = 4
		self.uiServerUrl = 'http://127.0.0.1:8765'
		self.signalHeader = None
		self.scoreHistory = []
		self.candidateIndex = -1
		self.candidateCount = 0
		self.neutralCount = 0
		self.armed = True
		self.stage = 'row'
		self.selectedRow = None
		self.codeByStageIndex = {}
		self.signalInfoPrinted = False

	def parseFrequencies(self, settingName, expectedCount):
		values = [float(value.strip()) for value in str(self.setting[settingName]).split(',') if value.strip()]
		if len(values) != expectedCount:
			raise ValueError(settingName + ' must contain exactly ' + str(expectedCount) + ' values')
		return values

	def initialize(self):
		self.rowFrequencies = self.parseFrequencies('Row frequencies (Hz)', 4)
		self.columnFrequencies = self.parseFrequencies('Column frequencies (Hz)', 3)
		self.minimumScore = float(self.setting['Minimum CCA score'])
		self.minimumMargin = float(self.setting['Minimum score margin'])
		self.smoothingWindows = max(1, int(self.setting['Smoothing windows']))
		self.confirmationWindows = max(1, int(self.setting['Confirmation windows']))
		self.neutralUnlockWindows = max(1, int(self.setting['Neutral unlock windows']))
		self.uiServerUrl = str(self.setting['UI server URL']).rstrip('/')
		self.codeByStageIndex['row'] = [OpenViBE_stimulation['OVTK_StimulationId_Label_{:02d}'.format(index + 1)] for index in range(4)]
		self.codeByStageIndex['column'] = [OpenViBE_stimulation['OVTK_StimulationId_Label_{:02d}'.format(index + 5)] for index in range(3)]
		self.codeByStageIndex['digit'] = [OpenViBE_stimulation['OVTK_StimulationId_Label_{:02d}'.format(index + 8)] for index in range(10)]
		self.output[0].append(OVStimulationHeader(0., 0.))

	def resetDetection(self, stage, armed):
		self.stage = stage
		self.frequencies = list(self.rowFrequencies if stage == 'row' else self.columnFrequencies)
		self.scoreHistory = []
		self.candidateIndex = -1
		self.candidateCount = 0
		self.neutralCount = 0
		self.armed = armed

	def scoreMargin(self, scores):
		ordered = sorted(scores, reverse=True)
		return (ordered[0] - ordered[1]) / max(ordered[0], 1e-12)

	def sendUi(self, path, params):
		url = self.uiServerUrl + path + '?' + urllib.parse.urlencode(params)
		try:
			with urllib.request.urlopen(url, timeout=0.25) as response:
				json.loads(response.read().decode('utf-8'))
			return True
		except Exception as error:
			print('UI_EVENT_ERROR:', str(error))
			return False

	def emitSelection(self, stage, index, date, scores):
		code = self.codeByStageIndex[stage][index]
		stimSet = OVStimulationSet(date, date + 1.0 / max(self.getClock(), 1.0))
		stimSet.append(OVStimulation(code, date, 0.))
		self.output[0].append(stimSet)
		print(stage.upper() + '_ACCEPTED:', index,
			'score =', round(scores[index], 3),
			'margin =', round(self.scoreMargin(scores), 3))

	def emitDigit(self, digit, date, scores):
		code = self.codeByStageIndex['digit'][digit]
		stimSet = OVStimulationSet(date, date + 1.0 / max(self.getClock(), 1.0))
		stimSet.append(OVStimulation(code, date, 0.))
		self.output[0].append(stimSet)
		print('DIGIT_ACCEPTED:', digit,
			'score =', round(scores[digit], 3) if digit < len(scores) else 'n/a')

	def bandpass(self, samplesByChannel, samplingRate, lowFrequency, highFrequency):
		numberOfSamples = samplesByChannel.shape[0]
		nfft = max(4096, 4 * numberOfSamples)
		spectrum = numpy.fft.rfft(samplesByChannel, n=nfft, axis=0)
		frequencyAxis = numpy.fft.rfftfreq(nfft, 1.0 / samplingRate)
		mask = (frequencyAxis >= lowFrequency) & (frequencyAxis <= highFrequency)
		filtered = numpy.fft.irfft(spectrum * mask[:, None], n=nfft, axis=0)
		return filtered[:numberOfSamples, :]

	def ccaCorrelation(self, samplesByChannel, reference):
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
		"""Return a 0..1 spectral SNR/coherence score for one SSVEP target."""
		numberOfSamples = samplesByChannel.shape[0]
		time = numpy.arange(numberOfSamples, dtype=float) / samplingRate
		centered = samplesByChannel - numpy.mean(samplesByChannel, axis=0, keepdims=True)
		windowed = centered * numpy.hanning(numberOfSamples)[:, None]
		spectrum = numpy.fft.rfft(windowed, axis=0)
		power = numpy.mean(numpy.abs(spectrum) ** 2, axis=1)
		frequencyAxis = numpy.fft.rfftfreq(numberOfSamples, 1.0 / samplingRate)
		score = 0.0
		weightTotal = 0.0
		for harmonic, weight in ((1, 0.70), (2, 0.20), (3, 0.10)):
			frequency = targetFrequency * harmonic
			if frequency >= samplingRate / 2.0 - 0.5 or frequency > 40.0:
				continue
			frequencyIndex = int(numpy.argmin(numpy.abs(frequencyAxis - frequency)))
			targetLow = max(1, frequencyIndex - 1)
			targetHigh = min(len(power), frequencyIndex + 2)
			targetPower = float(numpy.mean(power[targetLow:targetHigh]))
			noiseMask = ((numpy.abs(frequencyAxis - frequency) >= 1.5) &
				(numpy.abs(frequencyAxis - frequency) <= 3.5))
			noisePower = float(numpy.median(power[noiseMask]))
			snrDb = 10.0 * numpy.log10((targetPower + 1e-12) / (noisePower + 1e-12))
			# Ignore weak peaks; saturate strong peaks so one harmonic cannot dominate.
			snrEvidence = float(numpy.clip((snrDb - 2.0) / 12.0, 0.0, 1.0))
			channelSpectrum = spectrum[frequencyIndex, :]
			phaseCoherence = float(numpy.abs(numpy.mean(
				channelSpectrum / (numpy.abs(channelSpectrum) + 1e-12))))
			score += weight * snrEvidence * phaseCoherence
			weightTotal += weight
		return float(score / max(weightTotal, 1e-12))

	def processAccepted(self, index, date, scores):
		if self.stage == 'row':
			self.selectedRow = index
			self.emitSelection('row', index, date, scores)
			self.sendUi('/row', {'index': str(index)})
			print('ROW_ACCEPTED: row', index + 1, 'look at the fixation point until column is ready')
			self.resetDetection('column', False)
			return

		column = index
		self.emitSelection('column', column, date, scores)
		if self.selectedRow == 3 and column != 1:
			self.sendUi('/invalid', {})
			print('INVALID_SELECTION: row 4 only has digit 0 in column 2')
			self.selectedRow = None
			self.resetDetection('row', False)
			return

		if self.selectedRow == 3:
			digit = 0
		else:
			digit = self.selectedRow * 3 + column + 1
		self.sendUi('/column', {'index': str(column)})
		self.emitDigit(digit, date, scores)
		self.sendUi('/digit', {'digit': str(digit)})
		self.selectedRow = None
		self.resetDetection('row', False)

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
				scores = [float(numpy.median([history[index] for history in self.scoreHistory])) for index in range(len(self.frequencies))]
				margin = self.scoreMargin(scores)
				bestScore = max(scores)
				if bestScore < self.minimumScore:
					candidate = -1
					gateReason = 'score<' + str(self.minimumScore)
				elif margin < self.minimumMargin:
					candidate = -1
					gateReason = 'margin<' + str(self.minimumMargin)
				else:
					candidate = int(numpy.argmax(scores))
					gateReason = 'pass'
				stageLabel = 'ROW' if self.stage == 'row' else 'COL'
				print('SSVEP_' + stageLabel + '_SCORE:', ' '.join('{:02d}={:.3f}'.format(index, scores[index]) for index in range(len(scores))),
					'candidate =', 'none' if candidate < 0 else candidate, 'gate =', gateReason)

				if candidate < 0:
					self.neutralCount += 1
					if self.neutralCount >= self.neutralUnlockWindows:
						if not self.armed:
							print('SSVEP_ARMED:', self.stage, 'ready')
							self.armed = True
							self.sendUi('/ready', {'stage': self.stage})
				else:
					self.neutralCount = 0

				if candidate == self.candidateIndex:
					self.candidateCount += 1
				else:
					self.candidateIndex = candidate
					self.candidateCount = 1

				if candidate >= 0 and self.candidateCount >= self.confirmationWindows:
					if self.armed:
						self.processAccepted(candidate, chunk.startTime, scores)
					else:
						print('SSVEP_LOCKED:', self.stage, 'look at the center before continuing')

			elif type(chunk) == OVSignalEnd:
				self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
