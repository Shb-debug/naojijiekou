"""OpenViBE Python 3 Box: Alpha readiness + low-latency blink events.

Input should contain the exact Channel Selector order:
Fp1/Fp2 (hardware channels 1/2), then O1/Oz/O2/Pz
(hardware channels 15/14/16/13). Each epoch is 0.45 s and arrives every 0.10 s.
"""
import json
import time
import urllib.parse
import urllib.request

import numpy


class MyOVBox(OVBox):
    def __init__(self):
        OVBox.__init__(self)
        self.header = None
        self.bridge = "http://127.0.0.1:8766"
        self.blinkWindow = 0.45
        self.alphaWindow = 0.90
        self.absoluteThreshold = 35.0
        self.zThreshold = 5.0
        self.releaseZ = 1.8
        self.refractory = 0.55
        self.warmupSeconds = 4.0
        self.calibrationWindowSeconds = 1.5
        self.minPeakRatio = 8.0
        self.confirmations = 2
        self.smoothingSeconds = 0.04
        self.band = (8.0, 13.0)
        self.history = []
        self.calibration = []
        self.latched = False
        self.lastBlink = -1e9
        self.lastHeartbeat = -1e9
        self.startedAt = None
        self.baseline = None
        self.baselineMad = 1.0
        self.confirmCount = 0
        self.releaseCount = 0

    def initialize(self):
        self.bridge = str(self.setting.get("Bridge URL", self.bridge)).rstrip("/")
        self.absoluteThreshold = float(self.setting.get("Blink absolute threshold", self.absoluteThreshold))
        self.zThreshold = float(self.setting.get("Blink z threshold", self.zThreshold))
        self.refractory = float(self.setting.get("Blink refractory seconds", self.refractory))
        self.warmupSeconds = float(self.setting.get("Blink warm-up seconds", self.warmupSeconds))
        self.calibrationWindowSeconds = float(self.setting.get("Blink calibration window seconds", self.calibrationWindowSeconds))
        self.minPeakRatio = float(self.setting.get("Blink minimum peak ratio", self.minPeakRatio))
        self.confirmations = int(self.setting.get("Blink confirmation windows", self.confirmations))
        self.output[0].append(OVStimulationHeader(0., 0.))

    def send(self, endpoint, values=None):
        values = values or {}
        url = self.bridge + endpoint + ("?" + urllib.parse.urlencode(values) if values else "")
        try:
            with urllib.request.urlopen(url, timeout=0.08) as response:
                response.read()
        except Exception as error:
            print("ALPHA_BRIDGE_ERROR:", str(error))

    def alphaRatio(self, data, sampleRate):
        if data.shape[0] < max(32, int(sampleRate * .45)):
            return 0.0
        centered = data - numpy.median(data, axis=0, keepdims=True)
        nfft = max(256, 1 << int(numpy.ceil(numpy.log2(centered.shape[0]))))
        power = numpy.abs(numpy.fft.rfft(centered * numpy.hanning(centered.shape[0])[:, None], n=nfft, axis=0)) ** 2
        frequencies = numpy.fft.rfftfreq(nfft, 1.0 / sampleRate)
        total = (frequencies >= 4.) & (frequencies <= 30.)
        alpha = (frequencies >= self.band[0]) & (frequencies <= self.band[1])
        return float(numpy.sum(power[alpha]) / max(numpy.sum(power[total]), 1e-12))

    def blinkFeatures(self, data, sampleRate):
        if data.ndim == 1:
            data = data[:, None]
        if data.shape[0] < 16 or data.shape[1] == 0:
            return 0.0, 0.0, 0.0, 0.0, 0.5
        centered = data - numpy.median(data, axis=0, keepdims=True)
        timeAxis = numpy.linspace(-1.0, 1.0, data.shape[0])
        design = numpy.column_stack((numpy.ones(data.shape[0]), timeAxis))
        detrended = centered - design @ numpy.linalg.lstsq(design, centered, rcond=None)[0]
        smoothSize = max(5, int(round(sampleRate * self.smoothingSeconds)))
        if smoothSize % 2 == 0:
            smoothSize += 1
        kernel = numpy.ones(smoothSize, dtype=float) / smoothSize
        smoothed = numpy.column_stack([numpy.convolve(detrended[:, i], kernel, mode="same") for i in range(detrended.shape[1])])
        smoothed -= numpy.median(smoothed, axis=0, keepdims=True)
        channelPeaks = numpy.max(numpy.abs(smoothed), axis=0)
        peak = float(numpy.median(channelPeaks))
        agreement = float(numpy.min(channelPeaks) / max(float(numpy.max(channelPeaks)), 1e-9))
        correlation = 0.0
        if smoothed.shape[1] >= 2 and numpy.std(smoothed[:, 0]) > 1e-9 and numpy.std(smoothed[:, 1]) > 1e-9:
            correlation = float(numpy.corrcoef(smoothed[:, 0], smoothed[:, 1])[0, 1])
        common = numpy.median(smoothed, axis=1)
        peakPosition = float(numpy.argmax(numpy.abs(common)) / max(len(common) - 1, 1))
        compactness = float(numpy.mean(numpy.abs(common) >= max(peak * 0.35, 1e-9)))
        return peak, agreement, correlation, compactness, peakPosition

    def blink(self, data, now, sampleRate):
        if self.startedAt is None:
            self.startedAt = now
        current, agreement, correlation, compactness, peakPosition = self.blinkFeatures(data, sampleRate)
        self.history.append(current)
        self.history = self.history[-45:]
        elapsed = now - self.startedAt
        if elapsed < self.warmupSeconds:
            self.calibration.append(current)
            self.calibration = self.calibration[-45:]
            return False, 0.0, current
        if self.baseline is None:
            calibrationCount = max(8, int(round(self.calibrationWindowSeconds / 0.1)))
            values = numpy.asarray(self.calibration[-calibrationCount:], dtype=float)
            if values.size < 8:
                return False, 0.0, current
            self.baseline = float(numpy.median(values))
            self.baselineMad = max(1.4826 * float(numpy.median(numpy.abs(values - self.baseline))), 1.0)
        z = (current - self.baseline) / self.baselineMad
        threshold = max(self.absoluteThreshold, self.baseline * self.minPeakRatio, self.baseline + self.zThreshold * self.baselineMad)
        confidence = max(0., min(1., (current - threshold) / max(threshold, 1.) + .55))
        detected = (current >= threshold and z >= self.zThreshold and agreement >= .35 and correlation >= .65 and .08 <= peakPosition <= .92 and .10 <= compactness <= .80)
        self.confirmCount = self.confirmCount + 1 if detected else 0
        if self.latched:
            if not detected:
                self.releaseCount += 1
            else:
                self.releaseCount = 0
            if self.releaseCount >= 2:
                self.latched = False
                self.releaseCount = 0
            self.confirmCount = 0
        if detected and self.confirmCount >= max(1, self.confirmations) and now - self.lastBlink >= self.refractory and not self.latched:
            self.latched = True; self.lastBlink = now; self.confirmCount = 0
            return True, confidence, current
        return False, confidence, current

    def process(self):
        for _ in range(len(self.input[0])):
            chunk = self.input[0].pop()
            if type(chunk) == OVSignalHeader:
                self.header = chunk
            elif type(chunk) == OVSignalBuffer and self.header is not None:
                values = numpy.array(chunk, dtype=float).reshape(tuple(self.header.dimensionSizes))
                if values.ndim == 1: values = values.reshape(1, -1)
                channels = values.T
                sampleRate = float(self.header.samplingRate)
                # OpenViBE Channel Selector order is Fp1, Fp2, O1, Oz, O2, Pz.
                # The selector uses hardware channels 1;2;15;14;16;13.
                if channels.shape[1] < 6:
                    print("CHANNEL_MAPPING_ERROR: expected 6 channels in order Fp1,Fp2,O1,Oz,O2,Pz")
                    continue
                frontal = channels[:, :2]
                posterior = channels[:, 2:6]
                now = time.monotonic()
                ratio = self.alphaRatio(posterior, sampleRate)
                event, confidence, score = self.blink(frontal, now, sampleRate)
                if now - self.lastHeartbeat >= .5:
                    self.send("/openvibe/alpha", {"alpha_ratio": round(ratio, 5)})
                    self.send("/openvibe/heartbeat")
                    self.lastHeartbeat = now
                if event:
                    stim = OVStimulationSet(chunk.startTime, chunk.startTime + 1. / max(self.getClock(), 1.))
                    stim.append(OVStimulation(OpenViBE_stimulation["OVTK_StimulationId_Label_01"], chunk.startTime, 0.))
                    self.output[0].append(stim)
                    self.send("/openvibe/blink", {"confidence": round(confidence, 4), "blink_score": round(score, 3), "alpha_ratio": round(ratio, 5)})
                    print("BLINK_JUMP:", round(confidence, 2), "alpha:", round(ratio, 3))
            elif type(chunk) == OVSignalEnd:
                self.output[0].append(OVStimulationEnd(chunk.endTime, chunk.endTime))


box = MyOVBox()
