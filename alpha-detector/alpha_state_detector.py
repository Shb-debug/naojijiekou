import numpy


class MyOVBox(OVBox):
	def __init__(self):
		OVBox.__init__(self)
		self.closedThreshold = 0.5
		self.openThreshold = 0.3
		self.smoothingWindows = 3
		self.baselineWindows = 20
		self.confirmationWindows = 2
		self.minimumClosedWindows = 6
		self.recentValues = []
		self.baselineValues = []
		self.baseline = None
		self.state = None
		self.pendingState = None
		self.pendingCount = 0
		self.closedEvidenceCount = 0
		self.signalHeader = None
		self.presentCode = 0
		self.absentCode = 0

	def initialize(self):
		self.closedThreshold = float(self.setting['Closed threshold'])
		self.openThreshold = float(self.setting['Open threshold'])
		self.smoothingWindows = max(1, int(self.setting['Smoothing windows']))
		self.baselineWindows = max(1, int(self.setting['Baseline windows']))
		self.confirmationWindows = max(1, int(self.setting['Confirmation windows']))
		self.minimumClosedWindows = max(1, int(self.setting['Minimum closed windows']))
		if self.openThreshold >= self.closedThreshold:
			raise ValueError('Open threshold must be lower than Closed threshold')
		self.presentCode = OpenViBE_stimulation['OVTK_StimulationId_Label_01']
		self.absentCode = OpenViBE_stimulation['OVTK_StimulationId_Label_00']
		self.output[0].append(OVStimulationHeader(0., 0.))

	def emitState(self, state, date, metric, relative):
		code = self.presentCode if state else self.absentCode
		stimSet = OVStimulationSet(date, date + 1.0 / max(self.getClock(), 1.0))
		stimSet.append(OVStimulation(code, date, 0.))
		self.output[0].append(stimSet)
		baselineText = 'pending' if self.baseline is None else round(self.baseline, 6)
		print('EYE_STATE:', '闭眼' if state else '睁眼',
			'alpha =', round(metric, 6),
			'relative =', round(relative, 3),
			'baseline =', baselineText)

	def process(self):
		for chunkIdx in range(len(self.input[0])):
			chunk = self.input[0].pop()

			if type(chunk) == OVSignalHeader:
				self.signalHeader = chunk

			elif type(chunk) == OVSignalBuffer:
				if self.signalHeader is None:
					continue

				values = numpy.array(chunk).reshape(tuple(self.signalHeader.dimensionSizes))
				# Channel Selector is configured for channel 9. Mean is retained
				# so the script also works if several channels are selected later.
				value = float(numpy.mean(values))
				self.recentValues.append(value)
				if len(self.recentValues) > self.smoothingWindows:
					self.recentValues.pop(0)
				filteredValue = float(numpy.median(self.recentValues))

				# Start in the open-eye state. The first valid window emits
				# the initial state; decisions begin after the open-eye baseline.
				if self.state is None:
					self.state = False
					self.emitState(False, chunk.startTime, filteredValue, 0.0)

				if self.baseline is None:
					self.baselineValues.append(filteredValue)
					if len(self.baselineValues) < self.baselineWindows:
						continue
					self.baseline = max(float(numpy.median(self.baselineValues)), 1e-12)
					print('BASELINE_READY:', round(self.baseline, 6),
						'from', self.baselineWindows,
						'eyes-open windows')

				relative = (filteredValue - self.baseline) / self.baseline

				# Hysteresis plus duration gate. A closed state requires the
				# upper threshold for 6 consecutive 0.5-second windows (~3 sec),
				# so a blink or a single closure spike stays classified as open.
				if self.state is False:
					if relative >= self.closedThreshold:
						self.closedEvidenceCount += 1
					else:
						self.closedEvidenceCount = 0
					self.pendingState = None
					self.pendingCount = 0
					if self.closedEvidenceCount >= self.minimumClosedWindows:
						self.emitState(True, chunk.startTime, filteredValue, relative)
						self.state = True
						self.closedEvidenceCount = 0
				else:
					# When already closed, two low windows are enough to return
					# to open. This keeps the release responsive.
					if relative <= self.openThreshold:
						if self.pendingState is False:
							self.pendingCount += 1
						else:
							self.pendingState = False
							self.pendingCount = 1
						if self.pendingCount >= self.confirmationWindows:
							self.emitState(False, chunk.startTime, filteredValue, relative)
							self.state = False
							self.pendingState = None
							self.pendingCount = 0
					else:
						self.pendingState = None
						self.pendingCount = 0

			elif type(chunk) == OVSignalEnd:
				self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
